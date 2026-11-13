"""In-memory stand-ins for the crawler's database functions, and a fake website.

The crawl and approval modules import the repository functions by name, so tests patch those
names with the methods of FakeCrawlDb (install). The fake site answers through an httpx
MockTransport, so the real fetcher runs, with its address pinning and redirect checks.
"""

from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

from app.db.models.client import ClientRecord
from app.db.models.crawler import (
    UPDATE_PENDING,
    UPDATE_SUPERSEDED,
    CrawledPageRecord,
    CrawlRunRecord,
    PendingUpdateRecord,
)

NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
PUBLIC_ADDRESS = "93.184.216.34"
SITE_HOST = "clinic.example"

HTML = {"content-type": "text/html; charset=utf-8"}


def page_html(title: str, body: str, *, links: list[str] | None = None) -> str:
    anchors = "".join(f'<a href="{href}">link</a> ' for href in links or [])
    return (
        f"<!doctype html><html><head><title>{title}</title></head><body>"
        f"<nav>Home About</nav><main><h1>{title}</h1><p>{body}</p>{anchors}</main>"
        "<footer>Copyright</footer></body></html>"
    )


def html_page(title: str, body: str, *, links: list[str] | None = None):
    return (200, page_html(title, body, links=links), dict(HTML))


class FakeSite:
    """Paths of one host -> (status, body, headers). Anything else is 404."""

    def __init__(self, pages: dict | None = None, host: str = SITE_HOST) -> None:
        self.pages: dict = dict(pages or {})
        self.host = host
        self.requests: list[tuple[str, str]] = []  # (host header, request URL)

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append((request.headers.get("host", ""), str(request.url)))
            status, body, headers = self.pages.get(
                request.url.path, (404, "not found", {"content-type": "text/plain"})
            )
            return httpx.Response(
                status, content=body.encode() if isinstance(body, str) else body, headers=headers
            )

        return httpx.MockTransport(handler)

    def fetched_paths(self) -> list[str]:
        return [urlsplit(url).path for _, url in self.requests]


async def public_resolver(host: str) -> str:
    return PUBLIC_ADDRESS


def make_client(
    client_id: UUID | None = None, website_url: str | None = f"https://{SITE_HOST}"
) -> ClientRecord:
    return ClientRecord(
        id=client_id or uuid4(),
        name="Clinic",
        slug="clinic",
        status="active",
        created_at=NOW,
        updated_at=NOW,
        website_url=website_url,
    )


def make_run(client_id: UUID, trigger: str = "manual") -> CrawlRunRecord:
    return CrawlRunRecord(
        id=uuid4(),
        client_id=client_id,
        trigger=trigger,
        status="running",
        pages_fetched=0,
        pages_changed=0,
        pages_failed=0,
        error=None,
        requested_by=None,
        started_at=NOW,
        finished_at=None,
    )


class FakeCrawlDb:
    def __init__(self, client_id: UUID) -> None:
        self.client_id = client_id
        self.pages: dict[UUID, CrawledPageRecord] = {}
        self.pending: dict[UUID, PendingUpdateRecord] = {}
        self.finished_runs: list[dict] = []

    # ---- helpers for setting up a test

    def add_page(
        self,
        url: str,
        *,
        live_text: str | None = None,
        live_hash: str | None = None,
        included: bool = True,
        rejected_hash: str | None = None,
        title: str | None = None,
    ) -> CrawledPageRecord:
        page = CrawledPageRecord(
            id=uuid4(),
            client_id=self.client_id,
            url=url,
            included=included,
            status="active",
            title=title,
            live_content_hash=live_hash,
            live_text=live_text,
            rejected_content_hash=rejected_hash,
            last_status_code=None,
            last_error=None,
            last_crawled_at=None,
            created_at=NOW,
            updated_at=NOW,
        )
        self.pages[page.id] = page
        return page

    def page_by_url(self, url: str) -> CrawledPageRecord | None:
        return next((p for p in self.pages.values() if p.url == url), None)

    def open_changes(self) -> list[PendingUpdateRecord]:
        return [c for c in self.pending.values() if c.status == UPDATE_PENDING]

    def _replace(self, page_id: UUID, **changes) -> None:

        self.pages[page_id] = replace(self.pages[page_id], **changes)

    # ---- crawl functions

    async def list_crawled_pages(self, client_id):
        return sorted(
            (p for p in self.pages.values() if p.client_id == client_id), key=lambda p: p.url
        )

    async def get_crawled_page_by_url(self, client_id, url):
        return next(
            (p for p in self.pages.values() if p.client_id == client_id and p.url == url), None
        )

    async def get_crawled_page(self, page_id, *, client_id):
        page = self.pages.get(page_id)
        return page if page is not None and page.client_id == client_id else None

    async def ensure_crawled_page(self, client_id, url):
        existing = await self.get_crawled_page_by_url(client_id, url)
        if existing is not None:
            return existing
        return self.add_page(url)

    async def record_page_fetch(self, page_id, *, status_code, error, title=None):

        page = self.pages[page_id]
        self.pages[page_id] = replace(
            page,
            last_status_code=status_code,
            last_error=error,
            last_crawled_at=NOW,
            title=title or page.title,
        )

    async def set_page_included(self, page_id, *, client_id, included):
        self._replace(page_id, included=included)

    async def set_page_live(self, page_id, *, title, content_hash, text):
        self._replace(
            page_id,
            status="active",
            title=title,
            live_content_hash=content_hash,
            live_text=text,
            rejected_content_hash=None,
        )

    async def set_page_missing(self, page_id):
        self._replace(page_id, status="missing", live_content_hash=None, live_text=None)

    async def set_page_rejected_hash(self, page_id, content_hash):
        self._replace(page_id, rejected_content_hash=content_hash)

    async def open_change_for_page(self, page_id):
        return next(
            (
                c
                for c in self.pending.values()
                if c.page_id == page_id and c.status == UPDATE_PENDING
            ),
            None,
        )

    async def supersede_open_change(self, page_id):
        for change in list(self.pending.values()):
            if change.page_id == page_id and change.status == UPDATE_PENDING:
                self._set_status(change.id, UPDATE_SUPERSEDED)

    def _set_status(self, change_id: UUID, status: str, decided_by=None) -> None:

        self.pending[change_id] = replace(
            self.pending[change_id], status=status, decided_by=decided_by, decided_at=NOW
        )

    async def insert_pending_change(
        self,
        *,
        client_id,
        page_id,
        crawl_run_id,
        change_type,
        proposed_title,
        proposed_text,
        proposed_hash,
        words_added,
        words_removed,
    ) -> UUID:
        await self.supersede_open_change(page_id)
        page = self.pages[page_id]
        change = PendingUpdateRecord(
            id=uuid4(),
            client_id=client_id,
            page_id=page_id,
            page_url=page.url,
            crawl_run_id=crawl_run_id,
            change_type=change_type,
            status=UPDATE_PENDING,
            proposed_title=proposed_title,
            proposed_text=proposed_text,
            proposed_hash=proposed_hash,
            words_added=words_added,
            words_removed=words_removed,
            live_title=page.title,
            live_text=page.live_text,
            decided_by=None,
            decided_at=None,
            created_at=NOW,
            updated_at=NOW,
        )
        self.pending[change.id] = change
        return change.id

    async def finish_crawl_run(self, run_id, **fields):
        self.finished_runs.append({"id": run_id, **fields})

    # ---- approval functions

    async def list_pending_changes(self, client_id, *, status=UPDATE_PENDING):
        return [c for c in self.pending.values() if c.client_id == client_id and c.status == status]

    async def get_pending_change(self, update_id, *, client_id):
        change = self.pending.get(update_id)
        if change is None or change.client_id != client_id:
            return None
        page = self.pages[change.page_id]

        return replace(change, live_title=page.title, live_text=page.live_text)

    async def decide_pending_change(self, update_id, *, status, decided_by):
        change = self.pending.get(update_id)
        if change is None or change.status != UPDATE_PENDING:
            return False
        self._set_status(update_id, status, decided_by)
        return True


REPOSITORY_NAMES = (
    "list_crawled_pages",
    "get_crawled_page_by_url",
    "get_crawled_page",
    "ensure_crawled_page",
    "record_page_fetch",
    "set_page_included",
    "set_page_live",
    "set_page_missing",
    "set_page_rejected_hash",
    "open_change_for_page",
    "supersede_open_change",
    "insert_pending_change",
    "finish_crawl_run",
    "list_pending_changes",
    "get_pending_change",
    "decide_pending_change",
)


def install(monkeypatch, db: FakeCrawlDb, module_names: list[str]) -> None:
    """Points each module's imported repository names at the fake database."""
    for module in module_names:
        for name in REPOSITORY_NAMES:
            monkeypatch.setattr(f"{module}.{name}", getattr(db, name), raising=False)
