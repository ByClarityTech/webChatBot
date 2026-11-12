from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.db.session import get_db_connection

_SESSION_COLUMNS = """
    session_id, thread_id, client_id, type, status, end_reason, consent_given,
    consent_text_version, is_test_session, started_at, last_activity_at, closed_at
"""


@dataclass(frozen=True)
class ChatSession:
    session_id: UUID
    thread_id: UUID
    client_id: UUID
    type: str
    status: str
    end_reason: str | None
    consent_given: bool
    consent_text_version: int | None
    is_test_session: bool
    started_at: datetime
    last_activity_at: datetime
    closed_at: datetime | None


def _row(row: tuple) -> ChatSession:
    return ChatSession(*row)


async def start_session(
    *,
    session_id: UUID,
    thread_id: UUID,
    client_id: UUID,
    is_test_session: bool,
    now: datetime,
) -> None:
    """Binds the thread to the client and opens its session, in one transaction."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO chat_threads (thread_id, client_id) VALUES (%s, %s)",
                (thread_id, client_id),
            )
            await cur.execute(
                """
                INSERT INTO chat_sessions
                    (session_id, thread_id, client_id, is_test_session, started_at,
                     last_activity_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (session_id, thread_id, client_id, is_test_session, now, now),
            )
        await conn.commit()


async def get_session(session_id: UUID) -> ChatSession | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {_SESSION_COLUMNS} FROM chat_sessions WHERE session_id = %s",
                (session_id,),
            )
            row = await cur.fetchone()
    return _row(row) if row else None


async def get_active_session_for_thread(thread_id: UUID) -> ChatSession | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT {_SESSION_COLUMNS} FROM chat_sessions
                WHERE thread_id = %s AND status = 'active'
                """,
                (thread_id,),
            )
            row = await cur.fetchone()
    return _row(row) if row else None


async def touch_session(session_id: UUID, now: datetime) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE chat_sessions SET last_activity_at = %s
                WHERE session_id = %s AND status = 'active'
                """,
                (now, session_id),
            )
        await conn.commit()


async def end_session(session_id: UUID, *, reason: str, closed_at: datetime) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE chat_sessions SET status = 'ended', end_reason = %s, closed_at = %s
                WHERE session_id = %s AND status = 'active'
                """,
                (reason, closed_at, session_id),
            )
        await conn.commit()


async def mark_session_known(session_id: UUID, consent_text_version: int) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE chat_sessions
                SET type = 'known', consent_given = TRUE, consent_text_version = %s
                WHERE session_id = %s
                """,
                (consent_text_version, session_id),
            )
        await conn.commit()


async def upsert_session_contact(
    *,
    session_id: UUID,
    full_name: str,
    phone_encrypted: bytes,
    phone_hash: str,
    is_current_patient: bool,
) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO session_contacts
                    (session_id, full_name, phone_encrypted, phone_hash, is_current_patient)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (session_id) DO UPDATE SET
                    full_name = EXCLUDED.full_name,
                    phone_encrypted = EXCLUDED.phone_encrypted,
                    phone_hash = EXCLUDED.phone_hash,
                    is_current_patient = EXCLUDED.is_current_patient,
                    updated_at = NOW()
                """,
                (session_id, full_name, phone_encrypted, phone_hash, is_current_patient),
            )
        await conn.commit()


async def get_session_contact(session_id: UUID) -> tuple | None:
    """(full_name, phone_encrypted, phone_hash, is_current_patient), or None."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT full_name, phone_encrypted, phone_hash, is_current_patient
                FROM session_contacts WHERE session_id = %s
                """,
                (session_id,),
            )
            return await cur.fetchone()


async def count_sessions(client_id: UUID, *, include_test: bool) -> int:
    """Metrics leave out test sessions unless asked otherwise (PRD R-04 item 4)."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT COUNT(*) FROM chat_sessions
                WHERE client_id = %s AND (%s OR NOT is_test_session)
                """,
                (client_id, include_test),
            )
            row = await cur.fetchone()
    return int(row[0])
