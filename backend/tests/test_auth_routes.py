"""/api/v1/auth: login, logout, me and own password change (service layer mocked)."""

from unittest.mock import AsyncMock, patch

import pytest
from auth_helpers import make_user
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import auth
from app.core.config import get_settings
from app.core.roles import UserType
from app.services.auth import issue_token
from app.services.user_management import InvalidInputError

COOKIE = "webchatbot_session"


@pytest.fixture
def auth_app():
    test_app = FastAPI()
    test_app.include_router(auth.router, prefix="/api/v1")
    return test_app


def _client(app, **kwargs):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", **kwargs)


@pytest.fixture
def production_settings(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _login(client, email="admin@example.com", password="pw"):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


# ---------------------------------------------------------------- login


async def test_login_sets_httponly_lax_cookie_and_returns_user(auth_app):
    user = make_user(UserType.admin)
    with (
        patch("app.api.routes.auth.authenticate", new_callable=AsyncMock, return_value=user),
        patch("app.api.routes.auth.issue_token", return_value="tok"),
    ):
        async with _client(auth_app) as client:
            response = await _login(client)

    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert f"{COOKIE}=tok" in cookie
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert "max-age=86400" in cookie, "24 hours"
    assert "secure" not in cookie, "not Secure outside production (local dev is plain http)"
    body = response.json()
    assert body["user_type"] == "admin" and body["client"]["slug"] == "example"


async def test_login_cookie_is_secure_in_production(auth_app, production_settings):
    with (
        patch("app.api.routes.auth.authenticate", new_callable=AsyncMock, return_value=make_user()),
        patch("app.api.routes.auth.issue_token", return_value="tok"),
    ):
        async with _client(auth_app) as client:
            response = await _login(client)

    assert "secure" in response.headers["set-cookie"].lower()


async def test_super_admin_login_has_no_client(auth_app):
    with (
        patch(
            "app.api.routes.auth.authenticate",
            new_callable=AsyncMock,
            return_value=make_user(UserType.super_admin),
        ),
        patch("app.api.routes.auth.issue_token", return_value="tok"),
    ):
        async with _client(auth_app) as client:
            response = await _login(client)

    assert response.json()["client"] is None


async def test_failed_login_is_401_with_generic_message_and_no_cookie(auth_app):
    with patch("app.api.routes.auth.authenticate", new_callable=AsyncMock, return_value=None):
        async with _client(auth_app) as client:
            response = await _login(client)

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"
    assert "set-cookie" not in response.headers


async def test_sixth_attempt_after_five_failures_is_429_even_with_right_password(auth_app):
    authenticate = AsyncMock(return_value=None)
    with (
        patch("app.api.routes.auth.authenticate", authenticate),
        patch("app.api.routes.auth.issue_token", return_value="tok"),
    ):
        async with _client(auth_app) as client:
            for _ in range(5):
                assert (await _login(client)).status_code == 401
            authenticate.return_value = make_user()
            locked = await _login(client)
            other = await _login(client, email="someone-else@example.com")

    assert locked.status_code == 429
    assert int(locked.headers["retry-after"]) > 0
    assert authenticate.await_count == 6, "a locked attempt never checks the password"
    assert other.status_code == 200, "the lock is per email"


async def test_success_clears_earlier_failures(auth_app):
    authenticate = AsyncMock(return_value=None)
    with (
        patch("app.api.routes.auth.authenticate", authenticate),
        patch("app.api.routes.auth.issue_token", return_value="tok"),
    ):
        async with _client(auth_app) as client:
            for _ in range(4):
                await _login(client)
            authenticate.return_value = make_user()
            assert (await _login(client)).status_code == 200
            authenticate.return_value = None
            for _ in range(4):
                await _login(client)
            authenticate.return_value = make_user()
            assert (await _login(client)).status_code == 200


async def test_login_body_is_validated(auth_app):
    async with _client(auth_app) as client:
        response = await client.post("/api/v1/auth/login", json={"email": "a@b.c"})
    assert response.status_code == 422


# ---------------------------------------------------------------- logout and me


@pytest.mark.parametrize("cookies", [{COOKIE: "tok"}, {}], ids=["signed-in", "no-cookie"])
async def test_logout_clears_the_cookie(auth_app, cookies):
    async with _client(auth_app, cookies=cookies) as client:
        response = await client.post("/api/v1/auth/logout")

    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert f"{COOKIE}=" in cookie and "max-age=0" in cookie and "httponly" in cookie


async def test_me_without_cookie_is_401(auth_app):
    async with _client(auth_app) as client:
        response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401


async def test_me_with_a_real_token_cookie_returns_the_user(auth_app):
    """No auth mocks: the cookie holds a real JWT; only the user lookup is stubbed."""
    user = make_user(UserType.admin)
    token = issue_token(user)
    with patch("app.services.auth.get_user_by_id", new_callable=AsyncMock, return_value=user):
        async with _client(auth_app, cookies={COOKIE: token}) as client:
            response = await client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json()["email"] == user.email


async def test_me_with_unknown_or_expired_session_is_401(auth_app):
    with patch("app.api.deps.auth.resolve_token", new_callable=AsyncMock, return_value=None):
        async with _client(auth_app, cookies={COOKIE: "stale"}) as client:
            response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401


async def test_me_with_valid_session_returns_user(auth_app):
    user = make_user(UserType.support)
    with patch("app.api.deps.auth.resolve_token", new_callable=AsyncMock, return_value=user):
        async with _client(auth_app, cookies={COOKIE: "tok"}) as client:
            response = await client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json()["id"] == str(user.id)
    assert response.json()["user_type"] == "support"


# ---------------------------------------------------------------- own password


async def test_password_change_reissues_this_browsers_token_and_is_audited(auth_app, audit_rows):
    user = make_user(UserType.admin)
    change = AsyncMock()
    with (
        patch("app.api.deps.auth.resolve_token", new_callable=AsyncMock, return_value=user),
        patch("app.api.routes.auth.change_own_password", change),
        patch("app.api.routes.auth.issue_token", return_value="fresh-token"),
    ):
        async with _client(auth_app, cookies={COOKIE: "tok"}) as client:
            response = await client.post(
                "/api/v1/auth/password",
                json={"current_password": "old", "new_password": "New-password-123"},
            )

    assert response.status_code == 200
    assert change.await_args.kwargs == {
        "current_password": "old",
        "new_password": "New-password-123",
    }
    assert f"{COOKIE}=fresh-token" in response.headers["set-cookie"]
    assert audit_rows == [
        {
            "action": "user.password_change",
            "resource_type": "user",
            "resource_id": str(user.id),
            "client_ip": "127.0.0.1",
            "user_id": user.id,
            "client_id": user.client_id,
        }
    ]


async def test_wrong_current_password_counts_toward_the_lockout(auth_app):
    user = make_user(UserType.admin)
    change = AsyncMock(side_effect=InvalidInputError("Current password is incorrect"))
    with (
        patch("app.api.deps.auth.resolve_token", new_callable=AsyncMock, return_value=user),
        patch("app.api.routes.auth.change_own_password", change),
    ):
        async with _client(auth_app, cookies={COOKIE: "tok"}) as client:
            body = {"current_password": "guess", "new_password": "New-password-123"}
            for _ in range(5):
                assert (await client.post("/api/v1/auth/password", json=body)).status_code == 400
            locked = await client.post("/api/v1/auth/password", json=body)

    assert locked.status_code == 429
    assert change.await_count == 5


async def test_weak_new_password_is_400_but_not_a_failed_attempt(auth_app):
    user = make_user(UserType.admin)
    change = AsyncMock(side_effect=InvalidInputError("Password must be at least 12 characters"))
    with (
        patch("app.api.deps.auth.resolve_token", new_callable=AsyncMock, return_value=user),
        patch("app.api.routes.auth.change_own_password", change),
    ):
        async with _client(auth_app, cookies={COOKIE: "tok"}) as client:
            body = {"current_password": "right", "new_password": "short"}
            for _ in range(6):
                response = await client.post("/api/v1/auth/password", json=body)

    assert response.status_code == 400
    assert "12 characters" in response.json()["detail"]


# ---------------------------------------------------------------- JWT_SECRET missing


@pytest.fixture
def no_jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_login_is_503_without_jwt_secret_and_never_checks_the_password(
    auth_app, no_jwt_secret
):
    authenticate = AsyncMock(return_value=make_user())
    with patch("app.api.routes.auth.authenticate", authenticate):
        async with _client(auth_app) as client:
            response = await _login(client)

    assert response.status_code == 503
    assert "JWT_SECRET" in response.json()["detail"]
    authenticate.assert_not_awaited()


async def test_me_is_503_without_jwt_secret(auth_app, no_jwt_secret):
    async with _client(auth_app, cookies={COOKIE: "any.token.value"}) as client:
        response = await client.get("/api/v1/auth/me")
    assert response.status_code == 503
