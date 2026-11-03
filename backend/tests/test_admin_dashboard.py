from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from auth_helpers import signed_in, signed_out
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin import dashboard as admin_dashboard
from app.core.roles import UserType
from app.db.models.client import ClientRecord
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


def _client_lookup(client_id):
    return AsyncMock(
        return_value=ClientRecord(
            id=client_id,
            name="Example",
            slug="example",
            status="active",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )


@pytest.mark.asyncio
async def test_dashboard_summary_returns_counts_for_the_admins_client(dashboard_app):
    summary = AsyncMock(return_value=SAMPLE_SUMMARY)
    with signed_in(dashboard_app, UserType.admin) as admin, patch(
        "app.api.routes.admin.dashboard.get_dashboard_summary", summary
    ), patch("app.services.client_scope.get_client", _client_lookup(admin.client_id)):
        transport = ASGITransport(app=dashboard_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/dashboard/summary")

    assert response.status_code == 200
    assert response.json() == {
        "document_count": 3,
        "bookings_today": 2,
        "calendar_connected": True,
    }
    summary.assert_awaited_once_with(admin.client_id)


@pytest.mark.asyncio
async def test_super_admin_dashboard_counts_all_clients_or_one(dashboard_app):
    summary = AsyncMock(return_value=SAMPLE_SUMMARY)
    chosen = uuid4()
    with signed_in(dashboard_app, UserType.super_admin), patch(
        "app.api.routes.admin.dashboard.get_dashboard_summary", summary
    ), patch("app.services.client_scope.get_client", _client_lookup(chosen)):
        transport = ASGITransport(app=dashboard_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            everything = await client.get("/api/v1/admin/dashboard/summary")
            one = await client.get(
                "/api/v1/admin/dashboard/summary", params={"client_id": str(chosen)}
            )

    assert (everything.status_code, one.status_code) == (200, 200)
    assert [call.args for call in summary.await_args_list] == [(None,), (chosen,)]
