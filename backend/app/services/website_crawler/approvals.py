"""Admin decisions on crawled content: approve, reject, and include or exclude a page.

Nothing reaches the chatbot except through approve_change. The worker is called first. The
database says "live" only after the worker has answered, so a failed index leaves the change
pending and the chatbot as it was.
"""

from uuid import UUID

import httpx

from app.db.models.client import ClientRecord
from app.db.models.crawler import (
    CHANGE_REMOVED,
    UPDATE_APPROVED,
    UPDATE_PENDING,
    UPDATE_REJECTED,
    CrawledPageRecord,
    PendingUpdateRecord,
    decide_pending_change,
    get_crawled_page,
    get_pending_change,
    insert_pending_change,
    list_crawled_pages,
    open_change_for_page,
    set_page_included,
    set_page_live,
    set_page_missing,
    set_page_rejected_hash,
    supersede_open_change,
)
from app.services.document_worker import worker_request


class ChangeNotFound(Exception):
    pass


class ChangeAlreadyDecided(Exception):
    pass


class PageNotFound(Exception):
    pass


class IndexingFailed(Exception):
    """The worker could not index or remove the page. The chatbot is unchanged."""


def _worker_message(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    return str(detail) if detail else "The chatbot could not be updated. Try again."


async def _index_version(client: ClientRecord, change: PendingUpdateRecord) -> None:
    response = await worker_request(
        "PUT",
        f"/pages/{change.page_id}",
        json={
            "client_id": str(client.id),
            "url": change.page_url,
            "title": change.proposed_title or "",
            "text": change.proposed_text or "",
            "version": change.proposed_hash,
        },
    )
    if response.status_code >= 400:
        raise IndexingFailed(_worker_message(response))


async def _remove_from_index(client: ClientRecord, page_id: UUID) -> None:
    response = await worker_request(
        "DELETE", f"/pages/{page_id}", params={"client_id": str(client.id)}
    )
    if response.status_code >= 400:
        raise IndexingFailed(_worker_message(response))


async def _get_open_change(client: ClientRecord, update_id: UUID) -> PendingUpdateRecord:
    change = await get_pending_change(update_id, client_id=client.id)
    if change is None:
        raise ChangeNotFound()
    if change.status != UPDATE_PENDING:
        raise ChangeAlreadyDecided()
    return change


async def approve_change(
    client: ClientRecord, update_id: UUID, *, actor_id: UUID | None
) -> PendingUpdateRecord:
    change = await _get_open_change(client, update_id)

    if change.change_type == CHANGE_REMOVED:
        await _remove_from_index(client, change.page_id)
        await set_page_missing(change.page_id)
    else:
        await _index_version(client, change)
        await set_page_live(
            change.page_id,
            title=change.proposed_title,
            content_hash=change.proposed_hash or "",
            text=change.proposed_text or "",
        )

    if not await decide_pending_change(update_id, status=UPDATE_APPROVED, decided_by=actor_id):
        # Another admin decided it while the worker was working. The content is the same
        # version either way, so the chatbot is right; report the conflict.
        raise ChangeAlreadyDecided()
    updated = await get_pending_change(update_id, client_id=client.id)
    assert updated is not None
    return updated


async def reject_change(
    client: ClientRecord, update_id: UUID, *, actor_id: UUID | None
) -> PendingUpdateRecord:
    change = await _get_open_change(client, update_id)
    if change.change_type != CHANGE_REMOVED and change.proposed_hash:
        # Remember this version, so the next crawl does not propose it again.
        await set_page_rejected_hash(change.page_id, change.proposed_hash)
    if not await decide_pending_change(update_id, status=UPDATE_REJECTED, decided_by=actor_id):
        raise ChangeAlreadyDecided()
    updated = await get_pending_change(update_id, client_id=client.id)
    assert updated is not None
    return updated


async def _propose_removal(client: ClientRecord, page: CrawledPageRecord) -> bool:
    """Proposes taking a live page out of the chatbot. False if that is already proposed."""
    open_change = await open_change_for_page(page.id)
    if open_change is not None and open_change.change_type == CHANGE_REMOVED:
        return False
    await insert_pending_change(
        client_id=client.id,
        page_id=page.id,
        crawl_run_id=None,
        change_type=CHANGE_REMOVED,
        proposed_title=None,
        proposed_text=None,
        proposed_hash=None,
        words_added=0,
        words_removed=len((page.live_text or "").split()),
    )
    return True


async def propose_removal_of_live_pages(client: ClientRecord) -> int:
    """Every page the chatbot uses now is proposed for removal. Used when the website changes,
    so the old site's pages do not stay live without a decision. Returns how many were proposed."""
    proposed = 0
    for page in await list_crawled_pages(client.id):
        if page.live_content_hash and await _propose_removal(client, page):
            proposed += 1
    return proposed


async def set_page_include(
    client: ClientRecord, page_id: UUID, *, included: bool
) -> CrawledPageRecord:
    """Including a page only lets the next crawl read it. Excluding a page that the chatbot
    uses proposes its removal, which an admin then approves."""
    page = await get_crawled_page(page_id, client_id=client.id)
    if page is None:
        raise PageNotFound()
    await set_page_included(page_id, client_id=client.id, included=included)
    if not included:
        if page.live_content_hash:
            await _propose_removal(client, page)
        else:
            await supersede_open_change(page_id)
    refreshed = await get_crawled_page(page_id, client_id=client.id)
    if refreshed is None:
        raise PageNotFound()
    return refreshed
