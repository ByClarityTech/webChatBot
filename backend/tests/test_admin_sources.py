"""Admin Sources routes: who may use them, the website address, crawl start, page toggles,
and approve/reject with their status codes and audit rows."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from auth_helpers import make_client_record, signed_in
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin import sources
from app.core.roles import UserType
from app.db.models.crawler import (
    CHANGE_CHANGED,
    CHANGE_REMOVED,
    UPDATE_APPROVED,
    UPDATE_PENDING,
    UPDATE_REJECTED,
    CrawlAlreadyRunning,
    CrawledPageRecord,
    CrawlRunRecord,
    PendingUpdateRecord,
)
from app.services.website_crawler.approvals import (
    ChangeAlreadyDecided,
    ChangeNotFound,
    IndexingFailed,
    PageNotFound,
)
from app.services.website_crawler.crawl import CrawlNotConfigured

ADMIN, SUPPORT, SUPER = UserType.admin, UserType.support, UserType.super_admin
NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
SITE = "https://clinic.example"


@pytest.fixture
def app():
    test_app = FastAPI()
    test_app.include_router(sources.router, prefix="/api/v1")
    return test_app


@pytest.fixture
def site_client(monkeypatch):
    """Any client id resolves to a record with a website address set."""
    state = {"website_url": SITE}

    async def get_client(client_id):
        record = make_client_record(client_id=client_id)
        record.website_url = state["website_url"]
        return record

    monkeypatch.setattr("app.services.client_scope.get_client", get_client)
    return state


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _run(client_id, **fields) -> CrawlRunRecord:
    base = dict(
        id=uuid4(), client_id=client_id, trigger="manual", status="running", pages_fetched=0,
        pages_changed=0, pages_failed=0, error=None, requested_by=None, started_at=NOW,
        finished_at=None,
    )  # fmt: skip
    base.update(fields)
    return CrawlRunRecord(**base)


def _change(client_id, change_type=CHANGE_CHANGED, status=UPDATE_PENDING) -> PendingUpdateRecord:
    return PendingUpdateRecord(
        id=uuid4(), client_id=client_id, page_id=uuid4(), page_url=f"{SITE}/hours",
        crawl_run_id=None, change_type=change_type, status=status,
        proposed_title="Hours", proposed_text="Open Sunday.", proposed_hash="c" * 64,
        words_added=2, words_removed=1, live_title="Hours", live_text="Open Monday.",
        decided_by=None, decided_at=None, created_at=NOW, updated_at=NOW,
    )  # fmt: skip


# ---------------------------------------------------------------- access


async def test_support_cannot_use_the_sources_page(app, site_client):
    with signed_in(app, SUPPORT):
        async with _client(app) as client:
            response = await client.get("/api/v1/admin/sources/overview")
    assert response.status_code == 403


async def test_admin_reads_the_overview_of_their_own_client(app, site_client):
    with (
        signed_in(app, ADMIN),
        patch.object(sources, "get_running_crawl", AsyncMock(return_value=None)),
        patch.object(sources, "list_crawl_runs", AsyncMock(return_value=[])),
        patch.object(sources, "last_finished_crawl", AsyncMock(return_value=None)),
        patch.object(sources, "list_pending_changes", AsyncMock(return_value=[_change(uuid4())])),
    ):
        async with _client(app) as client:
            response = await client.get("/api/v1/admin/sources/overview")

    body = response.json()
    assert response.status_code == 200
    assert body["website_url"] == SITE
    assert body["pending_count"] == 1
    assert body["next_scheduled_at"] is None
    assert body["interval_days"] == 30
    assert body["max_pages"] == 30
    assert body["max_depth"] == 3


async def test_next_run_is_a_month_after_the_last_finished_crawl(app, site_client):
    finished = NOW - timedelta(days=5)
    with (
        signed_in(app, ADMIN) as actor,
        patch.object(sources, "get_running_crawl", AsyncMock(return_value=None)),
        patch.object(sources, "list_crawl_runs", AsyncMock(return_value=[])),
        patch.object(
            sources,
            "last_finished_crawl",
            AsyncMock(return_value=_run(actor.client_id, status="completed", finished_at=finished)),
        ),
        patch.object(sources, "list_pending_changes", AsyncMock(return_value=[])),
    ):
        async with _client(app) as client:
            response = await client.get("/api/v1/admin/sources/overview")

    assert datetime.fromisoformat(response.json()["next_scheduled_at"]) == finished + timedelta(
        days=30
    )


# ---------------------------------------------------------------- website address


async def test_a_bad_website_address_is_refused_and_nothing_is_saved(app, site_client):
    save = AsyncMock()
    with signed_in(app, ADMIN), patch.object(sources, "set_client_website_url", save):
        async with _client(app) as client:
            response = await client.put(
                "/api/v1/admin/sources/website-url", json={"website_url": "http://clinic.example"}
            )
    assert response.status_code == 400
    save.assert_not_awaited()


async def test_setting_the_website_is_saved_and_audited(app, site_client, audit_rows):
    site_client["website_url"] = None
    saved = make_client_record()
    saved.website_url = SITE
    save = AsyncMock(return_value=saved)
    removals = AsyncMock(return_value=0)
    with (
        signed_in(app, ADMIN) as actor,
        patch.object(sources, "set_client_website_url", save),
        patch.object(sources, "propose_removal_of_live_pages", removals),
        patch.object(sources, "get_running_crawl", AsyncMock(return_value=None)),
        patch.object(sources, "list_crawl_runs", AsyncMock(return_value=[])),
        patch.object(sources, "last_finished_crawl", AsyncMock(return_value=None)),
        patch.object(sources, "list_pending_changes", AsyncMock(return_value=[])),
    ):
        async with _client(app) as client:
            response = await client.put(
                "/api/v1/admin/sources/website-url", json={"website_url": "Clinic.example/"}
            )

    assert response.status_code == 200
    save.assert_awaited_once()
    assert save.await_args.args[1] == SITE
    removals.assert_not_awaited()  # first address: no earlier site to retire
    assert [(r["action"], r["client_id"]) for r in audit_rows] == [
        ("website.url_update", actor.client_id)
    ]


async def test_changing_to_another_site_proposes_removal_of_the_old_pages(app, site_client):
    site_client["website_url"] = "https://old.example"
    saved = make_client_record()
    saved.website_url = SITE
    removals = AsyncMock(return_value=2)
    with (
        signed_in(app, ADMIN),
        patch.object(sources, "set_client_website_url", AsyncMock(return_value=saved)),
        patch.object(sources, "propose_removal_of_live_pages", removals),
        patch.object(sources, "get_running_crawl", AsyncMock(return_value=None)),
        patch.object(sources, "list_crawl_runs", AsyncMock(return_value=[])),
        patch.object(sources, "last_finished_crawl", AsyncMock(return_value=None)),
        patch.object(sources, "list_pending_changes", AsyncMock(return_value=[])),
    ):
        async with _client(app) as client:
            response = await client.put(
                "/api/v1/admin/sources/website-url", json={"website_url": SITE}
            )

    assert response.status_code == 200
    removals.assert_awaited_once()


# ---------------------------------------------------------------- crawl now


async def test_crawl_now_starts_a_background_run_and_is_audited(app, site_client, audit_rows):
    with (
        signed_in(app, ADMIN) as actor,
        patch.object(sources, "begin_run", AsyncMock(return_value=_run(actor.client_id))) as begin,
        patch.object(sources, "launch_in_background", Mock()) as launch,
    ):
        async with _client(app) as client:
            response = await client.post("/api/v1/admin/sources/crawls")

    assert response.status_code == 202
    assert response.json()["status"] == "running"
    begin.assert_awaited_once()
    assert begin.await_args.kwargs["trigger"] == "manual"
    launch.assert_called_once()
    assert [r["action"] for r in audit_rows] == ["crawl.manual_start"]


async def test_crawl_now_while_a_crawl_runs_is_a_conflict(app, site_client, audit_rows):
    with (
        signed_in(app, ADMIN),
        patch.object(sources, "begin_run", AsyncMock(side_effect=CrawlAlreadyRunning())),
        patch.object(sources, "launch_in_background", Mock()) as launch,
    ):
        async with _client(app) as client:
            response = await client.post("/api/v1/admin/sources/crawls")

    assert response.status_code == 409
    launch.assert_not_called()
    assert audit_rows == []


async def test_crawl_now_without_a_website_is_refused(app, site_client):
    with (
        signed_in(app, ADMIN),
        patch.object(sources, "begin_run", AsyncMock(side_effect=CrawlNotConfigured())),
    ):
        async with _client(app) as client:
            response = await client.post("/api/v1/admin/sources/crawls")
    assert response.status_code == 400
    assert "website address" in response.json()["detail"]


# ---------------------------------------------------------------- pages


async def test_excluding_a_page_is_audited(app, site_client, audit_rows):
    page = CrawledPageRecord(
        id=uuid4(), client_id=uuid4(), url=f"{SITE}/hours", included=False, status="active",
        title="Hours", live_content_hash=None, live_text=None, rejected_content_hash=None,
        last_status_code=200, last_error=None, last_crawled_at=NOW, created_at=NOW, updated_at=NOW,
    )  # fmt: skip
    with (
        signed_in(app, ADMIN) as actor,
        patch.object(sources, "set_page_include", AsyncMock(return_value=page)),
    ):
        async with _client(app) as client:
            response = await client.patch(
                f"/api/v1/admin/sources/pages/{page.id}", json={"included": False}
            )

    assert response.status_code == 200
    assert response.json()["included"] is False
    assert [(r["action"], r["client_id"]) for r in audit_rows] == [
        ("page.exclude", actor.client_id)
    ]


async def test_a_page_of_another_client_is_not_found(app, site_client):
    with (
        signed_in(app, ADMIN),
        patch.object(sources, "set_page_include", AsyncMock(side_effect=PageNotFound())),
    ):
        async with _client(app) as client:
            response = await client.patch(
                f"/api/v1/admin/sources/pages/{uuid4()}", json={"included": True}
            )
    assert response.status_code == 404


# ---------------------------------------------------------------- changes


async def test_the_change_detail_has_a_word_diff(app, site_client):
    with (
        signed_in(app, ADMIN) as actor,
        patch.object(
            sources, "get_pending_change", AsyncMock(return_value=_change(actor.client_id))
        ),
    ):
        async with _client(app) as client:
            response = await client.get(f"/api/v1/admin/sources/changes/{uuid4()}")

    body = response.json()
    assert response.status_code == 200
    ops = [s["op"] for s in body["segments"]]
    assert "delete" in ops and "insert" in ops
    assert body["live_title"] == "Hours"


async def test_approve_is_audited(app, site_client, audit_rows):
    with (
        signed_in(app, ADMIN) as actor,
        patch.object(
            sources,
            "approve_change",
            AsyncMock(return_value=_change(actor.client_id, status=UPDATE_APPROVED)),
        ),
    ):
        async with _client(app) as client:
            response = await client.post(f"/api/v1/admin/sources/changes/{uuid4()}/approve")

    assert response.status_code == 200
    assert response.json()["status"] == UPDATE_APPROVED
    assert [r["action"] for r in audit_rows] == ["change.approve"]


async def test_approve_with_the_index_down_is_502_and_not_audited(app, site_client, audit_rows):
    with (
        signed_in(app, ADMIN),
        patch.object(
            sources,
            "approve_change",
            AsyncMock(side_effect=IndexingFailed("The chatbot could not be updated. Try again.")),
        ),
    ):
        async with _client(app) as client:
            response = await client.post(f"/api/v1/admin/sources/changes/{uuid4()}/approve")

    assert response.status_code == 502
    assert response.json()["detail"] == "The chatbot could not be updated. Try again."
    assert audit_rows == []


async def test_approving_a_decided_change_is_a_conflict(app, site_client):
    with (
        signed_in(app, ADMIN),
        patch.object(sources, "approve_change", AsyncMock(side_effect=ChangeAlreadyDecided())),
    ):
        async with _client(app) as client:
            response = await client.post(f"/api/v1/admin/sources/changes/{uuid4()}/approve")
    assert response.status_code == 409


async def test_reject_is_audited(app, site_client, audit_rows):
    with (
        signed_in(app, ADMIN) as actor,
        patch.object(
            sources,
            "reject_change",
            AsyncMock(return_value=_change(actor.client_id, CHANGE_REMOVED, UPDATE_REJECTED)),
        ),
    ):
        async with _client(app) as client:
            response = await client.post(f"/api/v1/admin/sources/changes/{uuid4()}/reject")

    assert response.status_code == 200
    assert response.json()["status"] == UPDATE_REJECTED
    assert [r["action"] for r in audit_rows] == ["change.reject"]


async def test_a_change_of_another_client_is_not_found(app, site_client):
    with (
        signed_in(app, ADMIN),
        patch.object(sources, "reject_change", AsyncMock(side_effect=ChangeNotFound())),
    ):
        async with _client(app) as client:
            response = await client.post(f"/api/v1/admin/sources/changes/{uuid4()}/reject")
    assert response.status_code == 404
