from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.db.session import get_db_connection

SHARE_CONTACT = "share_contact"
CALLBACK = "callback"
PURPOSES = (SHARE_CONTACT, CALLBACK)

_TEXT_COLUMNS = "purpose, version, body, approved_at, created_at"


@dataclass(frozen=True)
class ConsentText:
    purpose: str
    version: int
    body: str
    approved_at: datetime | None
    created_at: datetime

    @property
    def approved(self) -> bool:
        return self.approved_at is not None


async def get_consent_text(purpose: str, version: int) -> ConsentText | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {_TEXT_COLUMNS} FROM consent_texts WHERE purpose = %s AND version = %s",
                (purpose, version),
            )
            row = await cur.fetchone()
    return ConsentText(*row) if row else None


async def get_latest_approved_text(purpose: str) -> ConsentText | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT {_TEXT_COLUMNS} FROM consent_texts
                WHERE purpose = %s AND approved_at IS NOT NULL
                ORDER BY version DESC LIMIT 1
                """,
                (purpose,),
            )
            row = await cur.fetchone()
    return ConsentText(*row) if row else None


async def list_consent_texts() -> list[ConsentText]:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {_TEXT_COLUMNS} FROM consent_texts ORDER BY purpose, version"
            )
            rows = await cur.fetchall()
    return [ConsentText(*row) for row in rows]


async def approve_consent_text(purpose: str, version: int) -> ConsentText | None:
    """Sets approved_at once. A second approval keeps the first date."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                UPDATE consent_texts SET approved_at = COALESCE(approved_at, NOW())
                WHERE purpose = %s AND version = %s
                RETURNING {_TEXT_COLUMNS}
                """,
                (purpose, version),
            )
            row = await cur.fetchone()
        await conn.commit()
    return ConsentText(*row) if row else None


async def insert_consent_record(
    *,
    session_id: UUID,
    purpose: str,
    version: int,
    text_snapshot: str,
    text_sha256: str,
) -> None:
    """One record per session, purpose and version. Repeating the same acceptance is a no-op."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO consent_records
                    (session_id, purpose, text_version, text_snapshot, text_sha256)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (session_id, purpose, text_version) DO NOTHING
                """,
                (session_id, purpose, version, text_snapshot, text_sha256),
            )
        await conn.commit()


async def list_consent_records(session_id: UUID) -> list[tuple]:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT purpose, text_version, text_snapshot, text_sha256, accepted_at
                FROM consent_records WHERE session_id = %s ORDER BY accepted_at
                """,
                (session_id,),
            )
            return await cur.fetchall()
