"""Staff presence: one row per staff browser tab, refreshed by heartbeats.

A staff member is online for a client while any of their tabs beat within ONLINE_WINDOW. A
restart loses nothing here: the next heartbeat restores the row.
"""

from datetime import datetime, timedelta
from uuid import UUID

from app.db.session import get_db_connection

ONLINE_WINDOW = timedelta(seconds=60)
PRUNE_AFTER = timedelta(minutes=10)


async def heartbeat(*, user_id: UUID, tab_id: str, client_id: UUID, now: datetime) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO staff_presence (user_id, tab_id, client_id, last_seen_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, tab_id) DO UPDATE SET
                    client_id = EXCLUDED.client_id,
                    last_seen_at = EXCLUDED.last_seen_at
                """,
                (user_id, tab_id, client_id, now),
            )
        await conn.commit()


async def online_staff_count(client_id: UUID, now: datetime) -> int:
    """Distinct staff members with a recent beat for this client. Two tabs count once."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT COUNT(DISTINCT user_id) FROM staff_presence
                WHERE client_id = %s AND last_seen_at >= %s
                """,
                (client_id, now - ONLINE_WINDOW),
            )
            row = await cur.fetchone()
    return int(row[0])


async def prune_presence(now: datetime) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM staff_presence WHERE last_seen_at < %s", (now - PRUNE_AFTER,)
            )
        await conn.commit()
