from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin import dashboard as admin_dashboard
from app.schemas.admin_dashboard import AdminDashboardSummaryResponse

TEST_ADMIN_KEY = "test-admin-key-secret"


@pytest.fixture
def dashboard_app():
    test_app = FastAPI()
    test_app.include_router(admin_dashboard.router, prefix="/api/v1")
    return test_app


@pytest.fixture
def configured_admin_settings():
    settings = MagicMock()
    settings.admin_api_key = TEST_ADMIN_KEY
    return settings


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_ADMIN_KEY}"}


SAMPLE_SUMMARY = AdminDashboardSummaryResponse(
    document_count=3,
    bookings_today=2,
    calendar_connected=True,
)


@pytest.mark.asyncio
async def test_dashboard_summary_without_auth_returns_401(
    dashboard_app, configured_admin_settings
):
    with patch("app.api.deps.admin_auth.get_settings", return_value=configured_admin_settings):
        transport = ASGITransport(app=dashboard_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/dashboard/summary")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_summary_returns_aggregated_counts(
    dashboard_app, configured_admin_settings
):
    with (
        patch("app.api.deps.admin_auth.get_settings", return_value=configured_admin_settings),
        patch(
            "app.api.routes.admin.dashboard.get_dashboard_summary",
            new_callable=AsyncMock,
            return_value=SAMPLE_SUMMARY,
        ),
    ):
        transport = ASGITransport(app=dashboard_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/admin/dashboard/summary",
                headers=_auth_headers(),
            )

    assert response.status_code == 200
    assert response.json() == {
        "document_count": 3,
        "bookings_today": 2,
        "calendar_connected": True,
    }
