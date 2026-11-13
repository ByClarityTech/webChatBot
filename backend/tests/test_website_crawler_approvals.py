"""Approving, rejecting and excluding crawled content.

The worker is a recorder: what the chatbot is told to index or remove is what these tests check.
The database is the in-memory FakeCrawlDb.
"""

import httpx
import pytest
from crawler_fakes import SITE_HOST, FakeCrawlDb, install, make_client

from app.db.models.crawler import (
    CHANGE_CHANGED,
    CHANGE_NEW,
    CHANGE_REMOVED,
    UPDATE_APPROVED,
    UPDATE_PENDING,
    UPDATE_REJECTED,
    UPDATE_SUPERSEDED,
)
from app.services.website_crawler import approvals
from app.services.website_crawler.approvals import (
    ChangeAlreadyDecided,
    ChangeNotFound,
    IndexingFailed,
    PageNotFound,
    approve_change,
    propose_removal_of_live_pages,
    reject_change,
    set_page_include,
)

MODULES = ["app.services.website_crawler.approvals"]
HASH_A = "a" * 64
HASH_B = "b" * 64
PAGE_URL = f"https://{SITE_HOST}/hours"


class FakeWorker:
    """Records what the approvals asked the worker to do, and answers with a status."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: list[tuple[str, str, dict]] = []

    async def __call__(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if self.status >= 400:
            return httpx.Response(self.status, json={"detail": "Embedding service unavailable"})
        return httpx.Response(200, json={"status": "ok"})


@pytest.fixture
def client():
    return make_client()


@pytest.fixture
def db(client, monkeypatch):
    fake = FakeCrawlDb(client.id)
    install(monkeypatch, fake, MODULES)
    return fake


@pytest.fixture
def worker(monkeypatch):
    recorder = FakeWorker()
    monkeypatch.setattr(approvals, "worker_request", recorder)
    return recorder


async def propose(db, client, *, change_type, live_text=None, live_hash=None, url=PAGE_URL):
    page = db.add_page(url, live_text=live_text, live_hash=live_hash, title="Hours")
    if change_type == CHANGE_REMOVED:
        update_id = await db.insert_pending_change(
            client_id=client.id, page_id=page.id, crawl_run_id=None, change_type=CHANGE_REMOVED,
            proposed_title=None, proposed_text=None, proposed_hash=None,
            words_added=0, words_removed=3,
        )  # fmt: skip
    else:
        update_id = await db.insert_pending_change(
            client_id=client.id, page_id=page.id, crawl_run_id=None, change_type=change_type,
            proposed_title="Hours", proposed_text="Open Sunday, 10 to 2.", proposed_hash=HASH_B,
            words_added=4, words_removed=0,
        )  # fmt: skip
    return page, update_id


# ---------------------------------------------------------------- approve


async def test_approving_a_new_page_indexes_that_version_and_makes_it_live(client, db, worker):
    page, update_id = await propose(db, client, change_type=CHANGE_NEW)

    change = await approve_change(client, update_id, actor_id=None)

    ((method, path, kwargs),) = worker.calls
    assert (method, path) == ("PUT", f"/pages/{page.id}")
    assert kwargs["json"]["version"] == HASH_B
    assert kwargs["json"]["text"] == "Open Sunday, 10 to 2."
    assert kwargs["json"]["url"] == PAGE_URL
    assert kwargs["json"]["client_id"] == str(client.id)
    assert change.status == UPDATE_APPROVED
    assert db.pages[page.id].live_content_hash == HASH_B
    assert db.pages[page.id].live_text == "Open Sunday, 10 to 2."


async def test_approving_a_changed_page_replaces_the_live_version(client, db, worker):
    page, update_id = await propose(
        db, client, change_type=CHANGE_CHANGED, live_text="Open Monday.", live_hash=HASH_A
    )

    await approve_change(client, update_id, actor_id=None)

    assert len(worker.calls) == 1
    assert db.pages[page.id].live_content_hash == HASH_B


async def test_approving_a_removal_takes_the_page_out_of_the_chatbot(client, db, worker):
    page, update_id = await propose(
        db, client, change_type=CHANGE_REMOVED, live_text="Hours Open Monday.", live_hash=HASH_A
    )

    change = await approve_change(client, update_id, actor_id=None)

    ((method, path, kwargs),) = worker.calls
    assert (method, path) == ("DELETE", f"/pages/{page.id}")
    assert kwargs["params"] == {"client_id": str(client.id)}
    assert db.pages[page.id].status == "missing"
    assert db.pages[page.id].live_content_hash is None
    assert change.status == UPDATE_APPROVED


async def test_a_failed_index_leaves_the_change_pending_and_the_chatbot_unchanged(
    client, db, monkeypatch
):
    page, update_id = await propose(
        db, client, change_type=CHANGE_CHANGED, live_text="Open Monday.", live_hash=HASH_A
    )
    monkeypatch.setattr(approvals, "worker_request", FakeWorker(status=500))

    with pytest.raises(IndexingFailed) as exc:
        await approve_change(client, update_id, actor_id=None)

    assert "Embedding service unavailable" in str(exc.value)
    assert db.pending[update_id].status == UPDATE_PENDING
    assert db.pages[page.id].live_content_hash == HASH_A


async def test_a_failed_removal_leaves_the_page_live(client, db, monkeypatch):
    page, update_id = await propose(
        db, client, change_type=CHANGE_REMOVED, live_text="Open Monday.", live_hash=HASH_A
    )
    monkeypatch.setattr(approvals, "worker_request", FakeWorker(status=503))

    with pytest.raises(IndexingFailed):
        await approve_change(client, update_id, actor_id=None)

    assert db.pending[update_id].status == UPDATE_PENDING
    assert db.pages[page.id].live_content_hash == HASH_A


async def test_approving_twice_is_refused_the_second_time(client, db, worker):
    _, update_id = await propose(db, client, change_type=CHANGE_NEW)
    await approve_change(client, update_id, actor_id=None)

    with pytest.raises(ChangeAlreadyDecided):
        await approve_change(client, update_id, actor_id=None)
    assert len(worker.calls) == 1


async def test_a_decision_that_lost_a_race_is_reported(client, db, worker, monkeypatch):
    _, update_id = await propose(db, client, change_type=CHANGE_NEW)

    async def lost(*_args, **_kwargs):
        return False

    monkeypatch.setattr(approvals, "decide_pending_change", lost)

    with pytest.raises(ChangeAlreadyDecided):
        await approve_change(client, update_id, actor_id=None)


async def test_an_admin_of_another_client_cannot_see_the_change(client, db, worker):
    _, update_id = await propose(db, client, change_type=CHANGE_NEW)
    other = make_client()

    with pytest.raises(ChangeNotFound):
        await approve_change(other, update_id, actor_id=None)
    assert worker.calls == []


async def test_the_approver_is_recorded(client, db, worker):
    import uuid

    actor = uuid.uuid4()
    _, update_id = await propose(db, client, change_type=CHANGE_NEW)

    change = await approve_change(client, update_id, actor_id=actor)

    assert db.pending[update_id].decided_by == actor
    assert change.status == UPDATE_APPROVED


# ---------------------------------------------------------------- reject


async def test_rejecting_remembers_the_version_and_touches_nothing_live(client, db, worker):
    page, update_id = await propose(
        db, client, change_type=CHANGE_CHANGED, live_text="Open Monday.", live_hash=HASH_A
    )

    change = await reject_change(client, update_id, actor_id=None)

    assert change.status == UPDATE_REJECTED
    assert db.pages[page.id].rejected_content_hash == HASH_B
    assert db.pages[page.id].live_content_hash == HASH_A
    assert worker.calls == []


async def test_rejecting_a_removal_keeps_the_page_live(client, db, worker):
    page, update_id = await propose(
        db, client, change_type=CHANGE_REMOVED, live_text="Open Monday.", live_hash=HASH_A
    )

    await reject_change(client, update_id, actor_id=None)

    assert db.pages[page.id].live_content_hash == HASH_A
    assert db.pages[page.id].rejected_content_hash is None
    assert worker.calls == []


async def test_rejecting_twice_is_refused(client, db, worker):
    _, update_id = await propose(db, client, change_type=CHANGE_NEW)
    await reject_change(client, update_id, actor_id=None)

    with pytest.raises(ChangeAlreadyDecided):
        await reject_change(client, update_id, actor_id=None)


async def test_approving_after_rejecting_is_refused(client, db, worker):
    _, update_id = await propose(db, client, change_type=CHANGE_NEW)
    await reject_change(client, update_id, actor_id=None)

    with pytest.raises(ChangeAlreadyDecided):
        await approve_change(client, update_id, actor_id=None)
    assert worker.calls == []


# ---------------------------------------------------------------- include / exclude


async def test_excluding_a_live_page_proposes_its_removal_once(client, db, worker):
    page = db.add_page(PAGE_URL, live_text="Open Monday.", live_hash=HASH_A)

    await set_page_include(client, page.id, included=False)
    await set_page_include(client, page.id, included=False)

    removals = [c for c in db.open_changes() if c.change_type == CHANGE_REMOVED]
    assert len(removals) == 1
    assert db.pages[page.id].included is False
    assert worker.calls == []  # nothing leaves the chatbot until approved


async def test_excluding_a_page_that_is_not_live_only_clears_its_open_change(client, db, worker):
    page = db.add_page(PAGE_URL)
    stale = await db.insert_pending_change(
        client_id=client.id, page_id=page.id, crawl_run_id=None, change_type=CHANGE_NEW,
        proposed_title="Hours", proposed_text="Open.", proposed_hash=HASH_B,
        words_added=1, words_removed=0,
    )  # fmt: skip

    await set_page_include(client, page.id, included=False)

    assert db.pending[stale].status == UPDATE_SUPERSEDED
    assert db.open_changes() == []


async def test_including_a_page_proposes_nothing(client, db, worker):
    page = db.add_page(PAGE_URL, live_text="Open.", live_hash=HASH_A, included=False)

    refreshed = await set_page_include(client, page.id, included=True)

    assert refreshed.included is True
    assert db.open_changes() == []


async def test_an_unknown_or_foreign_page_cannot_be_changed(client, db, worker):
    import uuid

    page = db.add_page(PAGE_URL)
    with pytest.raises(PageNotFound):
        await set_page_include(client, uuid.uuid4(), included=False)
    with pytest.raises(PageNotFound):
        await set_page_include(make_client(), page.id, included=False)


async def test_changing_the_website_proposes_removal_of_every_live_page(client, db, worker):
    live = db.add_page(PAGE_URL, live_text="Open.", live_hash=HASH_A)
    db.add_page(f"https://{SITE_HOST}/team", live_text="Dr Lee.", live_hash=HASH_B)
    db.add_page(f"https://{SITE_HOST}/draft")  # never live: nothing to remove

    assert await propose_removal_of_live_pages(client) == 2
    assert await propose_removal_of_live_pages(client) == 0
    assert {c.page_id for c in db.open_changes()} == {
        live.id,
        *(p.id for p in db.pages.values() if p.url == f"https://{SITE_HOST}/team"),
    }
    assert all(c.change_type == CHANGE_REMOVED for c in db.open_changes())
