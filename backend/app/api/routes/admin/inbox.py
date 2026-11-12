"""Staff inbox: queues, live conversations, replies, AI drafts, callbacks, and presence.

Access: Area.inbox (Support, Admin, Super Admin), scoped to the staff member's own client, or to
the client a Super Admin names. Viewing a conversation is audited. Saving a draft FAQ needs the
FAQ permission, so Support can answer chats but cannot change the knowledge base.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps.auth import require_access
from app.core.permissions import Area, can_access
from app.db.models.callback_request import (
    STATUS_OPEN,
    get_callback,
    list_callbacks,
    mark_callback_handled,
)
from app.db.models.chat_session import get_session, get_session_contact
from app.db.models.faq import STATUS_DRAFT, insert_faq
from app.db.models.live_handover import (
    STATUS_ACCEPTED,
    STATUS_PENDING,
    get_handover,
    list_messages,
    list_queue,
)
from app.db.models.staff_presence import heartbeat, online_staff_count
from app.db.models.user import UserRecord
from app.services import live_chat as live
from app.services.admin_audit import log_admin_action
from app.services.broker import broker, staff_channel, stream_frames
from app.services.chat_sessions import utc_now
from app.services.client_scope import resolve_client_scope
from app.services.contact_privacy import ContactStorageNotConfigured, decrypt_phone
from app.services.knowledge_gaps import DUPLICATE_QUESTION
from app.services.staff_drafts import draft_reply

router = APIRouter(prefix="/admin/inbox", tags=["admin-inbox"])
callbacks_router = APIRouter(prefix="/admin/callbacks", tags=["admin-inbox"])

InboxActor = Annotated[UserRecord, Depends(require_access(Area.inbox))]
ClientIdQuery = Annotated[UUID | None, Query()]
MAX_QUESTION_CHARS = 200


class ReplyRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=2000)


class PresenceRequest(BaseModel):
    tab_id: str = Field(..., min_length=8, max_length=64)


class SaveFaqRequest(BaseModel):
    answer: str = Field(..., min_length=1, max_length=2000)


def _seconds_left(expires_at: datetime) -> int:
    return max(0, int((expires_at - utc_now()).total_seconds()))


async def _contact_for(session_id: UUID | None) -> dict | None:
    if session_id is None:
        return None
    row = await get_session_contact(session_id)
    if row is None:
        return {"name": None, "phone": None}
    full_name, encrypted, _hash, is_current_patient = row
    try:
        phone = decrypt_phone(encrypted)
    except ContactStorageNotConfigured:
        phone = None
    return {"name": full_name, "phone": phone, "is_current_patient": is_current_patient}


async def _own_handover(handover_id: UUID, client_id: UUID):
    handover = await get_handover(handover_id)
    if handover is None or handover.client_id != client_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=live.NOT_FOUND)
    return handover


@router.get("/stream")
async def stream(
    request: Request,
    actor: InboxActor,
    client_id: ClientIdQuery = None,
) -> StreamingResponse:
    """Live events for this client's staff: requests, acceptances, timeouts, and messages."""
    client = await resolve_client_scope(actor, client_id)
    queue = broker.subscribe(staff_channel(client.id))

    async def events():
        try:
            async for frame in stream_frames(queue):
                yield frame
        finally:
            broker.unsubscribe(staff_channel(client.id), queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("")
async def queues(actor: InboxActor, client_id: ClientIdQuery = None) -> dict:
    """Pending, active, and callback queues, plus how many staff are online."""
    client = await resolve_client_scope(actor, client_id)
    now = utc_now()
    pending = await list_queue(client.id, (STATUS_PENDING,))
    active = await list_queue(client.id, (STATUS_ACCEPTED,))
    callbacks = await list_callbacks(client.id, STATUS_OPEN)
    return {
        "online": await online_staff_count(client.id, now),
        "pending": [
            {
                "id": str(h.id),
                "session_id": str(h.session_id),
                "requested_at": h.requested_at.isoformat(),
                "seconds_left": _seconds_left(h.expires_at),
                "contact": await _contact_for(h.session_id),
            }
            for h in pending
        ],
        "active": [
            {
                "id": str(h.id),
                "session_id": str(h.session_id),
                "accepted_by": str(h.accepted_by) if h.accepted_by else None,
                "accepted_at": h.accepted_at.isoformat() if h.accepted_at else None,
            }
            for h in active
        ],
        "callbacks": [
            {
                "id": str(c.id),
                "source": c.source,
                "created_at": c.created_at.isoformat(),
                "session_id": str(c.session_id) if c.session_id else None,
                # Link to the conversation (FR-37 View Chat).
                "view_chat": f"/admin/inbox?conversation={c.session_id}" if c.session_id else None,
                "contact": await _contact_for(c.session_id),
            }
            for c in callbacks
        ],
    }


@router.get("/{handover_id}")
async def conversation(
    handover_id: UUID,
    request: Request,
    actor: InboxActor,
    client_id: ClientIdQuery = None,
) -> dict:
    """The transcript of one live conversation. Every view is audited."""
    client = await resolve_client_scope(actor, client_id)
    handover = await _own_handover(handover_id, client.id)
    session = await get_session(handover.session_id)
    messages = await list_messages(handover.id)
    await log_admin_action(
        "inbox.view",
        request=request,
        resource_type="live_handover",
        resource_id=str(handover.id),
        client_id=client.id,
    )
    return {
        "id": str(handover.id),
        "status": handover.status,
        "accepted_by": str(handover.accepted_by) if handover.accepted_by else None,
        "expires_at": handover.expires_at.isoformat(),
        "seconds_left": _seconds_left(handover.expires_at),
        "is_test_session": bool(session and session.is_test_session),
        "contact": await _contact_for(handover.session_id),
        "messages": [
            {
                "id": str(m.id),
                "sender": m.sender,
                "body": m.body,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
    }


@router.post("/{handover_id}/accept")
async def accept(
    handover_id: UUID,
    request: Request,
    actor: InboxActor,
    client_id: ClientIdQuery = None,
) -> dict:
    client = await resolve_client_scope(actor, client_id)
    accepted = await live.accept(handover_id, client.id, actor)
    await log_admin_action(
        "inbox.accept",
        request=request,
        resource_type="live_handover",
        resource_id=str(accepted.id),
        client_id=client.id,
    )
    return {"id": str(accepted.id), "status": accepted.status}


@router.post("/{handover_id}/messages", status_code=status.HTTP_201_CREATED)
async def reply(
    handover_id: UUID,
    body: ReplyRequest,
    actor: InboxActor,
    client_id: ClientIdQuery = None,
) -> dict:
    client = await resolve_client_scope(actor, client_id)
    message = await live.staff_reply(handover_id, client.id, actor, body.body)
    return {"id": str(message.id)}


@router.post("/{handover_id}/end", status_code=status.HTTP_204_NO_CONTENT)
async def end(
    handover_id: UUID,
    request: Request,
    actor: InboxActor,
    client_id: ClientIdQuery = None,
) -> None:
    client = await resolve_client_scope(actor, client_id)
    await live.end(handover_id, client.id, reason=live.END_STAFF)
    await log_admin_action(
        "inbox.end",
        request=request,
        resource_type="live_handover",
        resource_id=str(handover_id),
        client_id=client.id,
    )


@router.post("/{handover_id}/draft")
async def ai_draft(
    handover_id: UUID,
    request: Request,
    actor: InboxActor,
    client_id: ClientIdQuery = None,
) -> dict:
    """A suggested reply, for the agent to edit or discard. Not stored, not sent."""
    client = await resolve_client_scope(actor, client_id)
    handover = await _own_handover(handover_id, client.id)
    if handover.status != STATUS_ACCEPTED or handover.accepted_by != actor.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=live.NOT_YOURS)
    messages = await list_messages(handover.id)
    text = await draft_reply(messages)
    await log_admin_action(
        "inbox.draft",
        request=request,
        resource_type="live_handover",
        resource_id=str(handover.id),
        client_id=client.id,
    )
    return {"draft": text}


@router.post("/{handover_id}/save-faq", status_code=status.HTTP_201_CREATED)
async def save_as_draft_faq(
    handover_id: UUID,
    body: SaveFaqRequest,
    request: Request,
    actor: InboxActor,
    client_id: ClientIdQuery = None,
) -> dict:
    """Keeps this answer as a draft FAQ. An admin reviews and approves it before the bot uses it."""
    if not can_access(actor.user_type_code, Area.documents):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can save answers to the FAQs",
        )
    client = await resolve_client_scope(actor, client_id)
    handover = await _own_handover(handover_id, client.id)
    if handover.status != STATUS_ACCEPTED or handover.accepted_by != actor.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=live.NOT_YOURS)
    visitor_lines = [m for m in await list_messages(handover.id) if m.sender == "visitor"]
    if not visitor_lines:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No visitor question")
    question = " ".join(visitor_lines[-1].body.split())[:MAX_QUESTION_CHARS]
    try:
        faq = await insert_faq(
            client_id=client.id,
            question=question,
            answer=body.answer.strip(),
            created_by=actor.id,
            status=STATUS_DRAFT,
        )
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=DUPLICATE_QUESTION
        ) from exc
    await log_admin_action(
        "faq.draft_from_inbox",
        request=request,
        resource_type="faq",
        resource_id=str(faq.id),
        client_id=client.id,
    )
    return {"id": str(faq.id), "status": faq.status}


@router.post("/presence")
async def presence(
    body: PresenceRequest,
    request: Request,
    actor: InboxActor,
    client_id: ClientIdQuery = None,
) -> dict:
    """Heartbeat from each open inbox tab. Returns how many staff are online for the client."""
    client = await resolve_client_scope(actor, client_id)
    now = utc_now()
    await heartbeat(user_id=actor.id, tab_id=body.tab_id, client_id=client.id, now=now)
    return {"online": await online_staff_count(client.id, now)}


@callbacks_router.post("/{callback_id}/handle")
async def handle_callback(
    callback_id: UUID,
    request: Request,
    actor: InboxActor,
    client_id: ClientIdQuery = None,
) -> dict:
    client = await resolve_client_scope(actor, client_id)
    existing = await get_callback(callback_id, client.id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Callback not found")
    if not await mark_callback_handled(
        callback_id, client_id=client.id, user_id=actor.id, now=utc_now()
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already handled")
    await log_admin_action(
        "callback.handle",
        request=request,
        resource_type="callback_request",
        resource_id=str(callback_id),
        client_id=client.id,
    )
    return {"id": str(callback_id), "status": "handled"}
