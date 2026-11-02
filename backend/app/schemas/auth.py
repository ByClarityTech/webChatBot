from uuid import UUID

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    # Plain string: login only looks up what was stored. Format is validated when users are created.
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=1, max_length=1024)


class ClientSummary(BaseModel):
    id: UUID
    name: str
    slug: str


class CurrentUserResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    user_type: str
    client: ClientSummary | None = None


class LogoutResponse(BaseModel):
    ok: bool = True
