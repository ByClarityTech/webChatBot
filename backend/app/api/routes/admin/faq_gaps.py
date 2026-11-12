from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from app.api.deps.auth import require_access
from app.core.permissions import Area
from app.db.models.knowledge_gap import KnowledgeGap
from app.db.models.user import UserRecord
from app.schemas.faq_gaps import FaqGapItem, FaqGapListResponse
from app.schemas.faqs import FaqResponse
from app.services.admin_audit import log_admin_action
from app.services.client_scope import resolve_client_scope
from app.services.faqs import to_faq_response
from app.services.knowledge_gaps import dismiss_gap, draft_from_gap, top_open_gaps

router = APIRouter(prefix="/admin/faq-gaps", tags=["admin-faq-gaps"])

# Unanswered questions are FAQ work: the same roles as the FAQs themselves.
GapActor = Annotated[UserRecord, Depends(require_access(Area.documents))]
ClientIdQuery = Annotated[UUID | None, Query()]


def _item(gap: KnowledgeGap) -> FaqGapItem:
    return FaqGapItem(
        id=gap.id,
        question=gap.sample_query,
        reason=gap.reason,
        top_score=gap.top_score,
        ask_count=gap.ask_count,
        status=gap.status,
        drafted_faq_id=gap.drafted_faq_id,
        last_seen_at=gap.last_seen_at,
    )


@router.get("", response_model=FaqGapListResponse)
async def list_gaps(
    actor: GapActor,
    client_id: ClientIdQuery = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> FaqGapListResponse:
    """Open unanswered questions, most asked first."""
    client = await resolve_client_scope(actor, client_id)
    gaps = await top_open_gaps(client.id, limit)
    return FaqGapListResponse(gaps=[_item(gap) for gap in gaps])


@router.post("/{gap_id}/draft", response_model=FaqResponse, status_code=status.HTTP_201_CREATED)
async def draft_gap(
    gap_id: UUID,
    request: Request,
    actor: GapActor,
    client_id: ClientIdQuery = None,
) -> FaqResponse:
    """A draft FAQ with this question and no answer yet. A person writes and approves it."""
    client = await resolve_client_scope(actor, client_id)
    faq = await draft_from_gap(client.id, gap_id, actor)
    await log_admin_action(
        "faq_gap.draft",
        request=request,
        resource_type="faq_gap",
        resource_id=str(gap_id),
        client_id=client.id,
    )
    return to_faq_response(faq)


@router.post("/{gap_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss(
    gap_id: UUID,
    request: Request,
    actor: GapActor,
    client_id: ClientIdQuery = None,
) -> None:
    client = await resolve_client_scope(actor, client_id)
    await dismiss_gap(client.id, gap_id)
    await log_admin_action(
        "faq_gap.dismiss",
        request=request,
        resource_type="faq_gap",
        resource_id=str(gap_id),
        client_id=client.id,
    )
