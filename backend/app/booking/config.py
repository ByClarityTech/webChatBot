from dataclasses import dataclass
from uuid import UUID

from app.core.config import get_settings
from app.db.models.admin_settings import BusinessHoursRecord, get_client_business_hours


@dataclass(frozen=True)
class BusinessHoursConfig:
    business_days: list[int]
    business_hours_start: str
    business_hours_end: str
    booking_slot_duration_minutes: int
    booking_slot_buffer_minutes: int
    booking_timezone: str
    source: str


def default_business_hours() -> BusinessHoursConfig:
    """The .env hours: used for a client that has not saved its own."""
    settings = get_settings()
    return BusinessHoursConfig(
        business_days=settings.business_days_list,
        business_hours_start=settings.business_hours_start,
        business_hours_end=settings.business_hours_end,
        booking_slot_duration_minutes=settings.booking_slot_duration_minutes,
        booking_slot_buffer_minutes=settings.booking_slot_buffer_minutes,
        booking_timezone=settings.booking_timezone,
        source="env",
    )


def _from_record(record: BusinessHoursRecord) -> BusinessHoursConfig:
    return BusinessHoursConfig(
        business_days=record.business_days,
        business_hours_start=record.start_time,
        business_hours_end=record.end_time,
        booking_slot_duration_minutes=record.slot_duration_minutes,
        booking_slot_buffer_minutes=record.slot_buffer_minutes,
        booking_timezone=record.timezone,
        source="database",
    )


async def get_business_hours_config(client_id: UUID) -> BusinessHoursConfig:
    """The client's saved hours and timezone, or the .env defaults if it has none."""
    record = await get_client_business_hours(client_id)
    if record is None:
        return default_business_hours()
    return _from_record(record)
