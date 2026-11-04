from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from app.booking.config import BusinessHoursConfig, get_business_hours_config
from app.booking.schemas import AvailabilityResponse, Slot
from app.integrations.google.calendar_client import query_freebusy
from app.integrations.google.oauth import is_google_calendar_connected
from app.integrations.google.schemas import BusyPeriod


class CalendarNotConnectedError(Exception):
    pass


class SlotNotAvailableError(Exception):
    pass


class SlotInPastError(Exception):
    pass


def _parse_time(hhmm: str) -> time:
    hour, minute = map(int, hhmm.split(":"))
    return time(hour=hour, minute=minute)


def _slots_overlap(
    slot_start: datetime,
    slot_end: datetime,
    busy_start: datetime,
    busy_end: datetime,
) -> bool:
    return slot_start < busy_end and slot_end > busy_start


def _expand_busy_with_buffer(period: BusyPeriod, buffer: timedelta) -> tuple[datetime, datetime]:
    return period.start - buffer, period.end + buffer


def generate_candidate_slots(
    target_date: date,
    tz: ZoneInfo,
    config: BusinessHoursConfig,
) -> list[tuple[datetime, datetime]]:
    start_time = _parse_time(config.business_hours_start)
    end_time = _parse_time(config.business_hours_end)
    duration = timedelta(minutes=config.booking_slot_duration_minutes)

    day_start = datetime.combine(target_date, start_time, tzinfo=tz)
    day_end = datetime.combine(target_date, end_time, tzinfo=tz)

    slots: list[tuple[datetime, datetime]] = []
    current = day_start
    while current + duration <= day_end:
        slots.append((current, current + duration))
        current += duration
    return slots


async def get_availability_for_date(client_id: UUID, target_date: date) -> AvailabilityResponse:
    """Open slots of one client: its business hours (in its timezone) minus busy times in its
    own Google Calendar."""
    config = await get_business_hours_config(client_id)
    tz = ZoneInfo(config.booking_timezone)

    if target_date.isoweekday() not in config.business_days:
        return AvailabilityResponse(
            date=target_date,
            timezone=config.booking_timezone,
            slots=[],
        )

    if not await is_google_calendar_connected(client_id):
        raise CalendarNotConnectedError(
            "Google Calendar is not connected (or needs to be connected again) for this client."
        )

    business_start = _parse_time(config.business_hours_start)
    business_end = _parse_time(config.business_hours_end)
    day_start = datetime.combine(target_date, business_start, tzinfo=tz)
    day_end = datetime.combine(target_date, business_end, tzinfo=tz)

    freebusy = await query_freebusy(
        client_id=client_id,
        time_min=day_start,
        time_max=day_end,
        timezone=config.booking_timezone,
    )

    buffer = timedelta(minutes=config.booking_slot_buffer_minutes)
    blocked = [_expand_busy_with_buffer(period, buffer) for period in freebusy.busy]

    now = datetime.now(tz)
    available_slots: list[Slot] = []
    for slot_start, slot_end in generate_candidate_slots(target_date, tz, config):
        if slot_start < now:
            continue
        if any(
            _slots_overlap(slot_start, slot_end, busy_start, busy_end)
            for busy_start, busy_end in blocked
        ):
            continue
        available_slots.append(Slot(start=slot_start, end=slot_end))

    return AvailabilityResponse(
        date=target_date,
        timezone=config.booking_timezone,
        slots=available_slots,
    )


def _normalize_slot_start(slot_start: datetime, tz: ZoneInfo) -> datetime:
    if slot_start.tzinfo is None:
        return slot_start.replace(tzinfo=tz)
    return slot_start.astimezone(tz)


async def validate_bookable_slot(
    client_id: UUID, slot_start: datetime
) -> tuple[datetime, datetime, BusinessHoursConfig]:
    """Return (slot_start, slot_end, the client's hours config) if bookable; raise otherwise."""
    config = await get_business_hours_config(client_id)
    tz = ZoneInfo(config.booking_timezone)
    normalized_start = _normalize_slot_start(slot_start, tz)
    duration = timedelta(minutes=config.booking_slot_duration_minutes)
    slot_end = normalized_start + duration

    now = datetime.now(tz)
    if normalized_start < now:
        raise SlotInPastError("Cannot book a slot in the past")

    target_date = normalized_start.date()
    availability = await get_availability_for_date(client_id, target_date)

    for slot in availability.slots:
        if slot.start == normalized_start and slot.end == slot_end:
            return normalized_start, slot_end, config

    raise SlotNotAvailableError("Selected time slot is no longer available")
