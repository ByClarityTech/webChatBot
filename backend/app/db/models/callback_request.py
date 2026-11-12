"""Callback requests: the Callbacks queue (FR-23, FR-37).

One open request per conversation. The phone number is not copied here; it is read from the
conversation's contact record, which is encrypted at rest.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.db.session import get_db_connection

STATUS_OPEN = "open"
STATUS_HANDLED = "handled"

SOURCE_NO_STAFF = "No staff online"
SOURCE_TIMEOUT = "Not answered in 3 minutes"
SOURCE_IN_CHAT = "Callback requested in chat"


@dataclass(frozen=True)
class CallbackRequest:
    id: UUID
    client_id: UUID
    session_id: UUID | None
    status: str
    source: str
    created_at: datetime
    handled_by: UUID | None
    handled_at: datetime | None


_COLUMNS = "id, client_id, session_id, status, source, created_at, handled_by, handled_at"


async def ensure_open_callback(
    *, client_id: UUID, session_id: UUID | None, source: str, now: datetime
) -> CallbackRequest | None:
    """Opens a callback for this conversation unless one is already open. None if one was."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            if session_id is None:
                await cur.execute(
                    f"""
                    INSERT INTO callback_requests (client_id, session_id, source, created_at)
                    VALUES (%s, NULL, %s, %s) RETURNING {_COLUMNS}
                    """,
                    (client_id, source, now),
                )
            else:
                await cur.execute(
                    f"""
                    INSERT INTO callback_requests (client_id, session_id, source, created_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (session_id) WHERE status = 'open' AND session_id IS NOT NULL
                    DO NOTHING
                    RETURNING {_COLUMNS}
                    """,
                    (client_id, session_id, source, now),
                )
            row = await cur.fetchone()
        await conn.commit()
    return CallbackRequest(*row) if row else None


async def list_callbacks(client_id: UUID, status: str = STATUS_OPEN) -> list[CallbackRequest]:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT {_COLUMNS} FROM callback_requests
                WHERE client_id = %s AND status = %s ORDER BY created_at
                """,
                (client_id, status),
            )
            rows = await cur.fetchall()
    return [CallbackRequest(*row) for row in rows]


async def get_callback(callback_id: UUID, client_id: UUID) -> CallbackRequest | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {_COLUMNS} FROM callback_requests WHERE id = %s AND client_id = %s",
                (callback_id, client_id),
            )
            row = await cur.fetchone()
    return CallbackRequest(*row) if row else None


async def mark_callback_handled(
    callback_id: UUID, *, client_id: UUID, user_id: UUID, now: datetime
) -> bool:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE callback_requests SET status = 'handled', handled_by = %s, handled_at = %s
                WHERE id = %s AND client_id = %s AND status = 'open'
                RETURNING id
                """,
                (user_id, now, callback_id, client_id),
            )
            row = await cur.fetchone()
        await conn.commit()
    return row is not None
