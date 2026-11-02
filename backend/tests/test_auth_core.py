"""Password hashing, session tokens, the login lockout and the login rules."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from argon2 import PasswordHasher
from auth_helpers import make_user

from app.core import rate_limit
from app.core.rate_limit import FailedAttemptLimiter, login_failure_key
from app.core.roles import UserType
from app.core.security import (
    hash_password,
    hash_session_token,
    new_session_token,
    password_needs_rehash,
    password_problem,
    verify_password,
)
from app.services import auth as auth_service

# ---------------------------------------------------------------- passwords and tokens


def test_hash_is_argon2id_and_verifies():
    hashed = hash_password("Correct-horse-9")
    assert hashed.startswith("$argon2id$")
    assert verify_password("Correct-horse-9", hashed)
    assert not verify_password("correct-horse-9", hashed)


def test_verify_password_rejects_garbage_hash():
    assert verify_password("anything", "not-a-hash") is False


def test_weak_old_hash_needs_rehash():
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash("pw-123456789")
    assert password_needs_rehash(weak)
    assert not password_needs_rehash(hash_password("pw-123456789"))


@pytest.mark.parametrize(
    ("password", "problem"),
    [
        ("short", "at least 12"),
        ("x" * 129, "at most 128"),
        ("Jane@Example.com", "same as the email"),
        ("long-enough-password", None),
    ],
)
def test_password_rules(password, problem):
    result = password_problem(password, email="jane@example.com")
    if problem is None:
        assert result is None
    else:
        assert problem in result


def test_session_tokens_are_random_and_hashed():
    token = new_session_token()
    assert len(token) >= 40
    assert token != new_session_token()
    assert len(hash_session_token(token)) == 64
    assert hash_session_token(token) == hash_session_token(token)
    assert hash_session_token(token) != token


# ---------------------------------------------------------------- lockout limiter


def test_limiter_locks_after_max_failures_and_reports_retry_after(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock[0])
    limiter = FailedAttemptLimiter(max_failures=3, window_seconds=60)

    for _ in range(2):
        limiter.record_failure("k")
    assert limiter.retry_after("k") is None
    limiter.record_failure("k")
    assert 0 < limiter.retry_after("k") <= 61

    clock[0] += 61
    assert limiter.retry_after("k") is None, "failures expire after the window"


def test_limiter_clear_and_keys_are_independent():
    limiter = FailedAttemptLimiter(max_failures=1, window_seconds=60)
    limiter.record_failure("a")
    assert limiter.retry_after("a") is not None
    assert limiter.retry_after("b") is None
    limiter.clear("a")
    assert limiter.retry_after("a") is None


def test_login_failure_key_ignores_email_case_and_spaces():
    assert login_failure_key(" Jane@Example.COM ", "1.2.3.4") == "jane@example.com|1.2.3.4"


# ---------------------------------------------------------------- login rules


@pytest.mark.parametrize(
    ("user_type", "is_active", "client_status", "allowed"),
    [
        (UserType.super_admin, True, None, True),
        (UserType.super_admin, False, None, False),
        (UserType.admin, True, "active", True),
        (UserType.admin, True, "suspended", False),
        (UserType.support, False, "active", False),
    ],
)
def test_is_user_allowed(user_type, is_active, client_status, allowed):
    user = make_user(user_type)
    user.is_active = is_active
    if user_type != UserType.super_admin:
        user.client_status = client_status
    assert auth_service.is_user_allowed(user) is allowed


async def test_authenticate_unknown_email_still_spends_a_password_check():
    with (
        patch("app.services.auth.get_user_by_email", new_callable=AsyncMock, return_value=None),
        patch("app.services.auth.burn_password_check") as burn,
    ):
        assert await auth_service.authenticate("nobody@example.com", "pw") is None
    burn.assert_called_once_with("pw")


async def test_authenticate_wrong_password_and_disabled_user_return_none():
    user = make_user(UserType.admin)
    user.password_hash = hash_password("Right-password-1")
    record = AsyncMock()
    with (
        patch("app.services.auth.get_user_by_email", new_callable=AsyncMock, return_value=user),
        patch("app.services.auth.record_successful_login", record),
    ):
        assert await auth_service.authenticate(user.email, "wrong") is None
        user.client_status = "suspended"
        assert await auth_service.authenticate(user.email, "Right-password-1") is None
    record.assert_not_awaited()


async def test_authenticate_success_records_login_and_upgrades_weak_hash():
    user = make_user(UserType.admin)
    user.password_hash = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(
        "Right-password-1"
    )
    record = AsyncMock()
    with (
        patch("app.services.auth.get_user_by_email", new_callable=AsyncMock, return_value=user),
        patch("app.services.auth.record_successful_login", record),
    ):
        assert await auth_service.authenticate(user.email, "Right-password-1") is user

    new_hash = record.await_args.kwargs["new_password_hash"]
    assert new_hash.startswith("$argon2id$") and not password_needs_rehash(new_hash)


async def test_start_session_stores_only_the_token_hash():
    insert = AsyncMock()
    user = make_user(UserType.admin)
    with patch("app.services.auth.insert_user_session", insert):
        token = await auth_service.start_session(user, client_ip="1.2.3.4", user_agent="ua")

    kwargs = insert.await_args.kwargs
    assert kwargs["token_hash"] == hash_session_token(token)
    assert token not in str(kwargs)
    hours = (kwargs["expires_at"] - datetime.now(UTC)) / timedelta(hours=1)
    assert 7.9 < hours <= 8.0


@pytest.mark.parametrize(("seconds_ago", "touched"), [(5, False), (120, True)])
async def test_resolve_session_refreshes_last_seen_at_most_once_a_minute(seconds_ago, touched):
    session = MagicMock()
    session.user = make_user(UserType.admin)
    session.last_seen_at = datetime.now(UTC) - timedelta(seconds=seconds_ago)
    touch = AsyncMock()
    with (
        patch("app.services.auth.get_active_session", new_callable=AsyncMock, return_value=session),
        patch("app.services.auth.touch_user_session", touch),
    ):
        assert await auth_service.resolve_session("token") is session.user
    assert touch.await_count == (1 if touched else 0)


async def test_resolve_session_rejects_disabled_user():
    session = MagicMock()
    session.user = make_user(UserType.support)
    session.user.is_active = False
    with patch(
        "app.services.auth.get_active_session", new_callable=AsyncMock, return_value=session
    ):
        assert await auth_service.resolve_session("token") is None
