from datetime import datetime, timedelta, timezone

from app.db.session import get_db_connection


async def save_oauth_pending_state(state: str, code_verifier: str) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO oauth_pending_states (state, code_verifier, expires_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (state) DO UPDATE
                SET code_verifier = EXCLUDED.code_verifier,
                    expires_at = EXCLUDED.expires_at
                """,
                (state, code_verifier, expires_at),
            )
        await conn.commit()


async def consume_oauth_pending_state(state: str) -> str | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                DELETE FROM oauth_pending_states
                WHERE state = %s AND expires_at > NOW()
                RETURNING code_verifier
                """,
                (state,),
            )
            row = await cur.fetchone()
        await conn.commit()
    if row is None:
        return None
    return row[0]


async def cleanup_expired_oauth_pending_states() -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM oauth_pending_states WHERE expires_at <= NOW()")
        await conn.commit()
