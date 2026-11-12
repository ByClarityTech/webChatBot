"""Conversation sessions: when a chat starts, continues, idles out, or is closed.

A session lasts until the widget is closed or the visitor has been idle for more than
IDLE_TIMEOUT. The server decides from last_activity_at. A message that arrives after the
timeout ends the old session as 'idle' and starts a new one, with a new thread.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.db.models.chat_session import (
    ChatSession,
    end_session,
    get_active_session_for_thread,
    start_session,
    touch_session,
)

IDLE_TIMEOUT = timedelta(minutes=30)


def utc_now() -> datetime:
    """The app's clock. Tests replace this to move time."""
    return datetime.now(UTC)


def is_idle(session: ChatSession, now: datetime) -> bool:
    """Idle for more than the timeout. Exactly 30 minutes still counts as active."""
    return now - session.last_activity_at > IDLE_TIMEOUT


@dataclass(frozen=True)
class SessionHandle:
    thread_id: UUID
    session_id: UUID
    # True when an earlier conversation of this visitor ended and a new one started.
    reset: bool
    is_test_session: bool


async def start_new_session(
    client_id: UUID, *, is_test_session: bool, reset: bool = False
) -> SessionHandle:
    now = utc_now()
    thread_id = uuid4()
    session_id = uuid4()
    await start_session(
        session_id=session_id,
        thread_id=thread_id,
        client_id=client_id,
        is_test_session=is_test_session,
        now=now,
    )
    return SessionHandle(thread_id, session_id, reset, is_test_session)


async def resolve_for_message(
    thread_id: UUID | None, client_id: UUID, *, is_test_session: bool
) -> SessionHandle:
    """The session a message belongs to. Continues an active one, else starts another."""
    if thread_id is None:
        return await start_new_session(client_id, is_test_session=is_test_session)

    now = utc_now()
    active = await get_active_session_for_thread(thread_id)
    if active is not None and active.client_id == client_id:
        if not is_idle(active, now):
            await touch_session(active.session_id, now)
            return SessionHandle(
                thread_id, active.session_id, reset=False, is_test_session=active.is_test_session
            )
        # Idle: end it where its activity stopped, not at the moment this message arrived.
        await end_session(
            active.session_id,
            reason="idle",
            closed_at=active.last_activity_at + IDLE_TIMEOUT,
        )
    # Ended, unknown, or another client's thread: a new conversation, shown as a reset.
    return await start_new_session(client_id, is_test_session=is_test_session, reset=True)


async def close_session(thread_id: UUID | None, client_id: UUID) -> bool:
    """Ends the visitor's active session as 'closed'. False when there was none to end."""
    if thread_id is None:
        return False
    active = await get_active_session_for_thread(thread_id)
    if active is None or active.client_id != client_id:
        return False
    now = utc_now()
    if is_idle(active, now):
        await end_session(
            active.session_id,
            reason="idle",
            closed_at=active.last_activity_at + IDLE_TIMEOUT,
        )
    else:
        await end_session(active.session_id, reason="closed", closed_at=now)
    return True


async def active_session_for(thread_id: UUID | None, client_id: UUID) -> ChatSession | None:
    """The thread's session if it is still open for this client. Does not change anything."""
    if thread_id is None:
        return None
    active = await get_active_session_for_thread(thread_id)
    if active is None or active.client_id != client_id:
        return None
    if is_idle(active, utc_now()):
        return None
    return active
