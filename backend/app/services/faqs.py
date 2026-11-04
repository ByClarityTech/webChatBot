"""Client FAQs: rows in faqs, and approved ones indexed by the worker as one chunk each.

Only approved FAQs reach the chatbot. The worker is called before or after the database
change so that a failure never leaves a draft in the chatbot: approving indexes first,
taking an FAQ out (back to draft, or delete) removes its chunk first.
"""

from uuid import UUID

import psycopg
from fastapi import HTTPException, status

from app.db.models.client import ClientRecord
from app.db.models.faq import (
    MAX_DEFAULT_QUESTIONS,
    STATUS_APPROVED,
    STATUS_DRAFT,
    FaqRecord,
    delete_faq,
    get_faq,
    insert_faq,
    list_faqs,
    set_default_questions,
    update_faq,
)
from app.schemas.faqs import FaqListResponse, FaqResponse
from app.services.document_worker import raise_worker_error, worker_request

DUPLICATE_QUESTION = "This client already has an FAQ with this question"


def to_faq_response(faq: FaqRecord) -> FaqResponse:
    return FaqResponse(
        id=faq.id,
        client_id=faq.client_id,
        question=faq.question,
        answer=faq.answer,
        status=faq.status,
        is_default=faq.default_order is not None,
        default_order=faq.default_order,
        source_id=faq.source_id,
        source_filename=faq.source_filename,
        source_removed=faq.source_removed,
        created_at=faq.created_at,
        updated_at=faq.updated_at,
    )


async def _index(faq: FaqRecord) -> None:
    response = await worker_request(
        "PUT",
        f"/faqs/{faq.id}",
        json={"client_id": str(faq.client_id), "question": faq.question, "answer": faq.answer},
    )
    raise_worker_error(response, "Failed to index FAQ")


async def _unindex(faq: FaqRecord) -> None:
    response = await worker_request(
        "DELETE", f"/faqs/{faq.id}", params={"client_id": str(faq.client_id)}
    )
    raise_worker_error(response, "Failed to remove FAQ from the chatbot")


def _duplicate_error() -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DUPLICATE_QUESTION)


async def get_client_faq(client: ClientRecord, faq_id: UUID) -> FaqRecord:
    # Same 404 for "doesn't exist" and "another client's".
    faq = await get_faq(faq_id, client_id=client.id)
    if faq is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="FAQ not found")
    return faq


async def list_client_faqs(client: ClientRecord) -> FaqListResponse:
    faqs = await list_faqs(client.id)
    return FaqListResponse(
        client_id=client.id,
        faqs=[to_faq_response(faq) for faq in faqs],
        default_count=sum(1 for faq in faqs if faq.default_order is not None),
        max_defaults=MAX_DEFAULT_QUESTIONS,
    )


async def create_faq(
    client: ClientRecord, *, question: str, answer: str, created_by: UUID | None
) -> FaqRecord:
    """A new FAQ is a draft: it reaches the chatbot once approved."""
    try:
        return await insert_faq(
            client_id=client.id, question=question, answer=answer, created_by=created_by
        )
    except psycopg.errors.UniqueViolation as exc:
        raise _duplicate_error() from exc


async def change_faq(
    client: ClientRecord,
    faq_id: UUID,
    *,
    question: str | None,
    answer: str | None,
    status_: str | None,
) -> tuple[FaqRecord, list[str]]:
    """Edit and/or approve or un-approve. Returns the FAQ and the audit actions it implies."""
    faq = await get_client_faq(client, faq_id)
    actions: list[str] = []

    # Leaving the chatbot: remove the chunk before the row says draft.
    if status_ == STATUS_DRAFT and faq.status == STATUS_APPROVED:
        await _unindex(faq)
        actions.append("faq.unapprove")

    try:
        updated = await update_faq(faq.id, question=question, answer=answer, status=status_)
    except psycopg.errors.UniqueViolation as exc:
        if "faq.unapprove" in actions:
            await _index(faq)  # put it back as it was
        raise _duplicate_error() from exc
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="FAQ not found")

    if question is not None or answer is not None:
        actions.append("faq.update")
    becomes_approved = status_ == STATUS_APPROVED and faq.status != STATUS_APPROVED
    if becomes_approved:
        actions.append("faq.approve")

    # In the chatbot now: (re)index with the new text. On failure, undo the database change.
    if updated.status == STATUS_APPROVED and (becomes_approved or "faq.update" in actions):
        try:
            await _index(updated)
        except HTTPException:
            await update_faq(faq.id, question=faq.question, answer=faq.answer, status=faq.status)
            raise
    return updated, actions


async def approve_all(client: ClientRecord) -> list[FaqRecord]:
    """Approves the client's drafts one by one; stops at the first indexing failure, keeping
    the ones already approved."""
    approved: list[FaqRecord] = []
    for faq in await list_faqs(client.id):
        if faq.status != STATUS_DRAFT:
            continue
        await _index(faq)
        updated = await update_faq(faq.id, status=STATUS_APPROVED)
        if updated is not None:
            approved.append(updated)
    return approved


async def remove_faq(client: ClientRecord, faq_id: UUID) -> FaqRecord:
    faq = await get_client_faq(client, faq_id)
    # Out of the chatbot first; if the worker fails the row stays and can be deleted again.
    await _unindex(faq)
    await delete_faq(faq.id)
    return faq


async def set_defaults(client: ClientRecord, faq_ids: list[UUID]) -> list[FaqRecord]:
    try:
        return await set_default_questions(client.id, faq_ids)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
