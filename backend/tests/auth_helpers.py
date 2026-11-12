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


def make_client_record(slug: str = "example", *, client_id=None, status: str = "active"):
    """A ClientRecord for tests that look clients up by id or slug."""
    from app.db.models.client import ClientRecord

    return ClientRecord(
        id=client_id or uuid4(),
        name=slug.replace("-", " ").title(),
        slug=slug,
        status=status,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def fake_session_resolver():
    """Stands in for services.chat_sessions.resolve_for_message, without the database.

    Mirrors its rules: a thread continues the same conversation (same session id) for the
    client that opened it; any other thread, or none, starts a new conversation (reset is True
    when a thread was given).
    """
    from app.services.chat_sessions import SessionHandle

    threads: dict = {}

    async def resolve(thread_id, client_id, *, is_test_session):
        if thread_id is not None and thread_id in threads and threads[thread_id][0] == client_id:
            _, session_id = threads[thread_id]
            return SessionHandle(thread_id, session_id, False, is_test_session)
        new_thread, session_id = uuid4(), uuid4()
        threads[new_thread] = (client_id, session_id)
        return SessionHandle(new_thread, session_id, thread_id is not None, is_test_session)

    return resolve
