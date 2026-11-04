from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.db.session import get_db_connection

STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"

# Same limit as the CHECK in migration 0006.
MAX_DEFAULT_QUESTIONS = 4


@dataclass
class FaqRecord:
    id: UUID
    client_id: UUID
    question: str
    answer: str
    status: str
    # 1-4 when this FAQ is one of the client's default questions, else None.
    default_order: int | None
    source_id: UUID | None
    source_filename: str | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime

    @property
    def source_removed(self) -> bool:
        """Generated from a document that has since been deleted."""
        return self.source_id is None and self.source_filename is not None


# Column order must match _row_to_record.
_FAQ_COLUMNS = """
    id, client_id, question, answer, status, default_order, source_id, source_filename,
    created_by, created_at, updated_at
"""


def _row_to_record(row: tuple) -> FaqRecord:
    return FaqRecord(
        id=row[0],
        client_id=row[1],
        question=row[2],
        answer=row[3],
        status=row[4],
        default_order=row[5],
        source_id=row[6],
        source_filename=row[7],
        created_by=row[8],
        created_at=row[9],
        updated_at=row[10],
    )


async def insert_faq(
    *,
    client_id: UUID,
    question: str,
    answer: str,
    created_by: UUID | None,
    status: str = STATUS_DRAFT,
    source_id: UUID | None = None,
    source_filename: str | None = None,
) -> FaqRecord:
    """Raises UniqueViolation if the client already has this question (ignoring case)."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                INSERT INTO faqs
                    (client_id, question, answer, status, source_id, source_filename, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING {_FAQ_COLUMNS}
                """,
                (client_id, question, answer, status, source_id, source_filename, created_by),
            )
            row = await cur.fetchone()
        await conn.commit()
    return _row_to_record(row)


async def get_faq(faq_id: UUID, *, client_id: UUID) -> FaqRecord | None:
    """None when the FAQ does not exist or belongs to another client."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {_FAQ_COLUMNS} FROM faqs WHERE id = %s AND client_id = %s",
                (faq_id, client_id),
            )
            row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_record(row)


async def list_faqs(client_id: UUID) -> list[FaqRecord]:
    """Default questions first, in their order; then the rest, newest first."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT {_FAQ_COLUMNS} FROM faqs
                WHERE client_id = %s
                ORDER BY default_order NULLS LAST, created_at DESC, question
                """,
                (client_id,),
            )
            rows = await cur.fetchall()
    return [_row_to_record(row) for row in rows]


async def list_default_questions(client_id: UUID) -> list[str]:
    """The client's default questions, in order (for the public widget)."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT question FROM faqs
                WHERE client_id = %s AND default_order IS NOT NULL
                ORDER BY default_order
                """,
                (client_id,),
            )
            rows = await cur.fetchall()
    return [row[0] for row in rows]


async def existing_questions(client_id: UUID) -> set[str]:
    """Lower-cased questions the client already has, to skip repeats when generating."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT LOWER(question) FROM faqs WHERE client_id = %s", (client_id,))
            rows = await cur.fetchall()
    return {row[0] for row in rows}


async def update_faq(
    faq_id: UUID,
    *,
    question: str | None = None,
    answer: str | None = None,
    status: str | None = None,
) -> FaqRecord | None:
    """Changes the given fields. Moving back to draft also drops it from the defaults.
    Raises UniqueViolation when the new question clashes with another FAQ of the client."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                UPDATE faqs
                SET question = COALESCE(%(question)s, question),
                    answer = COALESCE(%(answer)s, answer),
                    status = COALESCE(%(status)s, status),
                    default_order = CASE WHEN %(status)s = 'draft' THEN NULL
                                         ELSE default_order END,
                    updated_at = NOW()
                WHERE id = %(id)s
                RETURNING {_FAQ_COLUMNS}
                """,
                {"question": question, "answer": answer, "status": status, "id": faq_id},
            )
            row = await cur.fetchone()
        await conn.commit()
    if row is None:
        return None
    return _row_to_record(row)


async def approve_all_drafts(client_id: UUID) -> list[FaqRecord]:
    """Approves every draft of the client; returns the FAQs that changed."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                UPDATE faqs SET status = 'approved', updated_at = NOW()
                WHERE client_id = %s AND status = 'draft'
                RETURNING {_FAQ_COLUMNS}
                """,
                (client_id,),
            )
            rows = await cur.fetchall()
        await conn.commit()
    return [_row_to_record(row) for row in rows]


async def set_default_questions(client_id: UUID, faq_ids: list[UUID]) -> list[FaqRecord]:
    """Makes exactly these FAQs the client's defaults, in this order (up to 4).

    Every id must be an approved FAQ of the client, else nothing changes and ValueError is
    raised. Returns the defaults in order."""
    if len(faq_ids) > MAX_DEFAULT_QUESTIONS:
        raise ValueError(f"At most {MAX_DEFAULT_QUESTIONS} default questions")
    if len(set(faq_ids)) != len(faq_ids):
        raise ValueError("A question can only be a default once")

    async with get_db_connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT COUNT(*) FROM faqs
                    WHERE client_id = %s AND status = 'approved' AND id = ANY(%s)
                    """,
                    (client_id, faq_ids),
                )
                (found,) = await cur.fetchone()
                if found != len(faq_ids):
                    raise ValueError("Only approved FAQs of this client can be defaults")

                await cur.execute(
                    "UPDATE faqs SET default_order = NULL WHERE client_id = %s"
                    " AND default_order IS NOT NULL",
                    (client_id,),
                )
                for position, faq_id in enumerate(faq_ids, start=1):
                    await cur.execute(
                        "UPDATE faqs SET default_order = %s, updated_at = NOW() WHERE id = %s",
                        (position, faq_id),
                    )
                await cur.execute(
                    f"""
                    SELECT {_FAQ_COLUMNS} FROM faqs
                    WHERE client_id = %s AND default_order IS NOT NULL
                    ORDER BY default_order
                    """,
                    (client_id,),
                )
                rows = await cur.fetchall()
    return [_row_to_record(row) for row in rows]


async def delete_faq(faq_id: UUID) -> bool:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM faqs WHERE id = %s", (faq_id,))
            deleted = cur.rowcount > 0
        await conn.commit()
    return deleted
