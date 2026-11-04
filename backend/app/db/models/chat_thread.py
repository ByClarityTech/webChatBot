from uuid import UUID

from app.db.session import get_db_connection


async def get_chat_thread_client(thread_id: UUID) -> UUID | None:
    """The client a thread belongs to, or None if there is no such thread."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT client_id FROM chat_threads WHERE thread_id = %s", (thread_id,)
            )
            row = await cur.fetchone()
    return row[0] if row else None


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
