from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.db.session import get_db_connection


@dataclass
class ClientRecord:
    id: UUID
    name: str
    slug: str
    status: str
    created_at: datetime
    updated_at: datetime
    user_count: int = 0


_CLIENT_COLUMNS = """
    c.id, c.name, c.slug, c.status, c.created_at, c.updated_at,
    (SELECT COUNT(*) FROM users u WHERE u.client_id = c.id)
"""


def _row_to_record(row: tuple) -> ClientRecord:
    return ClientRecord(
        id=row[0],
        name=row[1],
        slug=row[2],
        status=row[3],
        created_at=row[4],
        updated_at=row[5],
        user_count=int(row[6]),
    )


async def insert_client(*, name: str, slug: str) -> ClientRecord:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO clients (name, slug) VALUES (%s, %s) RETURNING id",
                (name, slug),
            )
            row = await cur.fetchone()
        await conn.commit()
    record = await get_client(row[0])
    if record is None:
        raise RuntimeError("Failed to insert client")
    return record


async def get_client(client_id: UUID) -> ClientRecord | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {_CLIENT_COLUMNS} FROM clients c WHERE c.id = %s", (client_id,)
            )
            row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_record(row)


async def list_clients() -> list[ClientRecord]:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"SELECT {_CLIENT_COLUMNS} FROM clients c ORDER BY c.name")
            rows = await cur.fetchall()
    return [_row_to_record(row) for row in rows]


async def update_client(
    client_id: UUID,
    *,
    name: str | None = None,
    status: str | None = None,
) -> ClientRecord | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE clients
                SET name = COALESCE(%s, name),
                    status = COALESCE(%s, status),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (name, status, client_id),
            )
        await conn.commit()
    return await get_client(client_id)
