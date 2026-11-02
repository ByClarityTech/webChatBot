from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import booking
from app.api.routes.admin import google_oauth
from app.booking.availability import (
    CalendarNotConnectedError,
    generate_candidate_slots,
    get_availability_for_date,
)
from app.booking.config import BusinessHoursConfig
from app.booking.schemas import AvailabilityResponse, Slot
from app.integrations.google.schemas import BusyPeriod, FreeBusyResult


@pytest.fixture
def oauth_app():
    test_app = FastAPI()
    test_app.include_router(google_oauth.router, prefix="/api/v1")
    return test_app


@pytest.fixture
def booking_app():
    test_app = FastAPI()
    test_app.include_router(booking.router, prefix="/api/v1")
    return test_app


@pytest.mark.asyncio
async def test_oauth_status_not_connected(oauth_app):
    mock_settings = MagicMock()
    mock_settings.admin_api_key = "test-admin-key"
    with (
        patch("app.api.deps.admin_auth.get_settings", return_value=mock_settings),
        patch(
            "app.api.routes.admin.google_oauth.is_google_calendar_connected",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        transport = ASGITransport(app=oauth_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/admin/google/oauth/status",
                headers={"Authorization": "Bearer test-admin-key"},
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is False
    assert payload["calendar_id"] is None
    assert payload["expires_at"] is None


@pytest.mark.asyncio
async def test_availability_not_connected_returns_503(booking_app):
    with patch(
        "app.api.routes.booking.get_availability_for_date",
        new_callable=AsyncMock,
        side_effect=CalendarNotConnectedError("Google Calendar is not connected."),
    ):
        transport = ASGITransport(app=booking_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/booking/availability?date=2026-07-01")

    assert response.status_code == 503
    assert "not connected" in response.json()["detail"].lower()


def test_generate_candidate_slots_respects_business_hours():
    from app.booking.config import BusinessHoursConfig

    tz = ZoneInfo("Asia/Kolkata")
    target_date = date(2030, 7, 1)
    config = BusinessHoursConfig(
        business_days=[1, 2, 3, 4, 5],
        business_hours_start="09:00",
        business_hours_end="17:00",
        booking_slot_duration_minutes=30,
        booking_slot_buffer_minutes=15,
        booking_timezone="Asia/Kolkata",
        source="env",
    )

    slots = generate_candidate_slots(target_date, tz, config)

    assert slots
    assert slots[0][0].hour == 9
    assert slots[0][0].minute == 0
    assert (slots[0][1] - slots[0][0]).total_seconds() == 30 * 60
    assert slots[-1][1].hour == 17
    assert slots[-1][1].minute == 0


@pytest.mark.asyncio
async def test_availability_excludes_busy_periods():
    tz = ZoneInfo("Asia/Kolkata")
    target_date = date(2030, 7, 2)

    freebusy = FreeBusyResult(
        calendar_id="primary",
        busy=[
            BusyPeriod(
                start=datetime(2030, 7, 2, 10, 0, tzinfo=tz),
                end=datetime(2030, 7, 2, 10, 30, tzinfo=tz),
            )
        ],
    )

    with (
        patch(
            "app.booking.availability.get_business_hours_config",
            new_callable=AsyncMock,
            return_value=BusinessHoursConfig(
                business_days=[1, 2, 3, 4, 5, 6, 7],
                business_hours_start="09:00",
                business_hours_end="17:00",
                booking_slot_duration_minutes=30,
                booking_slot_buffer_minutes=15,
                booking_timezone="Asia/Kolkata",
                source="env",
            ),
        ),
        patch(
            "app.booking.availability.is_google_calendar_connected",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.booking.availability.query_freebusy",
            new_callable=AsyncMock,
            return_value=freebusy,
        ),
    ):
        result = await get_availability_for_date(target_date)

    assert isinstance(result, AvailabilityResponse)
    assert result.date == target_date
    assert all(isinstance(slot, Slot) for slot in result.slots)
    blocked_starts = {slot.start for slot in result.slots if slot.start.hour == 10}
    assert datetime(2030, 7, 2, 10, 0, tzinfo=tz) not in blocked_starts
    assert datetime(2030, 7, 2, 9, 30, tzinfo=tz) not in {slot.start for slot in result.slots}


@pytest.mark.asyncio
async def test_availability_weekend_returns_empty_slots():
    with (
        patch(
            "app.booking.availability.get_business_hours_config",
            new_callable=AsyncMock,
            return_value=BusinessHoursConfig(
                business_days=[1, 2, 3, 4, 5],
                business_hours_start="09:00",
                business_hours_end="17:00",
                booking_slot_duration_minutes=30,
                booking_slot_buffer_minutes=15,
                booking_timezone="Asia/Kolkata",
                source="env",
            ),
        ),
        patch(
            "app.booking.availability.is_google_calendar_connected",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        result = await get_availability_for_date(date(2026, 7, 4))

    assert result.slots == []


def test_ensure_scope_in_auth_url_adds_missing_scope():
    from app.integrations.google.oauth import GOOGLE_CALENDAR_SCOPE, _ensure_scope_in_auth_url

    bare = "https://accounts.google.com/o/oauth2/auth?response_type=code&client_id=test"
    fixed = _ensure_scope_in_auth_url(bare)
    assert "scope=" in fixed
    assert quote(GOOGLE_CALENDAR_SCOPE, safe="") in fixed or GOOGLE_CALENDAR_SCOPE.replace("/", "%2F") in fixed
