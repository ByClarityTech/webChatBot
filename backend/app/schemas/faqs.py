from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

# Same limits as the faqs table (migration 0006); surrounding spaces are dropped first.
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Answer = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class FaqCreateRequest(BaseModel):
    question: Question
    answer: Answer


class FaqUpdateRequest(BaseModel):
    question: Question | None = None
    answer: Answer | None = None
    # approved: the chatbot answers from it; draft: taken out of the chatbot (and the defaults).
    status: Literal["draft", "approved"] | None = None


class FaqDefaultsRequest(BaseModel):
    """The client's default questions, in display order. An empty list clears them."""

    faq_ids: list[UUID] = Field(..., max_length=4)


class FaqResponse(BaseModel):
    id: UUID
    client_id: UUID
    question: str
    answer: str
    status: str
    is_default: bool
    default_order: int | None
    source_id: UUID | None
    source_filename: str | None
    # Generated from a document that has since been deleted.
    source_removed: bool
    created_at: datetime
    updated_at: datetime


class FaqListResponse(BaseModel):
    client_id: UUID
    faqs: list[FaqResponse]
    default_count: int
    max_defaults: int


class FaqApproveAllResponse(BaseModel):
    approved: int
    faqs: list[FaqResponse]


class FaqGenerateResponse(BaseModel):
    created: list[FaqResponse]
    # Suggested questions the client already has (not added again).
    skipped_existing: int
    documents_used: list[str]
    # Not read this time: over the text limit per run, or no readable text.
    documents_skipped: list[str]
    # Some document text was cut to fit the limit per run.
    truncated: bool
