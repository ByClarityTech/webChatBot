from datetime import datetime
from uuid import UUID

from app.db.session import get_db_connection

INVITE = "invite"
PASSWORD_RESET = "password_reset"


async def replace_invite_token(
    *, user_id: UUID, token_hash: str, purpose: str, expires_at: datetime
) -> None:
    """Store a new token. Older unused tokens of the same purpose are spent in one transaction."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE invite_tokens
                SET used_at = NOW()
                WHERE user_id = %s AND purpose = %s AND used_at IS NULL
                """,
                (user_id, purpose),
            )
            await cur.execute(
                """
                INSERT INTO invite_tokens (user_id, token_hash, purpose, expires_at)
                VALUES (%s, %s, %s, %s)
                """,
                (user_id, token_hash, purpose, expires_at),
            )
        await conn.commit()


async def find_unspent_token_user(token_hash: str, purpose: str) -> UUID | None:
    """Read only: the user a live token belongs to. Nothing is marked used."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT user_id FROM invite_tokens
                WHERE token_hash = %s AND purpose = %s
                  AND used_at IS NULL AND expires_at > NOW()
                """,
                (token_hash, purpose),
            )
            row = await cur.fetchone()
    return row[0] if row else None


async def spend_invite_token(token_hash: str, purpose: str) -> UUID | None:
    """Single use: marks the token used and returns its user, or None if unknown, spent,
    expired, or of another purpose. The UPDATE is one statement, so two requests cannot
    both spend the same token."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE invite_tokens
                SET used_at = NOW()
                WHERE token_hash = %s AND purpose = %s
                  AND used_at IS NULL AND expires_at > NOW()
                RETURNING user_id
                """,
                (token_hash, purpose),
            )
            row = await cur.fetchone()
        await conn.commit()
    return row[0] if row else None
