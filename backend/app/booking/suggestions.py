"""The next open slots of a client, for the booking card's quick picks (FR-16).

One calendar query covers the whole seven days, rather than one query per day, so this stays
quick. Results are cached per client for CACHE_SECONDS. The rules are the same as the day picker:
business days and hours, the slot length, the buffer around busy times, and the minimum notice.

The caller applies a timeout. A slow or failing calendar gives no suggestions, never an error
in the chat.
"""

import time as clock
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from app.booking.availability import (
    CalendarNotConnectedError,
    _expand_busy_with_buffer,
    _parse_time,
    _slots_overlap,
    generate_candidate_slots,
)
from app.booking.config import get_business_hours_config
from app.booking.mode import MODE_BUILT_IN, MODE_GOOGLE
from app.booking.schemas import Slot
from app.db.models.booking import list_blocking_bookings
from app.db.models.client import get_client
from app.integrations.google.calendar_client import query_freebusy
from app.integrations.google.oauth import is_google_calendar_connected
from app.integrations.google.schemas import BusyPeriod

SUGGESTION_DAYS = 7
SUGGESTION_LIMIT = 3
CACHE_SECONDS = 60.0
TIMEOUT_SECONDS = 1.5

_cache: dict[UUID, tuple[float, list[Slot]]] = {}


def clear_suggestion_cache() -> None:
    _cache.clear()


async def _blocked_periods(
    client_id: UUID,
    *,
    mode: str,
    window_start: datetime,
    window_end: datetime,
    timezone: str,
    buffer: timedelta,
) -> list[tuple[datetime, datetime]]:
    busy: list[tuple[datetime, datetime]] = []
    if mode == MODE_GOOGLE:
        freebusy = await query_freebusy(
            client_id=client_id,
            time_min=window_start,
            time_max=window_end,
            timezone=timezone,
        )
        busy.extend((period.start, period.end) for period in freebusy.busy)
    busy.extend(
        await list_blocking_bookings(
            client_id,
            window_start=window_start - buffer,
            window_end=window_end + buffer,
            include_confirmed=mode == MODE_BUILT_IN,
        )
    )
    return [
        _expand_busy_with_buffer(BusyPeriod(start=start, end=end), buffer) for start, end in busy
    ]


async def compute_suggestions(client_id: UUID, *, now: datetime) -> list[Slot]:
    client = await get_client(client_id)
    mode = client.calendar_mode if client is not None else None
    if mode == MODE_GOOGLE:
        if not await is_google_calendar_connected(client_id):
            raise CalendarNotConnectedError("Google Calendar is not connected")
    elif mode != MODE_BUILT_IN:
        raise CalendarNotConnectedError("Online booking is off for this client")

    config = await get_business_hours_config(client_id)
    tz = ZoneInfo(config.booking_timezone)
    today = now.astimezone(tz).date()
    open_days = [
        day
        for day in (today + timedelta(days=offset) for offset in range(SUGGESTION_DAYS))
        if day.isoweekday() in config.business_days
    ]
    if not open_days:
        return []

    window_start = datetime.combine(
        open_days[0], _parse_time(config.business_hours_start), tzinfo=tz
    )
    window_end = datetime.combine(open_days[-1], _parse_time(config.business_hours_end), tzinfo=tz)
    buffer = timedelta(minutes=config.booking_slot_buffer_minutes)
    blocked = await _blocked_periods(
        client_id,
        mode=mode,
        window_start=window_start,
        window_end=window_end,
        timezone=config.booking_timezone,
        buffer=buffer,
    )

    earliest = now.astimezone(tz) + timedelta(minutes=config.booking_min_notice_minutes)
    found: list[Slot] = []
    for day in open_days:
        for slot_start, slot_end in generate_candidate_slots(day, tz, config):
            if slot_start < earliest:
                continue
            if any(
                _slots_overlap(slot_start, slot_end, busy_start, busy_end)
                for busy_start, busy_end in blocked
            ):
                continue
            found.append(Slot(start=slot_start, end=slot_end))
            if len(found) == SUGGESTION_LIMIT:
                return found
    return found


async def suggest_slots(client_id: UUID, *, now: datetime | None = None) -> list[Slot]:
    """Up to three open slots in the next seven days. Cached for CACHE_SECONDS per client.

    Failures are not cached, so the next request tries the calendar again."""
    current = clock.monotonic()
    cached = _cache.get(client_id)
    if cached is not None and cached[0] > current:
        return cached[1]
    slots = await compute_suggestions(client_id, now=now or datetime.now(UTC))
    _cache[client_id] = (current + CACHE_SECONDS, slots)
    return slots
