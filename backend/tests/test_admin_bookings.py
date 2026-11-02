from datetime import datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from auth_helpers import signed_in, signed_out
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin import bookings as admin_bookings
from app.core.roles import UserType
from app.db.models.booking import BookingRecord


@pytest.fixture
def admin_bookings_app():
    test_app = FastAPI()
    test_app.include_router(admin_bookings.router, prefix="/api/v1")
    return test_app


def _sample_booking(*, status: str = "confirmed") -> BookingRecord:
    now = datetime(2030, 7, 2, 10, 0)
    return BookingRecord(
        id=uuid4(),
        name="Jane Doe",
        phone="+15551234567",
        email="jane@example.com",
        slot_start=now,
        slot_end=datetime(2030, 7, 2, 10, 30),
        google_event_id="evt_123",
        thread_id=None,
        status=status,
        created_at=now,
    )


@pytest.mark.asyncio
async def test_list_bookings_without_auth_returns_401(admin_bookings_app):
    with signed_out(admin_bookings_app):
        transport = ASGITransport(app=admin_bookings_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/bookings")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_bookings_with_auth_returns_paginated_list(admin_bookings_app):
    record = _sample_booking()
    mock_list = AsyncMock(return_value=([record], 1))

    with (
        signed_in(admin_bookings_app, UserType.admin),
        patch("app.api.routes.admin.bookings.list_bookings", mock_list),
    ):
        transport = ASGITransport(app=admin_bookings_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/admin/bookings?offset=0&limit=10&status=confirmed",
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["offset"] == 0
    assert payload["limit"] == 10
    assert payload["bookings"][0]["name"] == "Jane Doe"
    assert payload["bookings"][0]["email"] == "jane@example.com"
    mock_list.assert_awaited_once_with(
        offset=0,
        limit=10,
        slot_from=None,
        slot_to=None,
        status="confirmed",
    )


@pytest.mark.asyncio
async def test_list_bookings_passes_date_filters(admin_bookings_app):
    mock_list = AsyncMock(return_value=([], 0))

    with (
        signed_in(admin_bookings_app, UserType.admin),
        patch("app.api.routes.admin.bookings.list_bookings", mock_list),
    ):
        transport = ASGITransport(app=admin_bookings_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/admin/bookings"
                "?from=2030-07-01T00:00:00%2B00:00&to=2030-07-31T23:59:59%2B00:00",
            )

    assert response.status_code == 200
    kwargs = mock_list.await_args.kwargs
    assert kwargs["slot_from"] is not None
    assert kwargs["slot_to"] is not None


@pytest.mark.asyncio
async def test_get_booking_detail(admin_bookings_app):
    record = _sample_booking()
    mock_fetch = AsyncMock(return_value=record)

    with (
        signed_in(admin_bookings_app, UserType.admin),
        patch("app.api.routes.admin.bookings.fetch_booking", mock_fetch),
    ):
        transport = ASGITransport(app=admin_bookings_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/admin/bookings/{record.id}",
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == str(record.id)
    assert payload["phone"] == "+15551234567"
    assert payload["google_event_id"] == "evt_123"
    mock_fetch.assert_awaited_once_with(record.id)


@pytest.mark.asyncio
async def test_get_booking_detail_not_found(admin_bookings_app):
    from app.booking.service import BookingNotFoundError

    with (
        signed_in(admin_bookings_app, UserType.admin),
        patch(
            "app.api.routes.admin.bookings.fetch_booking",
            new_callable=AsyncMock,
            side_effect=BookingNotFoundError("Booking not found"),
        ),
    ):
        transport = ASGITransport(app=admin_bookings_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/admin/bookings/{uuid4()}",
            )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cancel_booking(admin_bookings_app):
    record = _sample_booking(status="cancelled")
    mock_cancel = AsyncMock(return_value=record)

    with (
        signed_in(admin_bookings_app, UserType.admin),
        patch("app.api.routes.admin.bookings.cancel_booking", mock_cancel),
    ):
        transport = ASGITransport(app=admin_bookings_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.patch(
                f"/api/v1/admin/bookings/{record.id}",
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "cancelled"
    mock_cancel.assert_awaited_once_with(record.id)


@pytest.mark.asyncio
async def test_cancel_booking_without_auth_returns_401(admin_bookings_app):
    with signed_out(admin_bookings_app):
        transport = ASGITransport(app=admin_bookings_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.patch(f"/api/v1/admin/bookings/{uuid4()}")

    assert response.status_code == 401
