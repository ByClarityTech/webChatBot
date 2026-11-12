from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.db.session import get_db_connection

STATUS_OPEN = "open"
STATUS_DRAFTED = "drafted"
STATUS_DISMISSED = "dismissed"

_GAP_COLUMNS = """
    id, client_id, query_hash, query_normalized, sample_query, reason, top_score, ask_count,
    status, drafted_faq_id, first_seen_at, last_seen_at
"""


@dataclass(frozen=True)
class KnowledgeGap:
    id: UUID
    client_id: UUID
    query_hash: str
    query_normalized: str
    sample_query: str
    reason: str
    top_score: float | None
    ask_count: int
    status: str
    drafted_faq_id: UUID | None
    first_seen_at: datetime
    last_seen_at: datetime


def _row(row: tuple) -> KnowledgeGap:
    return KnowledgeGap(*row)


async def upsert_gap(
    *,
    client_id: UUID,
    query_hash: str,
    query_normalized: str,
    sample_query: str,
    reason: str,
    top_score: float | None,
) -> tuple[UUID, int]:
    """Records one more ask of this question. Returns (gap id, ask count).

    A repeat keeps the first reason and sample, and keeps the status, so a dismissed question
    stays dismissed. The score is the latest one, for calibration.
    """
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO knowledge_gaps
                    (client_id, query_hash, query_normalized, sample_query, reason, top_score)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (client_id, query_hash) DO UPDATE SET
                    ask_count = knowledge_gaps.ask_count + 1,
                    last_seen_at = NOW(),
                    top_score = COALESCE(EXCLUDED.top_score, knowledge_gaps.top_score)
                RETURNING id, ask_count
                """,
                (client_id, query_hash, query_normalized, sample_query, reason, top_score),
            )
            row = await cur.fetchone()
        await conn.commit()
    return row[0], int(row[1])


async def get_gap(gap_id: UUID, client_id: UUID) -> KnowledgeGap | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {_GAP_COLUMNS} FROM knowledge_gaps WHERE id = %s AND client_id = %s",
                (gap_id, client_id),
            )
            row = await cur.fetchone()
    return _row(row) if row else None


async def list_open_gaps(client_id: UUID, limit: int) -> list[KnowledgeGap]:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT {_GAP_COLUMNS} FROM knowledge_gaps
                WHERE client_id = %s AND status = 'open'
                ORDER BY ask_count DESC, last_seen_at DESC
                LIMIT %s
                """,
                (client_id, limit),
            )
            rows = await cur.fetchall()
    return [_row(row) for row in rows]


async def mark_gap_drafted(gap_id: UUID, faq_id: UUID) -> bool:
    """Only an open gap can become drafted. False when it was already handled."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE knowledge_gaps SET status = 'drafted', drafted_faq_id = %s
                WHERE id = %s AND status = 'open'
                RETURNING id
                """,
                (faq_id, gap_id),
            )
            row = await cur.fetchone()
        await conn.commit()
    return row is not None


async def mark_gap_dismissed(gap_id: UUID, client_id: UUID) -> bool:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE knowledge_gaps SET status = 'dismissed'
                WHERE id = %s AND client_id = %s AND status = 'open'
                RETURNING id
                """,
                (gap_id, client_id),
            )
            row = await cur.fetchone()
        await conn.commit()
    return row is not None
