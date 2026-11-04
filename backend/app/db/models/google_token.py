from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

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
    client_id: UUID | None = None
    # ok, or needs_reconnect after Google refused a refresh (revoked or expired grant).
    status: str = "ok"
    last_error: str | None = None


# ---------------------------------------------------------------- per client (migration 0007)

STATUS_OK = "ok"
STATUS_NEEDS_RECONNECT = "needs_reconnect"

_TOKEN_COLUMNS = """
    id, access_token, refresh_token, token_expiry, calendar_id, connected_email,
    created_at, updated_at, client_id, status, last_error
"""


def _row_to_token(row: tuple) -> GoogleTokenRecord:
    return GoogleTokenRecord(
        id=row[0],
        access_token=row[1],
        refresh_token=row[2],
        token_expiry=row[3],
        calendar_id=row[4],
        connected_email=row[5],
        created_at=row[6],
        updated_at=row[7],
        client_id=row[8],
        status=row[9],
        last_error=row[10],
    )


async def get_client_google_token(client_id: UUID) -> GoogleTokenRecord | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {_TOKEN_COLUMNS} FROM google_tokens WHERE client_id = %s", (client_id,)
            )
            row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_token(row)


async def save_client_google_token(
    client_id: UUID,
    *,
    access_token: str,
    refresh_token_encrypted: str,
    token_expiry: datetime,
    calendar_id: str,
    connected_email: str | None,
) -> GoogleTokenRecord:
    """The client's connection (replacing an earlier one), marked healthy."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                INSERT INTO google_tokens (
                    client_id, access_token, refresh_token, token_expiry, calendar_id,
                    connected_email, status, last_error, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'ok', NULL, NOW())
                ON CONFLICT (client_id) DO UPDATE SET
                    access_token = EXCLUDED.access_token,
                    refresh_token = EXCLUDED.refresh_token,
                    token_expiry = EXCLUDED.token_expiry,
                    calendar_id = EXCLUDED.calendar_id,
                    connected_email = EXCLUDED.connected_email,
                    status = 'ok',
                    last_error = NULL,
                    updated_at = NOW()
                RETURNING {_TOKEN_COLUMNS}
                """,
                (
                    client_id,
                    access_token,
                    refresh_token_encrypted,
                    token_expiry,
                    calendar_id,
                    connected_email,
                ),
            )
            row = await cur.fetchone()
        await conn.commit()
    return _row_to_token(row)


async def update_client_google_token_access(
    client_id: UUID, *, access_token: str, token_expiry: datetime
) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE google_tokens
                SET access_token = %s, token_expiry = %s, updated_at = NOW()
                WHERE client_id = %s
                """,
                (access_token, token_expiry, client_id),
            )
        await conn.commit()


async def mark_client_google_token_needs_reconnect(client_id: UUID, error: str) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE google_tokens
                SET status = 'needs_reconnect', last_error = %s, updated_at = NOW()
                WHERE client_id = %s
                """,
                (error[:1000], client_id),
            )
        await conn.commit()


async def delete_client_google_token(client_id: UUID) -> bool:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM google_tokens WHERE client_id = %s", (client_id,))
            deleted = cur.rowcount > 0
        await conn.commit()
    return deleted


async def count_healthy_google_connections() -> int:
    """Clients whose calendar is connected and not refused (Super Admin dashboard total)."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) FROM google_tokens WHERE client_id IS NOT NULL AND status = 'ok'"
            )
            row = await cur.fetchone()
    return int(row[0])
