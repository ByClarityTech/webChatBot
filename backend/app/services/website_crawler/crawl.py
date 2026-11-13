"""One crawl of a client's website: fetch, extract, compare, and propose changes.

A crawl never changes what the chatbot knows. It only records pages and proposes changes
(pending_knowledge_updates). An admin approves them, and only then does the worker index.

Limits: 30 pages per run, depth 3 from the site root, one request at a time with at least a
second between requests (or the site's Crawl-delay, up to 10 seconds), robots.txt honored.
"""

import asyncio
import logging
import re
from collections import deque
from collections.abc import Awaitable, Callable
from uuid import UUID

from app.db.models.client import ClientRecord
from app.db.models.crawler import (
    CHANGE_CHANGED,
    CHANGE_NEW,
    CHANGE_REMOVED,
    RUN_COMPLETED,
    RUN_FAILED,
    CrawledPageRecord,
    CrawlRunRecord,
    create_crawl_run,
    ensure_crawled_page,
    finish_crawl_run,
    get_crawled_page_by_url,
    insert_pending_change,
    list_crawled_pages,
    open_change_for_page,
    record_page_fetch,
    supersede_open_change,
)
from app.services.website_crawler.diffing import content_hash, word_diff
from app.services.website_crawler.extract import ExtractedPage, extract_page
from app.services.website_crawler.fetcher import FetchResult, Resolver, fetch_public
from app.services.website_crawler.robots import DENY_ALL, RobotsPolicy, policy_from_response
from app.services.website_crawler.urls import (
    MAX_DEPTH,
    MAX_PAGES,
    canonical_url,
    host_of,
    in_scope,
    site_root,
)
from app.services.widget_verification import CheckRefused, resolve_public_address

logger = logging.getLogger(__name__)

# Every scheduled and manual crawl is at least this far apart for a site (see scheduler.py).
INTERVAL_DAYS = 30

_TASKS: set[asyncio.Task] = set()
_CHARSET = re.compile(r"charset=([\w.:-]+)", re.IGNORECASE)

Sleep = Callable[[float], Awaitable[None]]


class CrawlNotConfigured(Exception):
    """The client has no website address yet."""


class CrawlStopped(Exception):
    """The crawl cannot go on (for example robots.txt cannot be read). The message is kept."""


async def begin_run(
    client: ClientRecord, *, trigger: str, requested_by: UUID | None
) -> CrawlRunRecord:
    """Records a running crawl. Raises CrawlNotConfigured or CrawlAlreadyRunning."""
    if not client.website_url:
        raise CrawlNotConfigured()
    return await create_crawl_run(client.id, trigger=trigger, requested_by=requested_by)


def launch_in_background(run: CrawlRunRecord, client: ClientRecord) -> None:
    """Runs the crawl on this event loop and keeps a reference until it ends."""
    task = asyncio.create_task(execute_crawl(run, client))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def is_html(content_type: str) -> bool:
    kind = content_type.split(";", 1)[0].strip().lower()
    return kind in ("text/html", "application/xhtml+xml")


def decode_html(result: FetchResult) -> str:
    match = _CHARSET.search(result.content_type)
    charset = match.group(1) if match else "utf-8"
    try:
        return result.body.decode(charset, errors="replace")
    except LookupError:
        return result.body.decode("utf-8", errors="replace")


class _Counts:
    def __init__(self) -> None:
        self.fetched = 0
        self.changed = 0
        self.failed = 0


async def execute_crawl(
    run: CrawlRunRecord,
    client: ClientRecord,
    *,
    resolver: Resolver = resolve_public_address,
    transport=None,
    sleep: Sleep = asyncio.sleep,
) -> None:
    """Runs a crawl that begin_run has recorded, and always closes its run row."""
    counts = _Counts()
    try:
        await _crawl(run, client, counts, resolver=resolver, transport=transport, sleep=sleep)
    except CrawlStopped as exc:
        logger.warning("Crawl %s stopped: %s", run.id, exc)
        await _close(run, counts, RUN_FAILED, str(exc))
        return
    except Exception as exc:  # the run row must close whatever happened
        logger.exception("Crawl %s failed", run.id)
        await _close(run, counts, RUN_FAILED, f"Crawl stopped: {type(exc).__name__}")
        return
    await _close(run, counts, RUN_COMPLETED, None)


async def _close(run: CrawlRunRecord, counts: _Counts, status: str, error: str | None) -> None:
    await finish_crawl_run(
        run.id,
        status=status,
        pages_fetched=counts.fetched,
        pages_changed=counts.changed,
        pages_failed=counts.failed,
        error=error,
    )


async def _load_robots(site_host: str, *, resolver: Resolver, transport) -> RobotsPolicy:
    """robots.txt is the first request of a run, so it is not delayed."""
    try:
        result = await fetch_public(
            site_root(site_host) + "robots.txt",
            site_host=site_host,
            resolver=resolver,
            transport=transport,
        )
    except CheckRefused:
        return DENY_ALL
    return policy_from_response(result.status_code, result.body.decode("utf-8", errors="replace"))


async def _crawl(
    run: CrawlRunRecord,
    client: ClientRecord,
    counts: _Counts,
    *,
    resolver: Resolver,
    transport,
    sleep: Sleep,
) -> None:
    site_host = host_of(client.website_url or "")
    if not site_host:
        raise CrawlStopped("The website address is not set")

    robots = await _load_robots(site_host, resolver=resolver, transport=transport)
    if robots.deny_all:
        raise CrawlStopped(
            "robots.txt could not be read (server error or no answer), so nothing was crawled"
        )

    # Known pages that are included are re-checked first, then the site root, then whatever
    # the links lead to. Depth counts links from the root; known pages start at 0.
    known = [page.url for page in await list_crawled_pages(client.id) if page.included]
    queue: deque[tuple[str, int]] = deque([(url, 0) for url in known])
    queue.append((site_root(site_host), 0))
    seen: set[str] = set()

    while queue and counts.fetched < MAX_PAGES:
        url, depth = queue.popleft()
        if url in seen or not in_scope(url, site_host) or not robots.allows(url):
            continue
        seen.add(url)
        existing = await get_crawled_page_by_url(client.id, url)
        if existing is not None and not existing.included:
            continue

        # One request at a time, with the site's pace between requests.
        if counts.fetched:
            await sleep(robots.delay_seconds())
        counts.fetched += 1

        try:
            result = await fetch_public(
                url, site_host=site_host, resolver=resolver, transport=transport
            )
        except CheckRefused as exc:
            page = await ensure_crawled_page(client.id, url)
            await record_page_fetch(page.id, status_code=None, error=exc.code)
            counts.failed += 1
            continue

        page = await ensure_crawled_page(client.id, result.final_url)
        status = result.status_code
        if status in (404, 410):
            await record_page_fetch(page.id, status_code=status, error=None)
            if page.live_content_hash and await _propose_removal(page, client, run):
                counts.changed += 1
            continue
        if not 200 <= status < 300:
            await record_page_fetch(page.id, status_code=status, error=f"HTTP {status}")
            counts.failed += 1
            continue
        if not is_html(result.content_type):
            await record_page_fetch(page.id, status_code=status, error="Not an HTML page")
            continue

        extracted = extract_page(decode_html(result))
        if depth < MAX_DEPTH:
            queue.extend(_links_to_follow(extracted, result.final_url, site_host, depth, seen))

        if not extracted.text.strip():
            await record_page_fetch(page.id, status_code=status, error="No readable text")
            counts.failed += 1
            continue
        await record_page_fetch(
            page.id,
            status_code=status,
            error="Text was cut to 200 KB" if extracted.truncated else None,
            title=extracted.title or None,
        )
        if await _propose_content(page, extracted, client, run):
            counts.changed += 1


def _links_to_follow(
    extracted: ExtractedPage, base: str, site_host: str, depth: int, seen: set[str]
) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for href in extracted.links:
        url = canonical_url(href, base=base)
        if url is None or url in seen or not in_scope(url, site_host):
            continue
        found.append((url, depth + 1))
    return found


async def _propose_content(
    page: CrawledPageRecord, extracted: ExtractedPage, client: ClientRecord, run: CrawlRunRecord
) -> bool:
    """Proposes this text as a change, unless it is what the chatbot has, was rejected, or is
    already proposed. Returns True when a new change was recorded."""
    new_hash = content_hash(extracted.title, extracted.text)
    if page.live_content_hash == new_hash:
        # The page is back to what the chatbot has: an open change for it is out of date.
        await supersede_open_change(page.id)
        return False
    if page.rejected_content_hash == new_hash:
        return False
    open_change = await open_change_for_page(page.id)
    if open_change is not None and open_change.proposed_hash == new_hash:
        return False

    change = CHANGE_NEW if page.live_content_hash is None else CHANGE_CHANGED
    diff = word_diff(page.live_text or "", extracted.text)
    await insert_pending_change(
        client_id=client.id,
        page_id=page.id,
        crawl_run_id=run.id,
        change_type=change,
        proposed_title=extracted.title or None,
        proposed_text=extracted.text,
        proposed_hash=new_hash,
        words_added=diff.words_added,
        words_removed=diff.words_removed,
    )
    return True


async def _propose_removal(
    page: CrawledPageRecord, client: ClientRecord, run: CrawlRunRecord
) -> bool:
    open_change = await open_change_for_page(page.id)
    if open_change is not None and open_change.change_type == CHANGE_REMOVED:
        return False
    await insert_pending_change(
        client_id=client.id,
        page_id=page.id,
        crawl_run_id=run.id,
        change_type=CHANGE_REMOVED,
        proposed_title=None,
        proposed_text=None,
        proposed_hash=None,
        words_added=0,
        words_removed=len((page.live_text or "").split()),
    )
    return True
