from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.db.session import get_db_connection


@dataclass
class OAuthPendingFlow:
    code_verifier: str
    # Who started the connect flow, and for which client (migration 0007).
    client_id: UUID | None
    user_id: UUID | None


async def save_oauth_pending_state(
    state: str,
    code_verifier: str,
    *,
    client_id: UUID | None = None,
    user_id: UUID | None = None,
) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO oauth_pending_states
                    (state, code_verifier, expires_at, client_id, user_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (state) DO UPDATE
                SET code_verifier = EXCLUDED.code_verifier,
                    expires_at = EXCLUDED.expires_at,
                    client_id = EXCLUDED.client_id,
                    user_id = EXCLUDED.user_id
                """,
                (state, code_verifier, expires_at, client_id, user_id),
            )
        await conn.commit()


async def consume_oauth_pending_flow(state: str) -> OAuthPendingFlow | None:
    """Single use: the row is deleted as it is read. None when unknown or expired."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                DELETE FROM oauth_pending_states
                WHERE state = %s AND expires_at > NOW()
                RETURNING code_verifier, client_id, user_id
                """,
                (state,),
            )
            row = await cur.fetchone()
        await conn.commit()
    if row is None:
        return None
    return OAuthPendingFlow(code_verifier=row[0], client_id=row[1], user_id=row[2])


async def cleanup_expired_oauth_pending_states() -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM oauth_pending_states WHERE expires_at <= NOW()")
        await conn.commit()
