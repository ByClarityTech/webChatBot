from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.users import SLUG_PATTERN

ClientSlug = Field(..., min_length=2, max_length=100, pattern=SLUG_PATTERN)


class ConsentOfferResponse(BaseModel):
    purpose: str
    version: int
    text: str


class SessionStartRequest(BaseModel):
    client: str = ClientSlug
    test_session: bool = False


class SessionStartResponse(BaseModel):
    thread_id: str
    session_id: str


class SessionStatusResponse(BaseModel):
    active: bool
    session_id: str | None = None
    known: bool = False
    # The answer to "Are you a current patient?" if the session already has one, else null.
    current_patient: bool | None = None


class SessionCloseRequest(BaseModel):
    client: str = ClientSlug
    thread_id: UUID


class SessionCloseResponse(BaseModel):
    ended: bool


class ShareConsentRequest(BaseModel):
    client: str = ClientSlug
    thread_id: UUID
    session_id: UUID
    purpose: Literal["share_contact", "callback"] = "share_contact"
    text_version: int = Field(..., ge=1)
    full_name: str = Field(..., min_length=1, max_length=255)
    phone: str = Field(..., min_length=7, max_length=30)
    is_current_patient: bool
    # Must be true. The visitor ticks the box; an unticked box is never recorded as consent.
    consent_accepted: bool


class ShareConsentResponse(BaseModel):
    known: bool
    session_id: str


class ConsentTextItem(BaseModel):
    purpose: str
    version: int
    body: str
    approved_at: datetime | None
    created_at: datetime


class ConsentTextListResponse(BaseModel):
    texts: list[ConsentTextItem]
