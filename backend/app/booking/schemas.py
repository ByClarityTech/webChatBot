from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.schemas.users import SLUG_PATTERN


class Slot(BaseModel):
    start: datetime
    end: datetime


class AvailabilityResponse(BaseModel):
    date: date
    timezone: str
    slots: list[Slot]


class OAuthStartResponse(BaseModel):
    auth_url: str


class OAuthStatusResponse(BaseModel):
    connected: bool
    # A connection exists but Google refused it (revoked or expired grant): connect again.
    needs_reconnect: bool = False
    calendar_id: str | None = None
    expires_at: datetime | None = None
    connected_email: str | None = None
    refresh_error: str | None = None


class OAuthCallbackResponse(BaseModel):
    status: str = "connected"
    calendar_id: str
    connected_email: str | None = None
    message: str = Field(default="Google Calendar connected successfully")


class OAuthDisconnectResponse(BaseModel):
    status: str = "disconnected"
    message: str = Field(default="Google Calendar disconnected successfully")


class BookingCreateRequest(BaseModel):
    # Slug of the client whose widget this is (the embed URL's ?client=).
    client: str = Field(..., min_length=2, max_length=100, pattern=SLUG_PATTERN)
    name: str = Field(..., min_length=1, max_length=255)
    phone: str = Field(..., min_length=7, max_length=50)
    email: EmailStr
    slot_start: datetime
    # The chat this booking came from; must belong to the same client.
    thread_id: UUID | None = None


class BookingResponse(BaseModel):
    id: UUID
    status: str
    slot_start: datetime
    slot_end: datetime
    message: str = Field(default="Booking confirmed. Check your email for the calendar invite.")


class BookingDetailResponse(BaseModel):
    id: UUID
    status: str
    slot_start: datetime
    slot_end: datetime
    created_at: datetime


class AdminBookingSummary(BaseModel):
    id: UUID
    name: str
    email: EmailStr
    slot_start: datetime
    slot_end: datetime
    status: str


class AdminBookingListResponse(BaseModel):
    bookings: list[AdminBookingSummary]
    total: int
    offset: int
    limit: int


class AdminBookingDetailResponse(BaseModel):
    id: UUID
    name: str
    phone: str
    email: EmailStr
    slot_start: datetime
    slot_end: datetime
    status: str
    created_at: datetime
    google_event_id: str | None = None


class AdminBookingCancelResponse(BaseModel):
    id: UUID
    status: str
    message: str = Field(default="Booking cancelled successfully")
