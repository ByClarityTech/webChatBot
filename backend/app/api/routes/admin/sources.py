"""Admin routes for the website crawler: the Sources page (FR-02, FR-05).

Admins and Super Admins only (the documents area). Every change is audited. Approving or
rejecting a change, and including or excluding a page, is only done by an admin here; the
monthly crawl never changes what the chatbot uses.
"""

from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.deps.auth import require_access
from app.core.permissions import Area
from app.db.models.client import ClientRecord, normalize_website_url, set_client_website_url
from app.db.models.crawler import (
    CrawlAlreadyRunning,
    CrawledPageRecord,
    CrawlRunRecord,
    PendingUpdateRecord,
    get_pending_change,
    get_running_crawl,
    last_finished_crawl,
    list_crawl_runs,
    list_crawled_pages,
    list_pending_changes,
)
from app.db.models.user import UserRecord
from app.schemas.crawler import (
    CrawledPageOut,
    CrawledPagesResponse,
    CrawlRunOut,
    CrawlRunsResponse,
    DiffSegmentOut,
    PageChangesRequest,
    PendingChangeDetailOut,
    PendingChangeOut,
    PendingChangesResponse,
    SourcesOverviewResponse,
    WebsiteUrlRequest,
)
from app.services.admin_audit import log_admin_action
from app.services.client_scope import resolve_client_scope
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
from app.services.website_crawler.crawl import (
    INTERVAL_DAYS,
    CrawlNotConfigured,
    begin_run,
    launch_in_background,
)
from app.services.website_crawler.diffing import word_diff
from app.services.website_crawler.urls import MAX_DEPTH, MAX_PAGES

router = APIRouter(prefix="/admin/sources", tags=["admin-sources"])

SourcesActor = Annotated[UserRecord, Depends(require_access(Area.documents))]
ClientIdQuery = Annotated[UUID | None, Query()]


def _run_out(run: CrawlRunRecord) -> CrawlRunOut:
    return CrawlRunOut(
        id=run.id,
        trigger=run.trigger,
        status=run.status,
        pages_fetched=run.pages_fetched,
        pages_changed=run.pages_changed,
        pages_failed=run.pages_failed,
        error=run.error,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _page_out(page: CrawledPageRecord) -> CrawledPageOut:
    return CrawledPageOut(
        id=page.id,
        url=page.url,
        title=page.title,
        included=page.included,
        status="missing" if page.status == "missing" else "active",
        live=page.live_content_hash is not None,
        last_status_code=page.last_status_code,
        last_error=page.last_error,
        last_crawled_at=page.last_crawled_at,
    )


def _change_out(change: PendingUpdateRecord) -> PendingChangeOut:
    return PendingChangeOut(
        id=change.id,
        page_id=change.page_id,
        page_url=change.page_url,
        change_type=change.change_type,
        status=change.status,
        proposed_title=change.proposed_title,
        words_added=change.words_added,
        words_removed=change.words_removed,
        created_at=change.created_at,
        decided_at=change.decided_at,
    )


async def _overview(client: ClientRecord) -> SourcesOverviewResponse:
    running = await get_running_crawl(client.id)
    recent = await list_crawl_runs(client.id, limit=1)
    finished = await last_finished_crawl(client.id)
    pending = await list_pending_changes(client.id)
    next_at = (
        finished.finished_at + timedelta(days=INTERVAL_DAYS)
        if finished is not None and finished.finished_at is not None
        else None
    )
    return SourcesOverviewResponse(
        client_id=client.id,
        website_url=client.website_url,
        running=_run_out(running) if running else None,
        last_run=_run_out(recent[0]) if recent else None,
        next_scheduled_at=next_at,
        interval_days=INTERVAL_DAYS,
        pending_count=len(pending),
        max_pages=MAX_PAGES,
        max_depth=MAX_DEPTH,
    )


@router.get("/overview", response_model=SourcesOverviewResponse)
async def get_overview(actor: SourcesActor, client_id: ClientIdQuery = None):
    client = await resolve_client_scope(actor, client_id)
    return await _overview(client)


@router.put("/website-url", response_model=SourcesOverviewResponse)
async def set_website_url(
    body: WebsiteUrlRequest,
    request: Request,
    actor: SourcesActor,
    client_id: ClientIdQuery = None,
):
    client = await resolve_client_scope(actor, client_id)
    try:
        normalized = normalize_website_url(body.website_url)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    previous = client.website_url
    updated = await set_client_website_url(client.id, normalized)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    if previous and previous != normalized:
        # Pages of the old site stay live until an admin says otherwise: propose their removal.
        await propose_removal_of_live_pages(client)
    await log_admin_action(
        "website.url_update",
        request=request,
        resource_type="client",
        resource_id=str(client.id),
        client_id=client.id,
    )
    return await _overview(updated)


@router.post("/crawls", response_model=CrawlRunOut, status_code=status.HTTP_202_ACCEPTED)
async def start_crawl(
    request: Request, actor: SourcesActor, client_id: ClientIdQuery = None
) -> CrawlRunOut:
    client = await resolve_client_scope(actor, client_id)
    try:
        run = await begin_run(client, trigger="manual", requested_by=actor.id)
    except CrawlNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set the website address before crawling",
        ) from exc
    except CrawlAlreadyRunning as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A crawl is already running for this website",
        ) from exc
    launch_in_background(run, client)
    await log_admin_action(
        "crawl.manual_start",
        request=request,
        resource_type="crawl_run",
        resource_id=str(run.id),
        client_id=client.id,
    )
    return _run_out(run)


@router.get("/crawls", response_model=CrawlRunsResponse)
async def list_runs(actor: SourcesActor, client_id: ClientIdQuery = None) -> CrawlRunsResponse:
    client = await resolve_client_scope(actor, client_id)
    runs = await list_crawl_runs(client.id, limit=20)
    return CrawlRunsResponse(runs=[_run_out(run) for run in runs])


@router.get("/pages", response_model=CrawledPagesResponse)
async def list_pages(actor: SourcesActor, client_id: ClientIdQuery = None) -> CrawledPagesResponse:
    client = await resolve_client_scope(actor, client_id)
    pages = await list_crawled_pages(client.id)
    return CrawledPagesResponse(pages=[_page_out(page) for page in pages])


@router.patch("/pages/{page_id}", response_model=CrawledPageOut)
async def change_page(
    page_id: UUID,
    body: PageChangesRequest,
    request: Request,
    actor: SourcesActor,
    client_id: ClientIdQuery = None,
) -> CrawledPageOut:
    client = await resolve_client_scope(actor, client_id)
    try:
        page = await set_page_include(client, page_id, included=body.included)
    except PageNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page not found") from exc
    await log_admin_action(
        "page.include" if body.included else "page.exclude",
        request=request,
        resource_type="crawled_page",
        resource_id=str(page_id),
        client_id=client.id,
    )
    return _page_out(page)


@router.get("/changes", response_model=PendingChangesResponse)
async def list_changes(actor: SourcesActor, client_id: ClientIdQuery = None):
    client = await resolve_client_scope(actor, client_id)
    changes = await list_pending_changes(client.id)
    return PendingChangesResponse(changes=[_change_out(change) for change in changes])


@router.get("/changes/{update_id}", response_model=PendingChangeDetailOut)
async def get_change(
    update_id: UUID, actor: SourcesActor, client_id: ClientIdQuery = None
) -> PendingChangeDetailOut:
    client = await resolve_client_scope(actor, client_id)
    change = await get_pending_change(update_id, client_id=client.id)
    if change is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Change not found")
    diff = word_diff(change.live_text or "", change.proposed_text or "")
    return PendingChangeDetailOut(
        **_change_out(change).model_dump(),
        live_title=change.live_title,
        segments=[DiffSegmentOut(op=s.op, text=s.text) for s in diff.segments],
    )


def _decision_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ChangeNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Change not found")
    if isinstance(exc, ChangeAlreadyDecided):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This change was already decided"
        )
    if isinstance(exc, IndexingFailed):
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    raise exc


@router.post("/changes/{update_id}/approve", response_model=PendingChangeOut)
async def approve(
    update_id: UUID, request: Request, actor: SourcesActor, client_id: ClientIdQuery = None
) -> PendingChangeOut:
    client = await resolve_client_scope(actor, client_id)
    try:
        change = await approve_change(client, update_id, actor_id=actor.id)
    except (ChangeNotFound, ChangeAlreadyDecided, IndexingFailed) as exc:
        raise _decision_error(exc) from exc
    await log_admin_action(
        "change.approve",
        request=request,
        resource_type="pending_update",
        resource_id=str(update_id),
        client_id=client.id,
    )
    return _change_out(change)


@router.post("/changes/{update_id}/reject", response_model=PendingChangeOut)
async def reject(
    update_id: UUID, request: Request, actor: SourcesActor, client_id: ClientIdQuery = None
) -> PendingChangeOut:
    client = await resolve_client_scope(actor, client_id)
    try:
        change = await reject_change(client, update_id, actor_id=actor.id)
    except (ChangeNotFound, ChangeAlreadyDecided) as exc:
        raise _decision_error(exc) from exc
    await log_admin_action(
        "change.reject",
        request=request,
        resource_type="pending_update",
        resource_id=str(update_id),
        client_id=client.id,
    )
    return _change_out(change)
