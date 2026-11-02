from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AdminDashboardSummaryResponse(BaseModel):
    document_count: int
    bookings_today: int
    calendar_connected: bool


class AdminAuditLogEntry(BaseModel):
    id: int
    action: str
    resource_type: str | None = None
    resource_id: str | None = None
    client_ip: str | None = None
    created_at: datetime
    # Who acted (null for entries written before sign-in existed) and which client it concerns
    # (null for platform-level entries).
    user_id: UUID | None = None
    user_email: str | None = None
    client_id: UUID | None = None


class AdminAuditLogListResponse(BaseModel):
    entries: list[AdminAuditLogEntry]
    limit: int = Field(ge=1, le=200)
