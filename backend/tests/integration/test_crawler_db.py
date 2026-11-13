"""Website crawler tables against a real database (migration 0018).

These check what only PostgreSQL can enforce: one running crawl per client, stale runs that
stop blocking, one open change per page, and the advisory lock that picks the scheduler leader.
Skipped unless TEST_DATABASE_URL is set.
"""

from datetime import timedelta

import psycopg
import pytest

from app.db.models.client import set_client_website_url
from app.db.models.crawler import (
    CHANGE_CHANGED,
    CHANGE_NEW,
    RUN_COMPLETED,
    TRIGGER_MANUAL,
    UPDATE_APPROVED,
    UPDATE_PENDING,
    UPDATE_SUPERSEDED,
    CrawlAlreadyRunning,
    clients_due_for_crawl,
    create_crawl_run,
    decide_pending_change,
    ensure_crawled_page,
    finish_crawl_run,
    get_pending_change,
    insert_pending_change,
    list_pending_changes,
    open_change_for_page,
)
from app.services.website_crawler.scheduler import try_become_leader

pytestmark = pytest.mark.integration

HASH_A = "a" * 64
HASH_B = "b" * 64


@pytest.fixture
def clinic(db):
    row = db.execute(
        "INSERT INTO clients (name, slug, website_url) VALUES ('Clinic', 'clinic', 'https://clinic.example')"
        " RETURNING id"
    ).fetchone()
    return row[0]


async def test_a_second_crawl_for_the_same_client_is_refused_until_the_first_finishes(clinic, db):
    first = await create_crawl_run(clinic, trigger=TRIGGER_MANUAL, requested_by=None)

    with pytest.raises(CrawlAlreadyRunning):
        await create_crawl_run(clinic, trigger=TRIGGER_MANUAL, requested_by=None)

    await finish_crawl_run(
        first.id, status=RUN_COMPLETED, pages_fetched=1, pages_changed=0, pages_failed=0, error=None
    )
    second = await create_crawl_run(clinic, trigger=TRIGGER_MANUAL, requested_by=None)
    assert second.id != first.id


async def test_a_run_left_open_by_a_stopped_process_does_not_block_forever(clinic, db):
    db.execute(
        "INSERT INTO crawl_runs (client_id, trigger, status, started_at)"
        " VALUES (%s, 'manual', 'running', NOW() - interval '3 hours')",
        (clinic,),
    )

    run = await create_crawl_run(clinic, trigger=TRIGGER_MANUAL, requested_by=None)

    assert run.status == "running"
    closed = db.execute(
        "SELECT status, error FROM crawl_runs WHERE client_id = %s AND status = 'failed'",
        (clinic,),
    ).fetchone()
    assert closed == ("failed", "Interrupted before it finished")


async def test_only_one_open_change_per_page_and_a_new_one_supersedes_the_old(clinic, db):
    page = await ensure_crawled_page(clinic, "https://clinic.example/hours")
    first = await insert_pending_change(
        client_id=clinic, page_id=page.id, crawl_run_id=None, change_type=CHANGE_NEW,
        proposed_title="Hours", proposed_text="Open.", proposed_hash=HASH_A,
        words_added=1, words_removed=0,
    )  # fmt: skip
    second = await insert_pending_change(
        client_id=clinic, page_id=page.id, crawl_run_id=None, change_type=CHANGE_CHANGED,
        proposed_title="Hours", proposed_text="Open late.", proposed_hash=HASH_B,
        words_added=2, words_removed=0,
    )  # fmt: skip

    statuses = dict(db.execute(
        "SELECT id, status FROM pending_knowledge_updates WHERE page_id = %s", (page.id,)
    ).fetchall())  # fmt: skip
    assert statuses[first] == UPDATE_SUPERSEDED
    assert statuses[second] == UPDATE_PENDING
    current = await open_change_for_page(page.id)
    assert current.id == second


async def test_a_change_can_be_decided_only_once(clinic, db):
    page = await ensure_crawled_page(clinic, "https://clinic.example/hours")
    update = await insert_pending_change(
        client_id=clinic, page_id=page.id, crawl_run_id=None, change_type=CHANGE_NEW,
        proposed_title="Hours", proposed_text="Open.", proposed_hash=HASH_A,
        words_added=1, words_removed=0,
    )  # fmt: skip

    assert await decide_pending_change(update, status=UPDATE_APPROVED, decided_by=None) is True
    assert await decide_pending_change(update, status="rejected", decided_by=None) is False
    assert (await get_pending_change(update, client_id=clinic)).status == UPDATE_APPROVED


async def test_another_clients_change_is_not_visible(clinic, db):
    other = db.execute(
        "INSERT INTO clients (name, slug) VALUES ('Other', 'other') RETURNING id"
    ).fetchone()[0]
    page = await ensure_crawled_page(clinic, "https://clinic.example/hours")
    update = await insert_pending_change(
        client_id=clinic, page_id=page.id, crawl_run_id=None, change_type=CHANGE_NEW,
        proposed_title="Hours", proposed_text="Open.", proposed_hash=HASH_A,
        words_added=1, words_removed=0,
    )  # fmt: skip

    assert await get_pending_change(update, client_id=other) is None
    assert await list_pending_changes(other) == []


async def test_a_client_is_due_only_when_it_has_a_website_and_no_recent_crawl(clinic, db):
    assert clinic in await clients_due_for_crawl(interval_days=30)

    run = await create_crawl_run(clinic, trigger=TRIGGER_MANUAL, requested_by=None)
    await finish_crawl_run(
        run.id, status=RUN_COMPLETED, pages_fetched=1, pages_changed=0, pages_failed=0, error=None
    )
    assert clinic not in await clients_due_for_crawl(interval_days=30)

    db.execute(
        "UPDATE crawl_runs SET finished_at = NOW() - %s WHERE id = %s",
        (timedelta(days=31), run.id),
    )
    assert clinic in await clients_due_for_crawl(interval_days=30)


async def test_a_client_without_a_website_is_never_due(clinic, db):
    await set_client_website_url(clinic, None)
    assert clinic not in await clients_due_for_crawl(interval_days=30)


async def test_only_one_scheduler_holds_the_lock_at_a_time(database_url):
    leader_conn = await psycopg.AsyncConnection.connect(database_url, autocommit=True)
    other_conn = await psycopg.AsyncConnection.connect(database_url, autocommit=True)
    try:
        assert await try_become_leader(leader_conn) is True
        assert await try_become_leader(other_conn) is False
        await leader_conn.close()
        assert await try_become_leader(other_conn) is True
    finally:
        await other_conn.close()
        if not leader_conn.closed:
            await leader_conn.close()
