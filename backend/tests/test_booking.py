from datetime import datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import booking
from app.booking.availability import SlotNotAvailableError
from app.db.models.booking import BookingRecord


@pytest.fixture
def booking_app():
    test_app = FastAPI()
    test_app.include_router(booking.router, prefix="/api/v1")
    return test_app


def _sample_booking() -> BookingRecord:
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
        status="confirmed",
        created_at=now,
    )


@pytest.mark.asyncio
async def test_create_booking_success(booking_app):
    record = _sample_booking()
    with patch(
        "app.api.routes.booking.create_booking",
        new_callable=AsyncMock,
        return_value=record,
    ):
        transport = ASGITransport(app=booking_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/booking",
                json={
                    "name": "Jane Doe",
                    "phone": "+15551234567",
                    "email": "jane@example.com",
                    "slot_start": "2030-07-02T10:00:00+05:30",
                },
            )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "confirmed"
    assert "id" in payload


@pytest.mark.asyncio
async def test_create_booking_slot_unavailable(booking_app):
    with patch(
        "app.api.routes.booking.create_booking",
        new_callable=AsyncMock,
        side_effect=SlotNotAvailableError("Selected time slot is no longer available"),
    ):
        transport = ASGITransport(app=booking_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/booking",
                json={
                    "name": "Jane Doe",
                    "phone": "+15551234567",
                    "email": "jane@example.com",
                    "slot_start": "2030-07-02T10:00:00+05:30",
                },
            )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_create_booking_rate_limited(booking_app):
    from app.core.rate_limit import reset_booking_rate_limiter

    reset_booking_rate_limiter()
    record = _sample_booking()

    with (
        patch("app.core.rate_limit.get_settings") as mock_settings,
        patch(
            "app.api.routes.booking.create_booking",
            new_callable=AsyncMock,
            return_value=record,
        ),
    ):
        settings = mock_settings.return_value
        settings.booking_rate_limit_per_hour = 1

        transport = ASGITransport(app=booking_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "name": "Jane Doe",
                "phone": "+15551234567",
                "email": "jane@example.com",
                "slot_start": "2030-07-02T10:00:00+05:30",
            }
            first = await client.post("/api/v1/booking", json=payload)
            second = await client.post("/api/v1/booking", json=payload)

    reset_booking_rate_limiter()
    assert first.status_code == 201
    assert second.status_code == 429
    assert "Too many booking requests" in second.json()["detail"]


@pytest.mark.asyncio
async def test_create_booking_conflict(booking_app):
    from app.booking.service import BookingConflictError

    with patch(
        "app.api.routes.booking.create_booking",
        new_callable=AsyncMock,
        side_effect=BookingConflictError("This time slot was just booked by someone else"),
    ):
        transport = ASGITransport(app=booking_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/booking",
                json={
                    "name": "Jane Doe",
                    "phone": "+15551234567",
                    "email": "jane@example.com",
                    "slot_start": "2030-07-02T10:00:00+05:30",
                },
            )

    assert response.status_code == 409
    assert "just booked" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_get_booking_not_found(booking_app):
    from app.booking.service import BookingNotFoundError

    with patch(
        "app.api.routes.booking.fetch_booking",
        new_callable=AsyncMock,
        side_effect=BookingNotFoundError("Booking not found"),
    ):
        transport = ASGITransport(app=booking_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/booking/{uuid4()}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_booking_success(booking_app):
    record = _sample_booking()
    with patch(
        "app.api.routes.booking.fetch_booking",
        new_callable=AsyncMock,
        return_value=record,
    ):
        transport = ASGITransport(app=booking_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/booking/{record.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == str(record.id)
    assert payload["status"] == "confirmed"
