from uuid import UUID

from app.db.session import get_db_connection


async def claim_chat_thread(thread_id: UUID, client_id: UUID) -> UUID:
    """Bind a new thread to client_id, or return the client an existing thread belongs to.

    The caller compares the result with client_id: a different value means the thread was
    started on another client's widget and must not be continued.
    """
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            # The no-op update makes RETURNING give the existing row on conflict.
            await cur.execute(
                """
                INSERT INTO chat_threads (thread_id, client_id) VALUES (%s, %s)
                ON CONFLICT (thread_id) DO UPDATE SET thread_id = EXCLUDED.thread_id
                RETURNING client_id
                """,
                (thread_id, client_id),
            )
            row = await cur.fetchone()
        await conn.commit()
    return row[0]
