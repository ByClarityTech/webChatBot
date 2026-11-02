from dataclasses import dataclass
from datetime import datetime

from app.db.session import get_db_connection


@dataclass
class GoogleTokenRecord:
    id: int
    access_token: str
    refresh_token: str
    token_expiry: datetime
    calendar_id: str
    connected_email: str | None
    created_at: datetime
    updated_at: datetime


async def get_google_token() -> GoogleTokenRecord | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, access_token, refresh_token, token_expiry,
                       calendar_id, connected_email, created_at, updated_at
                FROM google_tokens
                ORDER BY id DESC
                LIMIT 1
                """
            )
            row = await cur.fetchone()
    if row is None:
        return None
    return GoogleTokenRecord(
        id=row[0],
        access_token=row[1],
        refresh_token=row[2],
        token_expiry=row[3],
        calendar_id=row[4],
        connected_email=row[5],
        created_at=row[6],
        updated_at=row[7],
    )


async def upsert_google_token(
    *,
    access_token: str,
    refresh_token_encrypted: str,
    token_expiry: datetime,
    calendar_id: str,
    connected_email: str | None,
) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM google_tokens")
            await cur.execute(
                """
                INSERT INTO google_tokens (
                    access_token, refresh_token, token_expiry,
                    calendar_id, connected_email, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, NOW())
                """,
                (
                    access_token,
                    refresh_token_encrypted,
                    token_expiry,
                    calendar_id,
                    connected_email,
                ),
            )
        await conn.commit()


async def delete_google_tokens() -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM google_tokens")
        await conn.commit()


async def update_google_token_access(
    *,
    access_token: str,
    token_expiry: datetime,
) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE google_tokens
                SET access_token = %s, token_expiry = %s, updated_at = NOW()
                WHERE id = (SELECT id FROM google_tokens ORDER BY id DESC LIMIT 1)
                """,
                (access_token, token_expiry),
            )
        await conn.commit()
