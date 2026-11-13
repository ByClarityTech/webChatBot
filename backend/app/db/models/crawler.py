"""Crawl runs, crawled pages and pending knowledge updates (migration 0018).

Every query is scoped by client_id. A page or change of another client reads as missing.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg

from app.db.session import get_db_connection

TRIGGER_SCHEDULED = "scheduled"
TRIGGER_MANUAL = "manual"

RUN_RUNNING = "running"
RUN_COMPLETED = "completed"
RUN_FAILED = "failed"

# A run still 'running' after this long was cut off (the process stopped). It no longer blocks.
STALE_RUN_MINUTES = 60

PAGE_ACTIVE = "active"
PAGE_MISSING = "missing"

CHANGE_NEW = "new"
CHANGE_CHANGED = "changed"
CHANGE_REMOVED = "removed"

UPDATE_PENDING = "pending"
UPDATE_APPROVED = "approved"
UPDATE_REJECTED = "rejected"
UPDATE_SUPERSEDED = "superseded"


class CrawlAlreadyRunning(Exception):
    """The client already has a crawl in progress."""


@dataclass
class CrawlRunRecord:
    id: UUID
    client_id: UUID
    trigger: str
    status: str
    pages_fetched: int
    pages_changed: int
    pages_failed: int
    error: str | None
    requested_by: UUID | None
    started_at: datetime
    finished_at: datetime | None


@dataclass
class CrawledPageRecord:
    id: UUID
    client_id: UUID
    url: str
    included: bool
    status: str
    title: str | None
    live_content_hash: str | None
    live_text: str | None
    rejected_content_hash: str | None
    last_status_code: int | None
    last_error: str | None
    last_crawled_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass
class PendingUpdateRecord:
    id: UUID
    client_id: UUID
    page_id: UUID
    page_url: str
    crawl_run_id: UUID | None
    change_type: str
    status: str
    proposed_title: str | None
    proposed_text: str | None
    proposed_hash: str | None
    words_added: int
    words_removed: int
    live_title: str | None
    live_text: str | None
    decided_by: UUID | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


_RUN_COLUMNS = """
    id, client_id, trigger, status, pages_fetched, pages_changed, pages_failed, error,
    requested_by, started_at, finished_at
"""

_PAGE_COLUMNS = """
    id, client_id, url, included, status, title, live_content_hash, live_text,
    rejected_content_hash, last_status_code, last_error, last_crawled_at, created_at, updated_at
"""

_PENDING_SELECT = """
    p.id, p.client_id, p.page_id, pg.url, p.crawl_run_id, p.change_type, p.status,
    p.proposed_title, p.proposed_text, p.proposed_hash, p.words_added, p.words_removed,
    pg.title, pg.live_text, p.decided_by, p.decided_at, p.created_at, p.updated_at
"""


def _run(row: tuple) -> CrawlRunRecord:
    return CrawlRunRecord(
        id=row[0],
        client_id=row[1],
        trigger=row[2],
        status=row[3],
        pages_fetched=int(row[4]),
        pages_changed=int(row[5]),
        pages_failed=int(row[6]),
        error=row[7],
        requested_by=row[8],
        started_at=row[9],
        finished_at=row[10],
    )


def _page(row: tuple) -> CrawledPageRecord:
    return CrawledPageRecord(
        id=row[0],
        client_id=row[1],
        url=row[2],
        included=bool(row[3]),
        status=row[4],
        title=row[5],
        live_content_hash=row[6].strip() if row[6] else None,
        live_text=row[7],
        rejected_content_hash=row[8].strip() if row[8] else None,
        last_status_code=row[9],
        last_error=row[10],
        last_crawled_at=row[11],
        created_at=row[12],
        updated_at=row[13],
    )


def _pending(row: tuple) -> PendingUpdateRecord:
    return PendingUpdateRecord(
        id=row[0],
        client_id=row[1],
        page_id=row[2],
        page_url=row[3],
        crawl_run_id=row[4],
        change_type=row[5],
        status=row[6],
        proposed_title=row[7],
        proposed_text=row[8],
        proposed_hash=row[9].strip() if row[9] else None,
        words_added=int(row[10]),
        words_removed=int(row[11]),
        live_title=row[12],
        live_text=row[13],
        decided_by=row[14],
        decided_at=row[15],
        created_at=row[16],
        updated_at=row[17],
    )


# ---------------------------------------------------------------- crawl runs


async def create_crawl_run(
    client_id: UUID, *, trigger: str, requested_by: UUID | None
) -> CrawlRunRecord:
    """A new running crawl. Raises CrawlAlreadyRunning if one is in progress.

    The partial unique index makes the check hold even with several instances. Runs left
    'running' by a stopped process are closed first, so they cannot block the client forever.
    """
    async with get_db_connection() as conn:
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE crawl_runs
                    SET status = 'failed', error = 'Interrupted before it finished',
                        finished_at = NOW()
                    WHERE client_id = %s AND status = 'running'
                      AND started_at < NOW() - make_interval(mins => %s)
                    """,
                    (client_id, STALE_RUN_MINUTES),
                )
                await cur.execute(
                    f"""
                    INSERT INTO crawl_runs (client_id, trigger, requested_by)
                    VALUES (%s, %s, %s)
                    RETURNING {_RUN_COLUMNS}
                    """,
                    (client_id, trigger, requested_by),
                )
                row = await cur.fetchone()
            await conn.commit()
        except psycopg.errors.UniqueViolation as exc:
            await conn.rollback()
            raise CrawlAlreadyRunning() from exc
    return _run(row)


async def finish_crawl_run(
    run_id: UUID,
    *,
    status: str,
    pages_fetched: int,
    pages_changed: int,
    pages_failed: int,
    error: str | None,
) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE crawl_runs
                SET status = %s, pages_fetched = %s, pages_changed = %s, pages_failed = %s,
                    error = %s, finished_at = NOW()
                WHERE id = %s
                """,
                (status, pages_fetched, pages_changed, pages_failed, error, run_id),
            )
        await conn.commit()


async def get_running_crawl(client_id: UUID) -> CrawlRunRecord | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {_RUN_COLUMNS} FROM crawl_runs"
                " WHERE client_id = %s AND status = 'running' LIMIT 1",
                (client_id,),
            )
            row = await cur.fetchone()
    return _run(row) if row else None


async def list_crawl_runs(client_id: UUID, *, limit: int = 10) -> list[CrawlRunRecord]:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT {_RUN_COLUMNS} FROM crawl_runs
                WHERE client_id = %s ORDER BY started_at DESC LIMIT %s
                """,
                (client_id, max(1, min(limit, 50))),
            )
            rows = await cur.fetchall()
    return [_run(row) for row in rows]


async def last_finished_crawl(client_id: UUID) -> CrawlRunRecord | None:
    """The most recent crawl that completed. Its finish time sets the next monthly run."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT {_RUN_COLUMNS} FROM crawl_runs
                WHERE client_id = %s AND status = 'completed'
                ORDER BY finished_at DESC LIMIT 1
                """,
                (client_id,),
            )
            row = await cur.fetchone()
    return _run(row) if row else None


async def clients_due_for_crawl(*, interval_days: int) -> list[UUID]:
    """Active clients with a website whose last completed crawl is older than the interval, or
    who have never been crawled, and who have no crawl running now."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT c.id FROM clients c
                WHERE c.website_url IS NOT NULL
                  AND c.status = 'active'
                  AND NOT EXISTS (
                      SELECT 1 FROM crawl_runs r
                      WHERE r.client_id = c.id AND r.status = 'running'
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM crawl_runs r
                      WHERE r.client_id = c.id AND r.status = 'completed'
                        AND r.finished_at > NOW() - make_interval(days => %s)
                  )
                ORDER BY c.name
                """,
                (interval_days,),
            )
            rows = await cur.fetchall()
    return [row[0] for row in rows]


# ---------------------------------------------------------------- pages


async def list_crawled_pages(client_id: UUID) -> list[CrawledPageRecord]:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {_PAGE_COLUMNS} FROM crawled_pages WHERE client_id = %s ORDER BY url",
                (client_id,),
            )
            rows = await cur.fetchall()
    return [_page(row) for row in rows]


async def get_crawled_page(page_id: UUID, *, client_id: UUID) -> CrawledPageRecord | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {_PAGE_COLUMNS} FROM crawled_pages WHERE id = %s AND client_id = %s",
                (page_id, client_id),
            )
            row = await cur.fetchone()
    return _page(row) if row else None


async def get_crawled_page_by_url(client_id: UUID, url: str) -> CrawledPageRecord | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {_PAGE_COLUMNS} FROM crawled_pages WHERE client_id = %s AND url = %s",
                (client_id, url),
            )
            row = await cur.fetchone()
    return _page(row) if row else None


async def ensure_crawled_page(client_id: UUID, url: str) -> CrawledPageRecord:
    """The page row for this URL, created as included and active if it is new."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                INSERT INTO crawled_pages (client_id, url) VALUES (%s, %s)
                ON CONFLICT (client_id, url) DO UPDATE SET url = EXCLUDED.url
                RETURNING {_PAGE_COLUMNS}
                """,
                (client_id, url),
            )
            row = await cur.fetchone()
        await conn.commit()
    return _page(row)


async def record_page_fetch(
    page_id: UUID,
    *,
    status_code: int | None,
    error: str | None,
    title: str | None = None,
) -> None:
    """What the last fetch of a page returned. The live version is not touched here."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE crawled_pages
                SET last_status_code = %s, last_error = %s, last_crawled_at = NOW(),
                    title = COALESCE(%s, title), updated_at = NOW()
                WHERE id = %s
                """,
                (status_code, error, title, page_id),
            )
        await conn.commit()


async def set_page_included(page_id: UUID, *, client_id: UUID, included: bool) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE crawled_pages SET included = %s, updated_at = NOW()"
                " WHERE id = %s AND client_id = %s",
                (included, page_id, client_id),
            )
        await conn.commit()


async def set_page_live(page_id: UUID, *, title: str | None, content_hash: str, text: str) -> None:
    """The indexed version is now this one. Clears the rejection memory."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE crawled_pages
                SET status = 'active', title = %s, live_content_hash = %s, live_text = %s,
                    rejected_content_hash = NULL, updated_at = NOW()
                WHERE id = %s
                """,
                (title, content_hash, text, page_id),
            )
        await conn.commit()


async def set_page_missing(page_id: UUID) -> None:
    """No longer indexed. The live version is cleared with the chunks."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE crawled_pages
                SET status = 'missing', live_content_hash = NULL, live_text = NULL,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (page_id,),
            )
        await conn.commit()


async def set_page_rejected_hash(page_id: UUID, content_hash: str) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE crawled_pages SET rejected_content_hash = %s, updated_at = NOW()"
                " WHERE id = %s",
                (content_hash, page_id),
            )
        await conn.commit()


# ---------------------------------------------------------------- pending changes


async def open_change_for_page(page_id: UUID) -> PendingUpdateRecord | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT {_PENDING_SELECT} FROM pending_knowledge_updates p
                JOIN crawled_pages pg ON pg.id = p.page_id
                WHERE p.page_id = %s AND p.status = 'pending'
                """,
                (page_id,),
            )
            row = await cur.fetchone()
    return _pending(row) if row else None


async def insert_pending_change(
    *,
    client_id: UUID,
    page_id: UUID,
    crawl_run_id: UUID | None,
    change_type: str,
    proposed_title: str | None,
    proposed_text: str | None,
    proposed_hash: str | None,
    words_added: int,
    words_removed: int,
) -> UUID:
    """A new open change for the page. Any open change it replaces is marked superseded."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE pending_knowledge_updates
                SET status = 'superseded', updated_at = NOW()
                WHERE page_id = %s AND status = 'pending'
                """,
                (page_id,),
            )
            await cur.execute(
                """
                INSERT INTO pending_knowledge_updates
                    (client_id, page_id, crawl_run_id, change_type, proposed_title,
                     proposed_text, proposed_hash, words_added, words_removed)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    client_id,
                    page_id,
                    crawl_run_id,
                    change_type,
                    proposed_title,
                    proposed_text,
                    proposed_hash,
                    words_added,
                    words_removed,
                ),
            )
            row = await cur.fetchone()
        await conn.commit()
    return row[0]


async def supersede_open_change(page_id: UUID) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE pending_knowledge_updates
                SET status = 'superseded', updated_at = NOW()
                WHERE page_id = %s AND status = 'pending'
                """,
                (page_id,),
            )
        await conn.commit()


async def list_pending_changes(
    client_id: UUID, *, status: str = UPDATE_PENDING
) -> list[PendingUpdateRecord]:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT {_PENDING_SELECT} FROM pending_knowledge_updates p
                JOIN crawled_pages pg ON pg.id = p.page_id
                WHERE p.client_id = %s AND p.status = %s
                ORDER BY p.created_at DESC
                """,
                (client_id, status),
            )
            rows = await cur.fetchall()
    return [_pending(row) for row in rows]


async def get_pending_change(update_id: UUID, *, client_id: UUID) -> PendingUpdateRecord | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT {_PENDING_SELECT} FROM pending_knowledge_updates p
                JOIN crawled_pages pg ON pg.id = p.page_id
                WHERE p.id = %s AND p.client_id = %s
                """,
                (update_id, client_id),
            )
            row = await cur.fetchone()
    return _pending(row) if row else None


async def decide_pending_change(update_id: UUID, *, status: str, decided_by: UUID | None) -> bool:
    """Marks an open change approved or rejected. False if it was already decided."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE pending_knowledge_updates
                SET status = %s, decided_by = %s, decided_at = NOW(), updated_at = NOW()
                WHERE id = %s AND status = 'pending'
                """,
                (status, decided_by, update_id),
            )
            changed = cur.rowcount > 0
        await conn.commit()
    return changed
