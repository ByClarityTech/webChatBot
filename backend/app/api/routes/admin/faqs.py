from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from app.api.deps.auth import require_access
from app.core.permissions import Area
from app.db.models.user import UserRecord
from app.schemas.faqs import (
    FaqApproveAllResponse,
    FaqCreateRequest,
    FaqDefaultsRequest,
    FaqGenerateResponse,
    FaqListResponse,
    FaqResponse,
    FaqUpdateRequest,
)
from app.services import faq_generation, faqs
from app.services.admin_audit import log_admin_action
from app.services.client_scope import resolve_client_scope

router = APIRouter(prefix="/admin/faqs", tags=["admin-faqs"])

# FAQs are part of the knowledge base: same roles as documents.
FaqActor = Annotated[UserRecord, Depends(require_access(Area.documents))]
# Super Admins name the client; Admins may leave it out (always their own client).
ClientIdQuery = Annotated[UUID | None, Query()]


async def _audit(request: Request, action: str, client_id: UUID, faq_id: UUID | None = None):
    await log_admin_action(
        action,
        request=request,
        resource_type="faq",
        resource_id=str(faq_id) if faq_id else None,
        client_id=client_id,
    )


@router.get("", response_model=FaqListResponse)
async def list_faqs(actor: FaqActor, client_id: ClientIdQuery = None) -> FaqListResponse:
    client = await resolve_client_scope(actor, client_id)
    return await faqs.list_client_faqs(client)


@router.post("", response_model=FaqResponse, status_code=status.HTTP_201_CREATED)
async def create_faq(
    body: FaqCreateRequest, request: Request, actor: FaqActor, client_id: ClientIdQuery = None
) -> FaqResponse:
    client = await resolve_client_scope(actor, client_id)
    faq = await faqs.create_faq(
        client, question=body.question, answer=body.answer, created_by=actor.id
    )
    await _audit(request, "faq.create", client.id, faq.id)
    return faqs.to_faq_response(faq)


@router.post("/approve-all", response_model=FaqApproveAllResponse)
async def approve_all(
    request: Request, actor: FaqActor, client_id: ClientIdQuery = None
) -> FaqApproveAllResponse:
    client = await resolve_client_scope(actor, client_id)
    approved = await faqs.approve_all(client)
    if approved:
        await _audit(request, "faq.approve_all", client.id)
    return FaqApproveAllResponse(
        approved=len(approved), faqs=[faqs.to_faq_response(faq) for faq in approved]
    )


@router.post("/generate", response_model=FaqGenerateResponse)
async def generate_faqs(
    request: Request, actor: FaqActor, client_id: ClientIdQuery = None
) -> FaqGenerateResponse:
    """Drafts FAQs from the client's ready documents. Takes a while: it calls the LLM."""
    client = await resolve_client_scope(actor, client_id)
    result = await faq_generation.generate_faqs(client, created_by=actor.id)
    if result.created:
        await _audit(request, "faq.generate", client.id)
    return FaqGenerateResponse(
        created=[faqs.to_faq_response(faq) for faq in result.created],
        skipped_existing=result.skipped_existing,
        documents_used=result.documents_used,
        documents_skipped=result.documents_skipped,
        truncated=result.truncated,
    )


@router.put("/defaults", response_model=list[FaqResponse])
async def set_defaults(
    body: FaqDefaultsRequest, request: Request, actor: FaqActor, client_id: ClientIdQuery = None
) -> list[FaqResponse]:
    client = await resolve_client_scope(actor, client_id)
    defaults = await faqs.set_defaults(client, body.faq_ids)
    await _audit(request, "faq.defaults_update", client.id)
    return [faqs.to_faq_response(faq) for faq in defaults]


@router.patch("/{faq_id}", response_model=FaqResponse)
async def update_faq(
    faq_id: UUID,
    body: FaqUpdateRequest,
    request: Request,
    actor: FaqActor,
    client_id: ClientIdQuery = None,
) -> FaqResponse:
    client = await resolve_client_scope(actor, client_id)
    faq, actions = await faqs.change_faq(
        client, faq_id, question=body.question, answer=body.answer, status_=body.status
    )
    for action in actions:
        await _audit(request, action, client.id, faq.id)
    return faqs.to_faq_response(faq)


@router.delete("/{faq_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_faq(
    faq_id: UUID, request: Request, actor: FaqActor, client_id: ClientIdQuery = None
) -> None:
    client = await resolve_client_scope(actor, client_id)
    await faqs.remove_faq(client, faq_id)
    await _audit(request, "faq.delete", client.id, faq_id)
