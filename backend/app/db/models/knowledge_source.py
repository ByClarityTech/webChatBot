from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.db.session import get_db_connection

KIND_FILE = "file"

STATUS_PROCESSING = "processing"
STATUS_READY = "ready"
STATUS_FAILED = "failed"


@dataclass
class KnowledgeSourceRecord:
    id: UUID
    client_id: UUID
    kind: str
    filename: str
    file_type: str
    size_bytes: int
    content_hash: str
    status: str
    chunk_count: int
    error: str | None
    uploaded_by: UUID | None
    created_at: datetime
    updated_at: datetime


# Column order must match _row_to_record.
_SOURCE_COLUMNS = """
    id, client_id, kind, filename, file_type, size_bytes, content_hash, status,
    chunk_count, error, uploaded_by, created_at, updated_at
"""


def _row_to_record(row: tuple) -> KnowledgeSourceRecord:
    return KnowledgeSourceRecord(
        id=row[0],
        client_id=row[1],
        kind=row[2],
        filename=row[3],
        file_type=row[4],
        size_bytes=int(row[5]),
        content_hash=row[6].strip(),
        status=row[7],
        chunk_count=int(row[8]),
        error=row[9],
        uploaded_by=row[10],
        created_at=row[11],
        updated_at=row[12],
    )


async def insert_knowledge_source(
    *,
    client_id: UUID,
    filename: str,
    file_type: str,
    size_bytes: int,
    content_hash: str,
    uploaded_by: UUID | None,
    kind: str = KIND_FILE,
) -> KnowledgeSourceRecord:
    """New source in 'processing'. Raises UniqueViolation if the client already has this file."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                INSERT INTO knowledge_sources
                    (client_id, kind, filename, file_type, size_bytes, content_hash, uploaded_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING {_SOURCE_COLUMNS}
                """,
                (client_id, kind, filename, file_type, size_bytes, content_hash, uploaded_by),
            )
            row = await cur.fetchone()
        await conn.commit()
    return _row_to_record(row)


async def get_knowledge_source(
    source_id: UUID, *, client_id: UUID | None = None
) -> KnowledgeSourceRecord | None:
    """With client_id, a source of another client is treated as missing."""
    query = f"SELECT {_SOURCE_COLUMNS} FROM knowledge_sources WHERE id = %s"
    params: list = [source_id]
    if client_id is not None:
        query += " AND client_id = %s"
        params.append(client_id)
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(query, params)
            row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_record(row)


async def get_knowledge_source_by_hash(
    client_id: UUID, content_hash: str
) -> KnowledgeSourceRecord | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT {_SOURCE_COLUMNS} FROM knowledge_sources
                WHERE client_id = %s AND content_hash = %s
                """,
                (client_id, content_hash),
            )
            row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_record(row)


async def list_knowledge_sources(client_id: UUID) -> list[KnowledgeSourceRecord]:
    """Newest first."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT {_SOURCE_COLUMNS} FROM knowledge_sources
                WHERE client_id = %s
                ORDER BY created_at DESC
                """,
                (client_id,),
            )
            rows = await cur.fetchall()
    return [_row_to_record(row) for row in rows]


async def update_knowledge_source_status(
    source_id: UUID,
    *,
    status: str,
    chunk_count: int | None = None,
    error: str | None = None,
) -> KnowledgeSourceRecord | None:
    """Sets the status; error is cleared unless given, chunk_count kept unless given."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                UPDATE knowledge_sources
                SET status = %s,
                    chunk_count = COALESCE(%s, chunk_count),
                    error = %s,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING {_SOURCE_COLUMNS}
                """,
                (status, chunk_count, error, source_id),
            )
            row = await cur.fetchone()
        await conn.commit()
    if row is None:
        return None
    return _row_to_record(row)


async def delete_knowledge_source(source_id: UUID) -> bool:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM knowledge_sources WHERE id = %s", (source_id,))
            deleted = cur.rowcount > 0
        await conn.commit()
    return deleted


async def count_ready_knowledge_sources(client_id: UUID | None = None) -> int:
    """Ready sources of one client, or of all clients when client_id is None."""
    query = "SELECT COUNT(*) FROM knowledge_sources WHERE status = %s"
    params: list = [STATUS_READY]
    if client_id is not None:
        query += " AND client_id = %s"
        params.append(client_id)
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(query, params)
            row = await cur.fetchone()
    return int(row[0])
