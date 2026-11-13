from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

ChangeType = Literal["new", "changed", "removed"]


class WebsiteUrlRequest(BaseModel):
    # Blank clears the website. The address is checked by normalize_website_url.
    website_url: str | None = Field(default=None, max_length=2048)


class CrawlRunOut(BaseModel):
    id: UUID
    trigger: Literal["scheduled", "manual"]
    status: Literal["running", "completed", "failed"]
    pages_fetched: int
    pages_changed: int
    pages_failed: int
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class SourcesOverviewResponse(BaseModel):
    client_id: UUID
    website_url: str | None
    running: CrawlRunOut | None
    last_run: CrawlRunOut | None
    # Next monthly run, or None when none is due by a fixed date (no crawl yet, or not set).
    next_scheduled_at: datetime | None
    interval_days: int
    pending_count: int
    max_pages: int
    max_depth: int


class CrawlRunsResponse(BaseModel):
    runs: list[CrawlRunOut]


class CrawledPageOut(BaseModel):
    id: UUID
    url: str
    title: str | None
    included: bool
    # Active: has been crawled. Missing: its removal was approved.
    status: Literal["active", "missing"]
    # Live: the chatbot uses a version of this page now.
    live: bool
    last_status_code: int | None
    last_error: str | None
    last_crawled_at: datetime | None


class CrawledPagesResponse(BaseModel):
    pages: list[CrawledPageOut]


class PageChangesRequest(BaseModel):
    included: bool


class PendingChangeOut(BaseModel):
    id: UUID
    page_id: UUID
    page_url: str
    change_type: ChangeType
    status: Literal["pending", "approved", "rejected", "superseded"]
    proposed_title: str | None
    words_added: int
    words_removed: int
    created_at: datetime
    decided_at: datetime | None


class DiffSegmentOut(BaseModel):
    op: Literal["equal", "insert", "delete"]
    text: str


class PendingChangeDetailOut(PendingChangeOut):
    live_title: str | None
    # Word-level diff from the live text to the proposed text, in reading order.
    segments: list[DiffSegmentOut]


class PendingChangesResponse(BaseModel):
    changes: list[PendingChangeOut]
