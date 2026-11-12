from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.users import SLUG_PATTERN


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    # Slug of the client whose widget this is (the embed URL's ?client=).
    client: str = Field(..., min_length=2, max_length=100, pattern=SLUG_PATTERN)
    thread_id: UUID | None = None
    # Honoured only from an authenticated admin session; ignored for public visitors.
    test_session: bool = False


class SourceCitation(BaseModel):
    filename: str
    excerpt: str
    score: float | None = None


class ChatResponse(BaseModel):
    reply: str
    thread_id: str
    sources: list[SourceCitation]
    action: str | None = None
    # Set when a deterministic safety reply was sent instead of a model answer.
    safety: Literal["urgent", "diagnosis"] | None = None
    session_id: str | None = None
    # True when an earlier conversation ended (30 minutes idle or closed) and this one started.
    session_reset: bool = False
