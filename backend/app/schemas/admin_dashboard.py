from datetime import datetime

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


class AdminAuditLogListResponse(BaseModel):
    entries: list[AdminAuditLogEntry]
    limit: int = Field(ge=1, le=200)
