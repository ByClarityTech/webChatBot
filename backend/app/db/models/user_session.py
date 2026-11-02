from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.db.models.user import USER_COLUMNS, UserRecord, row_to_user
from app.db.session import get_db_connection


@dataclass
class ActiveSession:
    session_id: UUID
    expires_at: datetime
    last_seen_at: datetime
    user: UserRecord


async def insert_user_session(
    *,
    user_id: UUID,
    token_hash: str,
    expires_at: datetime,
    client_ip: str | None,
    user_agent: str | None,
) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO user_sessions (user_id, token_hash, expires_at, client_ip, user_agent)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (user_id, token_hash, expires_at, client_ip, (user_agent or "")[:500] or None),
            )
        await conn.commit()


async def get_active_session(token_hash: str) -> ActiveSession | None:
    """Session that is not revoked and not expired, with its user (active or not)."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT s.id, s.expires_at, s.last_seen_at, {USER_COLUMNS}
                FROM user_sessions s
                JOIN users u ON u.id = s.user_id
                JOIN user_types t ON t.id = u.user_type_id
                LEFT JOIN clients c ON c.id = u.client_id
                WHERE s.token_hash = %s
                  AND s.revoked_at IS NULL
                  AND s.expires_at > NOW()
                """,
                (token_hash,),
            )
            row = await cur.fetchone()
    if row is None:
        return None
    return ActiveSession(
        session_id=row[0],
        expires_at=row[1],
        last_seen_at=row[2],
        user=row_to_user(row[3:]),
    )


async def touch_user_session(session_id: UUID) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE user_sessions SET last_seen_at = NOW() WHERE id = %s",
                (session_id,),
            )
        await conn.commit()


async def revoke_user_session(token_hash: str) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE user_sessions SET revoked_at = NOW()
                WHERE token_hash = %s AND revoked_at IS NULL
                """,
                (token_hash,),
            )
        await conn.commit()


async def revoke_all_user_sessions(user_id: UUID, *, keep_token_hash: str | None = None) -> None:
    """Sign a user out everywhere, optionally keeping the session making the request."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE user_sessions SET revoked_at = NOW()
                WHERE user_id = %s AND revoked_at IS NULL
                  AND (%s::text IS NULL OR token_hash <> %s)
                """,
                (user_id, keep_token_hash, keep_token_hash),
            )
        await conn.commit()
