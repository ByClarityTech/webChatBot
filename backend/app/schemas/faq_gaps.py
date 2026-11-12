from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class FaqGapItem(BaseModel):
    id: UUID
    # The visitor's wording, shown to the admin. Never written to server logs.
    question: str
    reason: str
    top_score: float | None
    ask_count: int
    status: str
    drafted_faq_id: UUID | None
    last_seen_at: datetime


class FaqGapListResponse(BaseModel):
    gaps: list[FaqGapItem]
