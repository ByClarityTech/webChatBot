"""Sign test clients in as a given role without a database (overrides get_current_user)."""

from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI

from app.api.deps.auth import get_current_user
from app.core.roles import USER_TYPE_IDS, UserType
from app.db.models.user import UserRecord


def make_user(user_type: UserType = UserType.admin, *, email: str | None = None) -> UserRecord:
    is_super_admin = user_type == UserType.super_admin
    return UserRecord(
        id=uuid4(),
        client_id=None if is_super_admin else uuid4(),
        user_type_id=USER_TYPE_IDS[user_type],
        user_type_code=user_type.value,
        email=email or f"{user_type.value}@example.com",
        password_hash="not-used",
        full_name=user_type.value.replace("_", " ").title(),
        is_active=True,
        last_login_at=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        client_name=None if is_super_admin else "Example Client",
        client_slug=None if is_super_admin else "example",
        client_status=None if is_super_admin else "active",
    )


@contextmanager
def signed_in(app: FastAPI, user_type: UserType = UserType.admin):
    user = make_user(user_type)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield user
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@contextmanager
def signed_out(app: FastAPI):
    """No session: get_current_user runs for real and finds no cookie (401)."""
    app.dependency_overrides.pop(get_current_user, None)
    yield
