"""Monthly re-crawl of every client with a website.

Several API instances may run this loop. Only the one that holds the PostgreSQL advisory lock
starts scheduled crawls. The lock belongs to a session, so if that instance stops or loses its
connection, the lock goes with it and another instance takes over on its next check.
The crawl_runs unique index is a second guard: a client never has two running crawls.
"""

import asyncio
import logging

import psycopg

from app.core.config import get_settings
from app.db.models.client import get_client
from app.db.models.crawler import TRIGGER_SCHEDULED, CrawlAlreadyRunning, clients_due_for_crawl
from app.services.website_crawler.crawl import (
    INTERVAL_DAYS,
    CrawlNotConfigured,
    Sleep,
    begin_run,
    execute_crawl,
)
from app.services.website_crawler.fetcher import Resolver
from app.services.widget_verification import resolve_public_address

logger = logging.getLogger(__name__)

# An arbitrary bigint that only this application uses for its crawl scheduler lock.
LEADER_LOCK_ID = 7_326_415_902_311
CHECK_SECONDS = 600.0


async def try_become_leader(conn: psycopg.AsyncConnection) -> bool:
    """True if this session holds the lock now (or already held it)."""
    cur = await conn.execute("SELECT pg_try_advisory_lock(%s)", (LEADER_LOCK_ID,))
    row = await cur.fetchone()
    return bool(row and row[0])


async def run_due_crawls(*, resolver: Resolver = resolve_public_address, transport=None) -> int:
    """Starts a scheduled crawl for every client that is due. Returns how many ran."""
    ran = 0
    for client_id in await clients_due_for_crawl(interval_days=INTERVAL_DAYS):
        client = await get_client(client_id)
        if client is None:
            continue
        try:
            run = await begin_run(client, trigger=TRIGGER_SCHEDULED, requested_by=None)
        except (CrawlAlreadyRunning, CrawlNotConfigured):
            continue
        await execute_crawl(run, client, resolver=resolver, transport=transport)
        ran += 1
    return ran


async def _scheduler_session(*, check_seconds: float, sleep: Sleep) -> None:
    """Holds one connection (and so the lock) and checks for due crawls until it fails."""
    conninfo = get_settings().database_url
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        while True:
            if await try_become_leader(conn):
                ran = await run_due_crawls()
                if ran:
                    logger.info("Scheduled website crawls finished: %d", ran)
            await sleep(check_seconds)


async def run_scheduler(
    *, check_seconds: float = CHECK_SECONDS, sleep: Sleep = asyncio.sleep
) -> None:
    """Runs until cancelled. A failed session is logged and reconnected."""
    while True:
        try:
            await _scheduler_session(check_seconds=check_seconds, sleep=sleep)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Website crawl scheduler stopped: %s", type(exc).__name__)
        await sleep(check_seconds)
