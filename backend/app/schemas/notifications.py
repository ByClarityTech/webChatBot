from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class NotificationRecipientCreate(BaseModel):
    email: EmailStr


class NotificationRecipientUpdate(BaseModel):
    enabled: bool


class NotificationRecipientResponse(BaseModel):
    id: UUID
    email: str
    enabled: bool
    created_at: datetime


class NotificationRecipientList(BaseModel):
    recipients: list[NotificationRecipientResponse]
    limit: int = Field(..., description="Most recipients a practice may list")
