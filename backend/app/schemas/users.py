from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


class ClientCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=2, max_length=100, pattern=SLUG_PATTERN)


class ClientUpdateRequest(BaseModel):
    # Slug is fixed after creation: it will identify the client in widget embeds.
    name: str | None = Field(default=None, min_length=1, max_length=255)
    status: Literal["active", "suspended"] | None = None


class ClientResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    status: str
    user_count: int
    created_at: datetime
    updated_at: datetime


class ClientListResponse(BaseModel):
    clients: list[ClientResponse]


class UserCreateRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=255)
    # Omit to send an invite email instead: the user sets their own password from the link.
    # With a password, the legacy path runs: the user must change it at first sign-in.
    password: str | None = Field(default=None, min_length=1, max_length=1024)
    # Super Admins are created only with scripts/create_super_admin.py.
    user_type: Literal["admin", "support"]
    # Required for Super Admins; Admins always create users in their own client.
    client_id: UUID | None = None


class UserUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=1, max_length=1024)


class UserResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    user_type: str
    client_id: UUID | None
    client_name: str | None
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime


class UserListResponse(BaseModel):
    users: list[UserResponse]


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=1024)
    new_password: str = Field(..., min_length=1, max_length=1024)
