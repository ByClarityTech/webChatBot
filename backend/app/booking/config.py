from dataclasses import dataclass

from app.core.config import get_settings
from app.db.models.admin_settings import BusinessHoursRecord, get_business_hours


@dataclass(frozen=True)
class BusinessHoursConfig:
    business_days: list[int]
    business_hours_start: str
    business_hours_end: str
    booking_slot_duration_minutes: int
    booking_slot_buffer_minutes: int
    booking_timezone: str
    source: str


def _from_env() -> BusinessHoursConfig:
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


async def get_business_hours_config() -> BusinessHoursConfig:
    record = await get_business_hours()
    if record is None:
        return _from_env()
    return _from_record(record)
