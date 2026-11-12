"""Live handover to staff: requests, acceptance, messages, endings, and the 3-minute timeout.

State lives in live_handovers (see db/models/live_handover.py). Each change is published on the
broker so open streams update. Escalation rules:

- No staff member has beaten in the last 60 seconds: the visitor goes straight to a callback.
- Nobody accepts within 3 minutes: the handover times out and a callback is opened.
- Staff who accept are the only ones who can reply to that visitor.
"""

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from uuid import UUID

from fastapi import HTTPException, status

from app.db.models.callback_request import SOURCE_NO_STAFF, SOURCE_TIMEOUT, ensure_open_callback
from app.db.models.chat_session import ChatSession, touch_session
from app.db.models.live_handover import (
    LiveHandover,
    LiveMessage,
    OpenHandoverExists,
    accept_handover,
    create_handover,
    end_handover,
    end_handovers_of_ended_sessions,
    expire_overdue_pending,
    get_handover,
    get_open_handover_for_session,
    insert_message,
    list_messages,
)
from app.db.models.staff_presence import online_staff_count, prune_presence
from app.db.models.user import UserRecord
from app.services.broker import broker, staff_channel, visitor_channel
from app.services.chat_sessions import utc_now

logger = logging.getLogger(__name__)

HANDOVER_WINDOW = timedelta(minutes=3)
MAX_MESSAGE_CHARS = 2000

END_STAFF = "staff_ended"
END_VISITOR = "visitor_left"

NOT_FOUND = "That conversation was not found."
ALREADY_TAKEN = "This request was already taken or has timed out."
NOT_YOURS = "Another team member is handling this conversation."
NOT_ACCEPTED = "Accept this conversation before replying."
OPEN_ALREADY = "A live chat is already open for this conversation."


@dataclass(frozen=True)
class RequestOutcome:
    """Either a pending handover with the visitor's stream token, or a callback instead."""

    status: str  # "pending" or "callback"
    handover_id: UUID | None = None
    token: str | None = None
    expires_at: datetime | None = None


def token_hash(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def handover_summary(handover: LiveHandover) -> dict:
    return {
        "handover_id": str(handover.id),
        "session_id": str(handover.session_id),
        "status": handover.status,
        "requested_at": handover.requested_at.isoformat(),
        "expires_at": handover.expires_at.isoformat(),
    }


def message_payload(message: LiveMessage) -> dict:
    return {
        "id": str(message.id),
        "handover_id": str(message.handover_id),
        "sender": message.sender,
        "body": message.body,
        "created_at": message.created_at.isoformat(),
    }


async def request_live(session: ChatSession, client_id: UUID) -> RequestOutcome:
    """The visitor asks for a person. Pending with a token, or a callback when nobody is online."""
    now = utc_now()
    if await get_open_handover_for_session(session.session_id) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=OPEN_ALREADY)

    if await online_staff_count(client_id, now) == 0:
        await ensure_open_callback(
            client_id=client_id, session_id=session.session_id, source=SOURCE_NO_STAFF, now=now
        )
        return RequestOutcome(status="callback")

    token = secrets.token_urlsafe(32)
    try:
        handover = await create_handover(
            session_id=session.session_id,
            client_id=client_id,
            requested_at=now,
            expires_at=now + HANDOVER_WINDOW,
            stream_token_hash=token_hash(token),
        )
    except OpenHandoverExists as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=OPEN_ALREADY) from exc
    broker.publish(staff_channel(client_id), "handover_requested", handover_summary(handover))
    return RequestOutcome(
        status="pending",
        handover_id=handover.id,
        token=token,
        expires_at=handover.expires_at,
    )


async def check_visitor_token(handover_id: UUID, token: str | None) -> LiveHandover:
    """The handover, if the token matches. The stream token never travels in a URL."""
    handover = await get_handover(handover_id)
    if handover is None or not token or token_hash(token) != handover.stream_token_hash:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return handover


async def accept(handover_id: UUID, client_id: UUID, user: UserRecord) -> LiveHandover:
    handover = await get_handover(handover_id)
    if handover is None or handover.client_id != client_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    now = utc_now()
    if not await accept_handover(handover_id, client_id=client_id, user_id=user.id, now=now):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ALREADY_TAKEN)
    accepted = await get_handover(handover_id)
    payload = handover_summary(accepted)
    payload["accepted_by"] = str(user.id)
    broker.publish(visitor_channel(handover_id), "handover_accepted", payload)
    broker.publish(staff_channel(client_id), "handover_accepted", payload)
    return accepted


async def _require_accepter(handover_id: UUID, client_id: UUID, user: UserRecord) -> LiveHandover:
    handover = await get_handover(handover_id)
    if handover is None or handover.client_id != client_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    if handover.status != "accepted":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NOT_ACCEPTED)
    if handover.accepted_by != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NOT_YOURS)
    return handover


def _clean_body(raw: str) -> str:
    body = (raw or "").strip()
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Write a message first")
    return body[:MAX_MESSAGE_CHARS]


async def staff_reply(
    handover_id: UUID, client_id: UUID, user: UserRecord, raw_body: str
) -> LiveMessage:
    handover = await _require_accepter(handover_id, client_id, user)
    message = await insert_message(
        handover_id=handover.id,
        client_id=client_id,
        sender="staff",
        author_user_id=user.id,
        body=_clean_body(raw_body),
    )
    now = utc_now()
    await touch_session(handover.session_id, now)
    payload = message_payload(message)
    broker.publish(visitor_channel(handover.id), "agent_message", payload)
    broker.publish(staff_channel(client_id), "agent_message", payload)
    return message


async def visitor_message(handover: LiveHandover, raw_body: str) -> LiveMessage:
    """A visitor's message while a handover is open. It is stored, never sent to the model."""
    message = await insert_message(
        handover_id=handover.id,
        client_id=handover.client_id,
        sender="visitor",
        author_user_id=None,
        body=_clean_body(raw_body),
    )
    await touch_session(handover.session_id, utc_now())
    broker.publish(staff_channel(handover.client_id), "visitor_message", message_payload(message))
    return message


async def end(handover_id: UUID, client_id: UUID, *, reason: str) -> None:
    handover = await get_handover(handover_id)
    if handover is None or handover.client_id != client_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    if await end_handover(handover_id, reason=reason, now=utc_now()):
        payload = {"handover_id": str(handover_id), "reason": reason}
        broker.publish(visitor_channel(handover_id), "handover_ended", payload)
        broker.publish(staff_channel(client_id), "handover_ended", payload)


async def visitor_ends(handover: LiveHandover) -> None:
    if await end_handover(handover.id, reason=END_VISITOR, now=utc_now()):
        payload = {"handover_id": str(handover.id), "reason": END_VISITOR}
        broker.publish(visitor_channel(handover.id), "handover_ended", payload)
        broker.publish(staff_channel(handover.client_id), "handover_ended", payload)


async def visitor_snapshot(handover: LiveHandover) -> dict:
    """What a (re)connecting visitor needs to draw the chat right away."""
    messages = await list_messages(handover.id)
    current = await get_handover(handover.id)
    return {
        "handover": handover_summary(current or handover),
        "messages": [message_payload(message) for message in messages],
    }


async def sweep(now: datetime | None = None) -> None:
    """Times out overdue requests into callbacks, and ends handovers of ended conversations."""
    now = now or utc_now()
    for handover in await expire_overdue_pending(now):
        payload = {"handover_id": str(handover.id), "reason": "timed_out"}
        broker.publish(visitor_channel(handover.id), "handover_timeout", payload)
        broker.publish(staff_channel(handover.client_id), "handover_timeout", payload)
        await ensure_open_callback(
            client_id=handover.client_id,
            session_id=handover.session_id,
            source=SOURCE_TIMEOUT,
            now=now,
        )
    for handover in await end_handovers_of_ended_sessions(now):
        payload = {"handover_id": str(handover.id), "reason": "session_ended"}
        broker.publish(visitor_channel(handover.id), "handover_ended", payload)
        broker.publish(staff_channel(handover.client_id), "handover_ended", payload)
    await prune_presence(now)


async def run_sweeper(interval_seconds: float = 15.0) -> None:
    """Background loop started by the app. A failed pass is logged and the loop carries on."""
    import asyncio

    while True:
        try:
            await sweep()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the loop must survive a database hiccup
            logger.error("Handover sweep failed (error=%s)", type(exc).__name__)
        await asyncio.sleep(interval_seconds)
