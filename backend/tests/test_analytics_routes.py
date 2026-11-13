"""Analytics and conversation routes: role gates, the transcript audit, cross-client and test
conversation misses, phone lookup, and the CSV export. Database functions are replaced; the
routes and services are real."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from auth_helpers import make_client_record, signed_in
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, HumanMessage

from app.api.routes.admin import analytics as analytics_routes
from app.api.routes.admin import conversations as conversation_routes
from app.core.roles import UserType
from app.db.models import analytics as analytics_db
from app.services.contact_privacy import ContactStorageNotConfigured

ADMIN, SUPPORT, SUPER = UserType.admin, UserType.support, UserType.super_admin
NOW = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
PHONE_HASH = "b" * 64


class FakeGraph:
    def __init__(self, messages: list) -> None:
        self.messages = messages

    async def aget_state(self, config):
        return SimpleNamespace(values={"messages": self.messages})


@pytest.fixture
def app():
    test_app = FastAPI()
    test_app.include_router(analytics_routes.router, prefix="/api/v1")
    test_app.include_router(conversation_routes.router, prefix="/api/v1")
    test_app.state.agent_graph = FakeGraph(
        [HumanMessage(content="Do you take Delta?"), AIMessage(content="We accept most plans.")]
    )
    return test_app


@pytest.fixture
def db(monkeypatch):
    """The database reads the routes use, empty by default. Tests change the ones they need."""
    calls: dict[str, list] = {}

    def fake(name, result):
        async def call(*args, **kwargs):
            calls.setdefault(name, []).append((args, kwargs))
            return result

        monkeypatch.setattr(analytics_db, name, call)

    fake("outcome_rows", [])
    fake("count_in_progress", 0)
    fake("handover_counts", (0, 0, 0))
    fake("top_gaps", [])
    fake("list_conversations", ([], 0))
    fake("conversation_row", None)
    fake("gap_events_for_session", [])
    fake("staff_messages_for_thread", [])
    fake("contact_for_session", None)
    fake("patient_groups", ([], 0))
    fake("latest_contacts", {})
    monkeypatch.setattr(analytics_routes, "timezone_for", AsyncMock(return_value="UTC"))
    monkeypatch.setattr(conversation_routes, "timezone_for", AsyncMock(return_value="UTC"))
    return SimpleNamespace(calls=calls, fake=fake)


@pytest.fixture
def clinic(monkeypatch):
    """Scoping: any client id resolves to a record of that id."""

    async def get_client(client_id):
        return make_client_record(client_id=client_id)

    monkeypatch.setattr("app.services.client_scope.get_client", get_client)


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


SESSION = uuid4()


def _detail_row(client_id):
    return (
        SESSION, uuid4(), NOW, NOW, "ended", "anonymous", "resolved",
        "Answered, then the visitor left", True, 1, 1, 0, 90,
    )  # fmt: skip


# ---------------------------------------------------------------- roles


async def test_support_cannot_open_a_transcript(app, clinic, db):
    with signed_in(app, SUPPORT):
        async with _client(app) as client:
            response = await client.get(f"/api/v1/admin/conversations/{SESSION}")
    assert response.status_code == 403
    assert "conversation_row" not in db.calls


async def test_support_cannot_list_patients(app, clinic, db):
    with signed_in(app, SUPPORT):
        async with _client(app) as client:
            response = await client.get("/api/v1/admin/conversations/patients")
    assert response.status_code == 403


async def test_support_cannot_search_by_phone(app, clinic, db):
    with signed_in(app, SUPPORT):
        async with _client(app) as client:
            response = await client.get(
                "/api/v1/admin/conversations", params={"phone": "312-555-0187"}
            )
    assert response.status_code == 403
    assert "list_conversations" not in db.calls


async def test_support_can_read_the_report_and_the_outcome_list(app, clinic, db):
    with signed_in(app, SUPPORT):
        async with _client(app) as client:
            report = await client.get("/api/v1/admin/analytics", params={"preset": "7d"})
            outcomes = await client.get("/api/v1/admin/conversations")
    assert report.status_code == 200
    assert outcomes.status_code == 200


# ---------------------------------------------------------------- transcript and audit


async def test_an_admin_opens_a_transcript_and_the_open_is_audited(app, clinic, db, audit_rows):
    db.fake("conversation_row", _detail_row(None))
    with signed_in(app, ADMIN) as actor:
        async with _client(app) as client:
            response = await client.get(f"/api/v1/admin/conversations/{SESSION}")

    body = response.json()
    assert response.status_code == 200
    assert [line["role"] for line in body["transcript"]] == ["visitor", "assistant"]
    assert body["transcript"][1]["text"] == "We accept most plans."
    assert "audit log" in body["notice"]
    audited = [row for row in audit_rows if row["action"] == "conversation.view"]
    assert len(audited) == 1
    assert audited[0]["resource_id"] == str(SESSION)
    assert audited[0]["client_id"] == actor.client_id


async def test_a_transcript_is_not_returned_when_its_audit_row_cannot_be_written(
    app, clinic, db, monkeypatch
):
    db.fake("conversation_row", _detail_row(None))

    async def failing_insert(**_row):
        raise ConnectionError("audit table unavailable")

    monkeypatch.setattr("app.services.admin_audit.insert_admin_audit_log", failing_insert)
    with signed_in(app, ADMIN):
        async with _client(app) as client:
            response = await client.get(f"/api/v1/admin/conversations/{SESSION}")

    assert response.status_code == 503
    assert "transcript" not in response.text
    assert "conversation_row" not in db.calls  # nothing was read


async def test_a_conversation_of_another_client_is_not_found_and_the_attempt_is_audited(
    app, clinic, db, audit_rows
):
    with signed_in(app, ADMIN):
        async with _client(app) as client:
            response = await client.get(f"/api/v1/admin/conversations/{SESSION}")
    assert response.status_code == 404
    assert [row["action"] for row in audit_rows] == ["conversation.view"]


async def test_a_test_conversation_is_not_found(app, clinic, db):
    # The query excludes test conversations; a None row is what the route sees.
    with signed_in(app, ADMIN):
        async with _client(app) as client:
            response = await client.get(f"/api/v1/admin/conversations/{SESSION}")
    assert response.status_code == 404


async def test_a_super_admin_must_name_the_client_for_a_transcript(app, clinic, db):
    with signed_in(app, SUPER):
        async with _client(app) as client:
            response = await client.get(f"/api/v1/admin/conversations/{SESSION}")
    assert response.status_code == 400


async def test_a_conversation_that_cannot_be_read_is_503_not_a_partial_transcript(
    app, clinic, db, audit_rows
):
    db.fake("conversation_row", _detail_row(None))

    class BrokenGraph:
        async def aget_state(self, config):
            raise ConnectionError("checkpoint store down")

    app.state.agent_graph = BrokenGraph()
    with signed_in(app, ADMIN):
        async with _client(app) as client:
            response = await client.get(f"/api/v1/admin/conversations/{SESSION}")
    assert response.status_code == 503
    assert "transcript" not in response.text
    assert [row["action"] for row in audit_rows] == ["conversation.view"]


async def test_the_transcript_is_unavailable_until_the_store_is_ready(app, clinic, db):
    app.state.agent_graph = None
    with signed_in(app, ADMIN):
        async with _client(app) as client:
            response = await client.get(f"/api/v1/admin/conversations/{SESSION}")
    assert response.status_code == 503


# ---------------------------------------------------------------- phone lookup


async def test_a_phone_lookup_compares_the_hash_for_the_clients_conversations(
    app, clinic, db, monkeypatch
):
    monkeypatch.setattr(conversation_routes, "phone_hash", lambda e164: PHONE_HASH)
    with signed_in(app, ADMIN) as actor:
        async with _client(app) as client:
            response = await client.get(
                "/api/v1/admin/conversations", params={"phone": "(312) 555-0187"}
            )

    assert response.status_code == 200
    (args, kwargs) = db.calls["list_conversations"][0]
    assert args[0] == actor.client_id
    assert kwargs["phone_hash"] == PHONE_HASH


async def test_a_malformed_phone_is_refused(app, clinic, db):
    with signed_in(app, ADMIN):
        async with _client(app) as client:
            response = await client.get("/api/v1/admin/conversations", params={"phone": "555"})
    assert response.status_code == 400


async def test_a_phone_lookup_without_the_hash_key_is_503(app, clinic, db, monkeypatch):
    def not_configured(e164):
        raise ContactStorageNotConfigured("PHONE_HASH_KEY is not set")

    monkeypatch.setattr(conversation_routes, "phone_hash", not_configured)
    with signed_in(app, ADMIN):
        async with _client(app) as client:
            response = await client.get(
                "/api/v1/admin/conversations", params={"phone": "312-555-0187"}
            )
    assert response.status_code == 503


async def test_the_patient_view_is_for_admins_and_returns_masked_phones_only(
    app, clinic, db, monkeypatch
):
    monkeypatch.setattr(
        "app.services.analytics.reports.decrypt_phone", lambda token: "+13125550187"
    )
    db.fake("patient_groups", ([(PHONE_HASH, 2, NOW, NOW)], 1))
    db.fake("latest_contacts", {PHONE_HASH: ("Dana Ruiz", b"cipher", SESSION)})
    with signed_in(app, ADMIN):
        async with _client(app) as client:
            response = await client.get("/api/v1/admin/conversations/patients")

    group = response.json()["groups"][0]
    assert group["masked_phone"] == "(***) ***-0187"
    assert group["name"] == "Dana Ruiz"
    assert PHONE_HASH not in response.text
    assert "+1312" not in response.text


# ---------------------------------------------------------------- report and export


async def test_an_invalid_range_is_400(app, clinic, db):
    with signed_in(app, ADMIN):
        async with _client(app) as client:
            response = await client.get(
                "/api/v1/admin/analytics",
                params={"start": "2026-11-10", "end": "2026-11-01"},
            )
    assert response.status_code == 400


async def test_a_range_over_366_days_is_400(app, clinic, db):
    with signed_in(app, ADMIN):
        async with _client(app) as client:
            response = await client.get(
                "/api/v1/admin/analytics",
                params={"start": "2024-01-01", "end": "2026-11-10"},
            )
    assert response.status_code == 400


async def test_the_summary_export_is_csv_with_no_transcript_or_pii_and_is_audited(
    app, clinic, db, audit_rows
):
    db.fake("list_conversations", ([], 0))
    with signed_in(app, ADMIN) as actor:
        async with _client(app) as client:
            response = await client.get(
                "/api/v1/admin/analytics/export", params={"kind": "summary", "preset": "30d"}
            )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert "metric,value" in response.text
    assert "Dana" not in response.text and "+1312" not in response.text
    exports = [row for row in audit_rows if row["action"] == "analytics.export"]
    assert exports and exports[0]["client_id"] == actor.client_id


async def test_the_conversation_export_needs_a_client_for_a_super_admin(app, clinic, db):
    with signed_in(app, SUPER):
        async with _client(app) as client:
            response = await client.get(
                "/api/v1/admin/analytics/export",
                params={"kind": "conversations", "preset": "7d"},
            )
    assert response.status_code == 400
    assert "client" in response.json()["detail"].lower()


async def test_a_super_admin_report_covers_all_clients_without_a_client_filter(app, clinic, db):
    with signed_in(app, SUPER):
        async with _client(app) as client:
            response = await client.get("/api/v1/admin/analytics", params={"preset": "7d"})
    assert response.status_code == 200
    assert response.json()["client_id"] is None
    (args, _) = db.calls["outcome_rows"][0]
    assert args[0] is None
