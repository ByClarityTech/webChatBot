from unittest.mock import AsyncMock, patch

import pytest
from auth_helpers import signed_in, signed_out
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin import dashboard as admin_dashboard
from app.core.roles import UserType
from app.schemas.admin_dashboard import AdminDashboardSummaryResponse


@pytest.fixture
def dashboard_app():
    test_app = FastAPI()
    test_app.include_router(admin_dashboard.router, prefix="/api/v1")
    return test_app


SAMPLE_SUMMARY = AdminDashboardSummaryResponse(
    document_count=3,
    bookings_today=2,
    calendar_connected=True,
)


@pytest.mark.asyncio
async def test_dashboard_summary_without_auth_returns_401(
    dashboard_app
):
    with signed_out(dashboard_app):
        transport = ASGITransport(app=dashboard_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/dashboard/summary")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_summary_returns_aggregated_counts(
    dashboard_app
):
    with (
        signed_in(dashboard_app, UserType.admin),
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
            )

    assert response.status_code == 200
    assert response.json() == {
        "document_count": 3,
        "bookings_today": 2,
        "calendar_connected": True,
    }
