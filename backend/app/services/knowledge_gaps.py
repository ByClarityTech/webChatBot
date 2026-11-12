"""Questions the knowledge base could not answer: recorded, deduplicated, drafted, dismissed.

A gap is keyed by its normalized question. Asking it again raises ask_count instead of adding
a row. Server logs record the reason and the count only, never the question text, since it can
contain health details.
"""

import hashlib
import logging
from uuid import UUID

from fastapi import HTTPException, status

from app.core.safety import normalize
from app.db.models.faq import FaqRecord, existing_questions, insert_faq
from app.db.models.knowledge_gap import (
    STATUS_OPEN,
    KnowledgeGap,
    get_gap,
    list_open_gaps,
    mark_gap_dismissed,
    mark_gap_drafted,
    upsert_gap,
)
from app.db.models.user import UserRecord

logger = logging.getLogger(__name__)

MIN_WORDS = 3  # shorter messages ("hi", "thanks") are not questions worth tracking
MAX_QUESTION_CHARS = 200  # the faqs.question column limit
REASONS = ("no_match", "model_declined", "keyword_miss")

DUPLICATE_QUESTION = "This client already has an FAQ with this question"


def normalize_query(text: object) -> str:
    return normalize(text)


def query_hash(normalized: str) -> str:
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def is_loggable(normalized: str) -> bool:
    return len(normalized.split()) >= MIN_WORDS


async def record_gap(
    client_id: UUID, text: str, *, reason: str, top_score: float | None
) -> int | None:
    """Logs one ask. Returns the new ask count, or None when the message is not a question."""
    if reason not in REASONS:
        raise ValueError(f"Unknown gap reason: {reason}")
    normalized = normalize_query(text)
    if not is_loggable(normalized):
        return None
    sample = " ".join((text or "").split())[:MAX_QUESTION_CHARS]
    _, count = await upsert_gap(
        client_id=client_id,
        query_hash=query_hash(normalized),
        query_normalized=normalized,
        sample_query=sample,
        reason=reason,
        top_score=top_score,
    )
    logger.info("Knowledge gap recorded (reason=%s, ask_count=%d)", reason, count)
    return count


async def get_client_gap(client_id: UUID, gap_id: UUID) -> KnowledgeGap:
    gap = await get_gap(gap_id, client_id)
    if gap is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")
    return gap


async def top_open_gaps(client_id: UUID, limit: int = 20) -> list[KnowledgeGap]:
    return await list_open_gaps(client_id, max(1, min(limit, 100)))


async def draft_from_gap(client_id: UUID, gap_id: UUID, actor: UserRecord) -> FaqRecord:
    """A draft FAQ with the gap's question and no answer, for a person to write and approve.

    No answer is generated: a model could invent prices or clinical facts.
    """
    gap = await get_client_gap(client_id, gap_id)
    if gap.status != STATUS_OPEN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This question is already drafted or dismissed",
        )
    question = gap.sample_query[:MAX_QUESTION_CHARS]
    if question.lower() in await existing_questions(client_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DUPLICATE_QUESTION)
    faq = await insert_faq(
        client_id=client_id,
        question=question,
        answer="",
        created_by=actor.id,
    )
    if not await mark_gap_drafted(gap.id, faq.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This question was handled by someone else just now",
        )
    return faq


async def dismiss_gap(client_id: UUID, gap_id: UUID) -> None:
    gap = await get_client_gap(client_id, gap_id)
    if gap.status != STATUS_OPEN or not await mark_gap_dismissed(gap.id, client_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This question is already handled"
        )
