"""Live handovers to staff, and the messages in them.

Every state change is one conditional UPDATE. Two staff members accepting the same request, or
a late accept after the 3-minute timeout, can therefore never both succeed.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg

from app.db.session import get_db_connection

STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_TIMED_OUT = "timed_out"
STATUS_ENDED = "ended"

_HANDOVER_COLUMNS = """
    id, session_id, client_id, status, requested_at, expires_at, accepted_by, accepted_at,
    ended_at, end_reason, stream_token_hash
"""


@dataclass(frozen=True)
class LiveHandover:
    id: UUID
    session_id: UUID
    client_id: UUID
    status: str
    requested_at: datetime
    expires_at: datetime
    accepted_by: UUID | None
    accepted_at: datetime | None
    ended_at: datetime | None
    end_reason: str | None
    stream_token_hash: str


@dataclass(frozen=True)
class LiveMessage:
    id: UUID
    handover_id: UUID
    sender: str
    author_user_id: UUID | None
    body: str
    created_at: datetime


class OpenHandoverExists(Exception):
    """The conversation already has a pending or accepted handover."""


def _handover(row: tuple) -> LiveHandover:
    return LiveHandover(*row)


async def create_handover(
    *,
    session_id: UUID,
    client_id: UUID,
    requested_at: datetime,
    expires_at: datetime,
    stream_token_hash: str,
) -> LiveHandover:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute(
                    f"""
                    INSERT INTO live_handovers
                        (session_id, client_id, requested_at, expires_at, stream_token_hash)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING {_HANDOVER_COLUMNS}
                    """,
                    (session_id, client_id, requested_at, expires_at, stream_token_hash),
                )
            except psycopg.errors.UniqueViolation as exc:
                await conn.rollback()
                raise OpenHandoverExists from exc
            row = await cur.fetchone()
        await conn.commit()
    return _handover(row)


async def get_handover(handover_id: UUID) -> LiveHandover | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {_HANDOVER_COLUMNS} FROM live_handovers WHERE id = %s", (handover_id,)
            )
            row = await cur.fetchone()
    return _handover(row) if row else None


async def get_open_handover_for_session(session_id: UUID) -> LiveHandover | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT {_HANDOVER_COLUMNS} FROM live_handovers
                WHERE session_id = %s AND status IN ('pending', 'accepted')
                """,
                (session_id,),
            )
            row = await cur.fetchone()
    return _handover(row) if row else None


async def accept_handover(
    handover_id: UUID, *, client_id: UUID, user_id: UUID, now: datetime
) -> bool:
    """True only for the one caller who moves a pending, unexpired handover to accepted."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE live_handovers
                SET status = 'accepted', accepted_by = %s, accepted_at = %s
                WHERE id = %s AND client_id = %s AND status = 'pending' AND expires_at > %s
                RETURNING id
                """,
                (user_id, now, handover_id, client_id, now),
            )
            row = await cur.fetchone()
        await conn.commit()
    return row is not None


async def end_handover(handover_id: UUID, *, reason: str, now: datetime) -> bool:
    """Ends a pending or accepted handover. False when it had already ended."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE live_handovers SET status = 'ended', end_reason = %s, ended_at = %s
                WHERE id = %s AND status IN ('pending', 'accepted')
                RETURNING id
                """,
                (reason, now, handover_id),
            )
            row = await cur.fetchone()
        await conn.commit()
    return row is not None


async def expire_overdue_pending(now: datetime) -> list[LiveHandover]:
    """Moves every pending handover past its deadline to timed_out. Returns the ones it moved."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                UPDATE live_handovers SET status = 'timed_out', end_reason = 'timed_out',
                                          ended_at = %s
                WHERE status = 'pending' AND expires_at <= %s
                RETURNING {_HANDOVER_COLUMNS}
                """,
                (now, now),
            )
            rows = await cur.fetchall()
        await conn.commit()
    return [_handover(row) for row in rows]


async def end_handovers_of_ended_sessions(now: datetime) -> list[LiveHandover]:
    """Ends open handovers whose conversation has already ended (closed or idle)."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                UPDATE live_handovers SET status = 'ended', end_reason = 'session_ended',
                                          ended_at = %s
                WHERE status IN ('pending', 'accepted')
                  AND session_id IN (SELECT session_id FROM chat_sessions WHERE status = 'ended')
                RETURNING {_HANDOVER_COLUMNS}
                """,
                (now,),
            )
            rows = await cur.fetchall()
        await conn.commit()
    return [_handover(row) for row in rows]


async def insert_message(
    *,
    handover_id: UUID,
    client_id: UUID,
    sender: str,
    author_user_id: UUID | None,
    body: str,
) -> LiveMessage:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO live_messages (handover_id, client_id, sender, author_user_id, body)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, handover_id, sender, author_user_id, body, created_at
                """,
                (handover_id, client_id, sender, author_user_id, body),
            )
            row = await cur.fetchone()
        await conn.commit()
    return LiveMessage(*row)


async def list_messages(handover_id: UUID) -> list[LiveMessage]:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, handover_id, sender, author_user_id, body, created_at
                FROM live_messages WHERE handover_id = %s ORDER BY created_at, id
                """,
                (handover_id,),
            )
            rows = await cur.fetchall()
    return [LiveMessage(*row) for row in rows]


async def list_queue(client_id: UUID, statuses: tuple[str, ...]) -> list[LiveHandover]:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT {_HANDOVER_COLUMNS} FROM live_handovers
                WHERE client_id = %s AND status = ANY(%s)
                ORDER BY requested_at
                """,
                (client_id, list(statuses)),
            )
            rows = await cur.fetchall()
    return [_handover(row) for row in rows]
