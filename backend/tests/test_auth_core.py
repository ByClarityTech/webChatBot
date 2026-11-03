"""Password hashing, JWT access tokens, the login lockout and the login rules."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import jwt
import pytest
from argon2 import PasswordHasher
from auth_helpers import make_user

from app.core import rate_limit
from app.core.config import get_settings
from app.core.rate_limit import FailedAttemptLimiter, login_failure_key
from app.core.roles import UserType
from app.core.security import (
    JWT_AUDIENCE,
    JWT_ISSUER,
    TokenConfigError,
    TokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    password_needs_rehash,
    password_problem,
    token_cutoff_now,
    verify_password,
)
from app.services import auth as auth_service

SECRET = "s" * 64

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


# ---------------------------------------------------------------- JWT


def test_jwt_round_trip_has_expected_claims():
    user_id = uuid4()
    now = datetime(2026, 1, 15, 10, 0, 0, 750000, tzinfo=UTC)  # in the past: iat is checked
    token = create_access_token(user_id, secret=SECRET, ttl=timedelta(hours=24), now=now)
    assert jwt.get_unverified_header(token)["alg"] == "HS256"

    claims = jwt.decode(
        token, SECRET, algorithms=["HS256"], audience=JWT_AUDIENCE, options={"verify_exp": False}
    )
    assert claims["sub"] == str(user_id)
    assert claims["iss"] == JWT_ISSUER and claims["aud"] == JWT_AUDIENCE
    assert claims["iat"] == int(now.replace(microsecond=0).timestamp())
    assert claims["exp"] - claims["iat"] == 24 * 3600


def test_decode_accepts_a_fresh_token():
    user_id = uuid4()
    token = create_access_token(user_id, secret=SECRET, ttl=timedelta(hours=1))
    assert decode_access_token(token, secret=SECRET)["sub"] == str(user_id)


def _encode(claims: dict, *, secret: str = SECRET, algorithm: str = "HS256") -> str:
    now = int(datetime.now(UTC).timestamp())
    base = {
        "sub": str(uuid4()),
        "iat": now,
        "exp": now + 3600,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
    }
    return jwt.encode({**base, **claims}, secret, algorithm=algorithm)


@pytest.mark.parametrize(
    "token_factory",
    [
        pytest.param(
            lambda: _encode({"exp": int(datetime.now(UTC).timestamp()) - 1}), id="expired"
        ),
        pytest.param(lambda: _encode({}, secret="x" * 64), id="other-secret"),
        pytest.param(lambda: _encode({"iss": "someone-else"}), id="wrong-issuer"),
        pytest.param(lambda: _encode({"aud": "another-app"}), id="wrong-audience"),
        pytest.param(lambda: _encode({}, algorithm="HS512"), id="other-algorithm"),
        pytest.param(
            lambda: jwt.encode({"sub": "x", "exp": 9999999999}, None, algorithm="none"),
            id="alg-none",
        ),
        pytest.param(
            lambda: jwt.encode(
                {"sub": "x", "exp": 9999999999, "iss": JWT_ISSUER, "aud": JWT_AUDIENCE},
                SECRET,
                algorithm="HS256",
            ),
            id="missing-iat",
        ),
        pytest.param(lambda: "not.a.jwt", id="garbage"),
    ],
)
def test_decode_rejects_bad_tokens(token_factory):
    with pytest.raises(TokenError):
        decode_access_token(token_factory(), secret=SECRET)


@pytest.mark.parametrize("secret", ["", "too-short"])
def test_missing_or_short_secret_is_a_config_error(secret):
    with pytest.raises(TokenConfigError):
        create_access_token(uuid4(), secret=secret, ttl=timedelta(hours=1))
    with pytest.raises(TokenConfigError):
        decode_access_token("x.y.z", secret=secret)


def test_token_cutoff_is_whole_seconds_utc():
    cutoff = token_cutoff_now()
    assert cutoff.microsecond == 0 and cutoff.tzinfo is UTC


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


def _token_for(user, *, issued: datetime | None = None) -> str:
    return create_access_token(
        user.id, secret=get_settings().jwt_secret, ttl=timedelta(hours=24), now=issued
    )


async def _resolve(user, token):
    with patch(
        "app.services.auth.get_user_by_id", new_callable=AsyncMock, return_value=user
    ) as get:
        result = await auth_service.resolve_token(token)
    return result, get


def test_issue_token_uses_configured_secret_and_lifetime():
    user = make_user(UserType.admin)
    claims = decode_access_token(auth_service.issue_token(user), secret=get_settings().jwt_secret)
    assert claims["sub"] == str(user.id)
    assert claims["exp"] - claims["iat"] == 24 * 3600


async def test_resolve_token_loads_the_user_fresh_from_the_database():
    user = make_user(UserType.admin)
    result, get = await _resolve(user, _token_for(user))
    assert result is user
    get.assert_awaited_once_with(user.id)


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(lambda u: setattr(u, "is_active", False), id="deactivated"),
        pytest.param(lambda u: setattr(u, "client_status", "suspended"), id="client-suspended"),
    ],
)
async def test_resolve_token_refuses_disabled_users(change):
    user = make_user(UserType.support)
    token = _token_for(user)
    change(user)
    assert (await _resolve(user, token))[0] is None


async def test_resolve_token_refuses_deleted_users():
    user = make_user(UserType.admin)
    assert (await _resolve(None, _token_for(user)))[0] is None


async def test_tokens_issued_before_the_cutoff_are_refused():
    user = make_user(UserType.admin)
    issued = datetime.now(UTC) - timedelta(minutes=5)
    old_token = _token_for(user, issued=issued)
    user.tokens_valid_after = issued + timedelta(seconds=1)
    assert (await _resolve(user, old_token))[0] is None
    assert (await _resolve(user, _token_for(user)))[0] is user, "a newer token still works"


async def test_token_issued_in_the_cutoff_second_is_accepted():
    """Own password change: the cutoff and the fresh token share a second; the fresh one works."""
    user = make_user(UserType.admin)
    cutoff = token_cutoff_now()
    user.tokens_valid_after = cutoff
    assert (await _resolve(user, _token_for(user, issued=cutoff)))[0] is user


async def test_resolve_token_rejects_tampered_or_odd_tokens():
    user = make_user(UserType.admin)
    header, payload, signature = _token_for(user).split(".")
    swapped = "A" if signature[-1] != "A" else "B"
    tampered = f"{header}.{payload}.{signature[:-1]}{swapped}"
    assert (await _resolve(user, tampered))[0] is None

    not_a_uuid = jwt.encode(
        {"sub": "admin", "iat": 1, "exp": 9999999999, "iss": JWT_ISSUER, "aud": JWT_AUDIENCE},
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    result, get = await _resolve(user, not_a_uuid)
    assert result is None
    get.assert_not_awaited()
