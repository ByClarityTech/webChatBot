"""Admin conversations: the outcome list, phone lookup, patient grouping, and the transcript.

- The list shows outcome labels, counts and durations. Support may use it.
- A phone lookup and the patient view identify a person. Admin and Super Admin only.
- The transcript is Admin and Super Admin only. Every open writes a conversation.view audit row
  before anything is read; if that row cannot be written, the transcript is not shown.
"""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.deps.auth import require_access
from app.core.permissions import Area, can_access
from app.db.models.user import UserRecord
from app.schemas.analytics import ConversationDetailOut, ConversationListOut, PatientGroupListOut
from app.services.admin_audit import require_admin_audit
from app.services.analytics.filters import DEFAULT_PRESET, Period, resolve_period
from app.services.analytics.reports import (
    conversation_detail,
    list_conversations,
    patient_groups,
    timezone_for,
    today_in,
)
from app.services.client_scope import resolve_client_scope
from app.services.contact_privacy import (
    ContactStorageNotConfigured,
    InvalidPhoneError,
    normalize_us_phone,
    phone_hash,
)

router = APIRouter(prefix="/admin/conversations", tags=["admin-conversations"])

ListViewer = Annotated[UserRecord, Depends(require_access(Area.analytics))]
PatientViewer = Annotated[UserRecord, Depends(require_access(Area.conversations))]
ClientIdQuery = Annotated[UUID | None, Query()]
PresetQuery = Annotated[str | None, Query(pattern=r"^(7d|30d|90d)$")]
OutcomeQuery = Annotated[
    Literal["booking", "callback", "handover", "resolved", "abandoned", "in_progress"] | None,
    Query(),
]


async def _client_and_period(
    actor: UserRecord,
    client_id: UUID | None,
    preset: str | None,
    start: date | None,
    end: date | None,
):
    client = await resolve_client_scope(actor, client_id)
    timezone = await timezone_for(client.id)
    chosen_preset = preset if (preset or start or end) else None
    if chosen_preset is None and start is None and end is None:
        chosen_preset = DEFAULT_PRESET
    try:
        period: Period = resolve_period(
            preset=chosen_preset, start=start, end=end, timezone=timezone, today=today_in(timezone)
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return client, period


def _phone_hash_or_refuse(phone: str) -> str:
    try:
        e164 = normalize_us_phone(phone)
    except InvalidPhoneError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    try:
        return phone_hash(e164)
    except ContactStorageNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Phone lookup is not configured on this installation",
        ) from exc


@router.get("", response_model=ConversationListOut)
async def get_conversations(
    actor: ListViewer,
    client_id: ClientIdQuery = None,
    preset: PresetQuery = None,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    outcome: OutcomeQuery = None,
    phone: Annotated[str | None, Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ConversationListOut:
    phone_hash_value = None
    if phone:
        # Looking up a person by phone is for Admin and Super Admin only.
        if not can_access(actor.user_type_code, Area.conversations):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Phone lookup is not available to your role",
            )
        phone_hash_value = _phone_hash_or_refuse(phone)
    client, period = await _client_and_period(actor, client_id, preset, start, end)
    return await list_conversations(
        client.id,
        period,
        outcome=outcome,
        phone_hash=phone_hash_value,
        limit=limit,
        offset=offset,
    )


@router.get("/patients", response_model=PatientGroupListOut)
async def get_patient_groups(
    actor: PatientViewer,
    client_id: ClientIdQuery = None,
    preset: PresetQuery = None,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PatientGroupListOut:
    client, period = await _client_and_period(actor, client_id, preset, start, end)
    return await patient_groups(client.id, period, limit=limit, offset=offset)


@router.get("/{session_id}", response_model=ConversationDetailOut)
async def get_conversation(
    session_id: UUID,
    request: Request,
    actor: PatientViewer,
    client_id: ClientIdQuery = None,
) -> ConversationDetailOut:
    client = await resolve_client_scope(actor, client_id)
    graph = getattr(request.app.state, "agent_graph", None)
    if graph is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The conversation store is not ready. Try again shortly.",
        )

    # The audit row comes first. If it cannot be written, nothing below runs.
    try:
        await require_admin_audit(
            "conversation.view",
            request=request,
            resource_type="chat_session",
            resource_id=str(session_id),
            client_id=client.id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The conversation cannot be opened: the audit log is unavailable",
        ) from exc
    try:
        detail = await conversation_detail(client.id, session_id, graph)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="This conversation could not be read. Try again shortly.",
        ) from exc
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return detail
