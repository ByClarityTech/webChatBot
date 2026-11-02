from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from auth_helpers import signed_in, signed_out
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin import settings as admin_settings
from app.booking.availability import generate_candidate_slots, get_availability_for_date
from app.booking.config import BusinessHoursConfig
from app.core.roles import UserType
from app.db.models.admin_settings import BusinessHoursRecord


@pytest.fixture
def admin_settings_app():
    test_app = FastAPI()
    test_app.include_router(admin_settings.router, prefix="/api/v1")
    return test_app


SAMPLE_DB_RECORD = BusinessHoursRecord(
    business_days=[1, 2, 3, 4, 5],
    start_time="10:00",
    end_time="16:00",
    slot_duration_minutes=30,
    slot_buffer_minutes=10,
    timezone="Asia/Kolkata",
    updated_at=datetime(2026, 6, 24, 12, 0, 0),
)

SAMPLE_DB_CONFIG = BusinessHoursConfig(
    business_days=[1, 2, 3, 4, 5],
    business_hours_start="10:00",
    business_hours_end="16:00",
    booking_slot_duration_minutes=30,
    booking_slot_buffer_minutes=10,
    booking_timezone="Asia/Kolkata",
    source="database",
)


@pytest.mark.asyncio
async def test_get_business_hours_without_auth_returns_401(
    admin_settings_app
):
    with signed_out(admin_settings_app):
        transport = ASGITransport(app=admin_settings_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/settings/business-hours")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_business_hours_with_auth_returns_settings(
    admin_settings_app
):
    with (
        signed_in(admin_settings_app, UserType.admin),
        patch(
            "app.api.routes.admin.settings.get_business_hours_config",
            new_callable=AsyncMock,
            return_value=SAMPLE_DB_CONFIG,
        ),
        patch(
            "app.api.routes.admin.settings.get_business_hours",
            new_callable=AsyncMock,
            return_value=SAMPLE_DB_RECORD,
        ),
    ):
        transport = ASGITransport(app=admin_settings_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/admin/settings/business-hours",
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["start_time"] == "10:00"
    assert payload["end_time"] == "16:00"
    assert payload["source"] == "database"
    assert payload["business_days"] == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_put_business_hours_updates_settings(admin_settings_app):
    mock_upsert = AsyncMock(return_value=SAMPLE_DB_RECORD)

    with (
        signed_in(admin_settings_app, UserType.admin),
        patch("app.api.routes.admin.settings.upsert_business_hours", mock_upsert),
        patch(
            "app.api.routes.admin.settings.get_business_hours_config",
            new_callable=AsyncMock,
            return_value=SAMPLE_DB_CONFIG,
        ),
    ):
        transport = ASGITransport(app=admin_settings_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.put(
                "/api/v1/admin/settings/business-hours",
                json={
                    "business_days": [1, 2, 3, 4, 5],
                    "start_time": "10:00",
                    "end_time": "16:00",
                    "slot_duration_minutes": 30,
                    "slot_buffer_minutes": 10,
                    "timezone": "Asia/Kolkata",
                },
            )

    assert response.status_code == 200
    assert response.json()["start_time"] == "10:00"
    mock_upsert.assert_awaited_once()


@pytest.mark.asyncio
async def test_put_business_hours_without_auth_returns_401(
    admin_settings_app
):
    with signed_out(admin_settings_app):
        transport = ASGITransport(app=admin_settings_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.put(
                "/api/v1/admin/settings/business-hours",
                json={
                    "business_days": [1],
                    "start_time": "10:00",
                    "end_time": "16:00",
                    "slot_duration_minutes": 30,
                    "slot_buffer_minutes": 10,
                    "timezone": "Asia/Kolkata",
                },
            )

    assert response.status_code == 401


def test_generate_candidate_slots_uses_db_config():
    tz = ZoneInfo("Asia/Kolkata")
    target_date = date(2030, 7, 1)
    config = SAMPLE_DB_CONFIG

    slots = generate_candidate_slots(target_date, tz, config)

    assert slots
    assert slots[0][0].hour == 10
    assert slots[0][0].minute == 0
    assert (slots[0][1] - slots[0][0]).total_seconds() == 30 * 60
    assert slots[-1][1].hour == 16
    assert slots[-1][1].minute == 0


@pytest.mark.asyncio
async def test_availability_uses_updated_db_settings():
    tz = ZoneInfo("Asia/Kolkata")
    target_date = date(2030, 7, 2)

    with (
        patch(
            "app.booking.availability.get_business_hours_config",
            new_callable=AsyncMock,
            return_value=SAMPLE_DB_CONFIG,
        ),
        patch(
            "app.booking.availability.is_google_calendar_connected",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.booking.availability.query_freebusy",
            new_callable=AsyncMock,
            return_value=MagicMock(busy=[]),
        ),
    ):
        result = await get_availability_for_date(target_date)

    assert result.timezone == "Asia/Kolkata"
    assert result.slots
    assert result.slots[0].start.hour == 10
    assert all(slot.start.hour < 16 or (slot.start.hour == 15 and slot.end.hour <= 16) for slot in result.slots)
