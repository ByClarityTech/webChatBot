"""Date ranges and the shared test-session predicate.

Every analytics query filters out test conversations with TEST_EXCLUDED, against the
chat_sessions alias "cs". Nothing else decides whether a test conversation counts.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

TEST_EXCLUDED = "cs.is_test_session = FALSE"
MAX_RANGE_DAYS = 366
PRESET_DAYS = {"7d": 7, "30d": 30, "90d": 90}
DEFAULT_PRESET = "30d"


@dataclass(frozen=True)
class Period:
    """A client-local day range, both ends inclusive."""

    start: date
    end: date
    timezone: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def bounds(self) -> tuple[datetime, datetime]:
        """[start, end) as aware UTC datetimes, from local midnight to local midnight."""
        tz = ZoneInfo(self.timezone)
        start = datetime.combine(self.start, datetime.min.time(), tzinfo=tz)
        end = datetime.combine(self.end + timedelta(days=1), datetime.min.time(), tzinfo=tz)
        return start.astimezone(UTC), end.astimezone(UTC)


def resolve_period(
    *,
    preset: str | None,
    start: date | None,
    end: date | None,
    timezone: str,
    today: date,
) -> Period:
    """A preset (7d, 30d, 90d) ending today, or an explicit start and end. Raises ValueError."""
    if preset is not None:
        if start is not None or end is not None:
            raise ValueError("Choose a preset or a custom range, not both")
        if preset not in PRESET_DAYS:
            raise ValueError("Unknown range preset")
        return Period(today - timedelta(days=PRESET_DAYS[preset] - 1), today, timezone)
    if start is None or end is None:
        raise ValueError("A custom range needs both a start and an end date")
    if end < start:
        raise ValueError("The end date is before the start date")
    period = Period(start, end, timezone)
    if period.days > MAX_RANGE_DAYS:
        raise ValueError(f"A range can be at most {MAX_RANGE_DAYS} days")
    return period
