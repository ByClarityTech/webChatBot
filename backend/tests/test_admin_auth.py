from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin import auth, google_oauth
from app.core.rate_limit import reset_admin_login_rate_limiter
from app.integrations.google.oauth import GoogleOAuthError

TEST_ADMIN_KEY = "test-admin-key-secret"


@pytest.fixture
def admin_app():
    test_app = FastAPI()
    test_app.include_router(auth.router, prefix="/api/v1")
    test_app.include_router(google_oauth.router, prefix="/api/v1")
    return test_app


@pytest.fixture
def configured_admin_settings():
    settings = MagicMock()
    settings.admin_api_key = TEST_ADMIN_KEY
    settings.admin_login_rate_limit_per_hour = 20
    return settings


@pytest.fixture(autouse=True)
def _reset_admin_login_rate_limiter():
    reset_admin_login_rate_limiter()
    yield
    reset_admin_login_rate_limiter()


@pytest.mark.asyncio
async def test_oauth_status_without_bearer_returns_401(admin_app, configured_admin_settings):
    with patch("app.api.deps.admin_auth.get_settings", return_value=configured_admin_settings):
        transport = ASGITransport(app=admin_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/google/oauth/status")

    assert response.status_code == 401
    assert "admin api key" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_oauth_status_with_correct_key_returns_200(admin_app, configured_admin_settings):
    with (
        patch("app.api.deps.admin_auth.get_settings", return_value=configured_admin_settings),
        patch(
            "app.api.routes.admin.google_oauth.is_google_calendar_connected",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        transport = ASGITransport(app=admin_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/admin/google/oauth/status",
                headers={"Authorization": f"Bearer {TEST_ADMIN_KEY}"},
            )

    assert response.status_code == 200
    assert response.json()["connected"] is False


@pytest.mark.asyncio
async def test_oauth_callback_without_auth_does_not_require_bearer(admin_app, configured_admin_settings):
    mock_settings = MagicMock()
    mock_settings.admin_api_key = TEST_ADMIN_KEY
    mock_settings.admin_frontend_url = "http://localhost:3000"
    with (
        patch("app.api.deps.admin_auth.get_settings", return_value=configured_admin_settings),
        patch("app.api.routes.admin.google_oauth.get_settings", return_value=mock_settings),
    ):
        transport = ASGITransport(app=admin_app)
        async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
            response = await client.get("/api/v1/admin/google/oauth/callback")

    assert response.status_code != 401
    assert response.status_code == 307
    assert response.headers["location"] == "http://localhost:3000/admin/calendar?error=Missing%20authorization%20code"


@pytest.mark.asyncio
async def test_admin_login_valid_key_returns_ok(admin_app, configured_admin_settings):
    with patch("app.api.routes.admin.auth.get_settings", return_value=configured_admin_settings):
        transport = ASGITransport(app=admin_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/admin/auth/login",
                json={"api_key": TEST_ADMIN_KEY},
            )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_admin_login_invalid_key_returns_401(admin_app, configured_admin_settings):
    with patch("app.api.routes.admin.auth.get_settings", return_value=configured_admin_settings):
        transport = ASGITransport(app=admin_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/admin/auth/login",
                json={"api_key": "wrong-key"},
            )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_admin_login_rate_limit_returns_429(admin_app):
    settings = MagicMock()
    settings.admin_api_key = TEST_ADMIN_KEY
    settings.admin_login_rate_limit_per_hour = 2

    with (
        patch("app.api.routes.admin.auth.get_settings", return_value=settings),
        patch("app.core.rate_limit.get_settings", return_value=settings),
    ):
        transport = ASGITransport(app=admin_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(2):
                response = await client.post(
                    "/api/v1/admin/auth/login",
                    json={"api_key": "wrong-key"},
                )
                assert response.status_code == 401

            response = await client.post(
                "/api/v1/admin/auth/login",
                json={"api_key": "wrong-key"},
            )

    assert response.status_code == 429
    assert "too many" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_oauth_status_refresh_failure_returns_refresh_error(admin_app, configured_admin_settings):
    expired = datetime.now(timezone.utc) - timedelta(minutes=5)
    record = MagicMock()
    record.calendar_id = "primary"
    record.token_expiry = expired
    record.connected_email = "admin@example.com"

    with (
        patch("app.api.deps.admin_auth.get_settings", return_value=configured_admin_settings),
        patch(
            "app.api.routes.admin.google_oauth.is_google_calendar_connected",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.api.routes.admin.google_oauth.get_valid_credentials",
            new_callable=AsyncMock,
            side_effect=GoogleOAuthError("Google Calendar refresh failed. Please reconnect your calendar."),
        ),
    ):
        transport = ASGITransport(app=admin_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/admin/google/oauth/status",
                headers={"Authorization": f"Bearer {TEST_ADMIN_KEY}"},
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is False
    assert "refresh failed" in payload["refresh_error"].lower()


@pytest.mark.asyncio
async def test_oauth_callback_success_redirects_to_frontend(admin_app, configured_admin_settings):
    mock_settings = MagicMock()
    mock_settings.admin_frontend_url = "http://localhost:3000"
    mock_record = MagicMock()
    mock_record.calendar_id = "primary"
    mock_record.connected_email = "admin@example.com"

    with (
        patch("app.api.deps.admin_auth.get_settings", return_value=configured_admin_settings),
        patch("app.api.routes.admin.google_oauth.get_settings", return_value=mock_settings),
        patch(
            "app.api.routes.admin.google_oauth.exchange_code_and_store",
            new_callable=AsyncMock,
            return_value=mock_record,
        ),
    ):
        transport = ASGITransport(app=admin_app)
        async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
            response = await client.get(
                "/api/v1/admin/google/oauth/callback?code=abc&state=xyz",
            )

    assert response.status_code == 307
    assert response.headers["location"] == "http://localhost:3000/admin/calendar?connected=1"


@pytest.mark.asyncio
async def test_oauth_status_unconfigured_admin_key_returns_503(admin_app):
    settings = MagicMock()
    settings.admin_api_key = ""
    with patch("app.api.deps.admin_auth.get_settings", return_value=settings):
        transport = ASGITransport(app=admin_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/admin/google/oauth/status",
                headers={"Authorization": "Bearer any-key"},
            )

    assert response.status_code == 503
    assert "admin_api_key" in response.json()["detail"].lower()
