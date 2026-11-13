"""Summary numbers and the daily series, computed from labeled conversations.

Pure functions over plain rows, so each formula has its own tests. A rate is None when its
denominator is zero, never 0.

Denominator: engaged conversations (at least one visitor message) that have ended. In-progress
conversations and bounces are in no rate. Outcomes are exclusive (first match wins), so the
outcome counts add up to the engaged count.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.services.analytics.outcomes import (
    ABANDONED,
    BOOKING,
    CALLBACK,
    HANDOVER,
    OUTCOMES,
    RESOLVED,
)


@dataclass(frozen=True)
class OutcomeRow:
    day: date  # the conversation's start day, in the client's timezone
    outcome: str
    engaged: bool
    duration_seconds: int


@dataclass(frozen=True)
class HandoverCounts:
    created: int
    accepted: int
    timed_out: int


@dataclass(frozen=True)
class Summary:
    started: int  # ended conversations plus in-progress ones
    ended: int
    in_progress: int
    engaged: int
    bounced: int
    outcome_counts: dict[str, int] = field(default_factory=dict)
    booking_rate: float | None = None
    callback_rate: float | None = None
    handover_rate: float | None = None
    resolution_rate: float | None = None
    abandonment_rate: float | None = None
    average_duration_seconds: float | None = None
    handovers_created: int = 0
    handovers_accepted: int = 0
    handovers_timed_out: int = 0
    handover_accept_rate: float | None = None
    handover_timeout_rate: float | None = None


@dataclass(frozen=True)
class DayPoint:
    day: date
    started: int
    engaged: int
    booking: int
    callback: int
    handover: int
    resolved: int
    abandoned: int


def safe_rate(numerator: int, denominator: int) -> float | None:
    """A share from 0 to 1, rounded to 4 places. None when there is nothing to divide by."""
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def summarize(rows: list[OutcomeRow], *, in_progress: int, handovers: HandoverCounts) -> Summary:
    engaged_rows = [row for row in rows if row.engaged]
    engaged = len(engaged_rows)
    counts = {outcome: 0 for outcome in OUTCOMES}
    for row in engaged_rows:
        counts[row.outcome] += 1

    average = (
        round(sum(row.duration_seconds for row in engaged_rows) / engaged, 1) if engaged else None
    )
    return Summary(
        started=len(rows) + in_progress,
        ended=len(rows),
        in_progress=in_progress,
        engaged=engaged,
        bounced=len(rows) - engaged,
        outcome_counts=counts,
        booking_rate=safe_rate(counts[BOOKING], engaged),
        callback_rate=safe_rate(counts[CALLBACK], engaged),
        handover_rate=safe_rate(counts[HANDOVER], engaged),
        resolution_rate=safe_rate(counts[RESOLVED], engaged),
        abandonment_rate=safe_rate(counts[ABANDONED], engaged),
        average_duration_seconds=average,
        handovers_created=handovers.created,
        handovers_accepted=handovers.accepted,
        handovers_timed_out=handovers.timed_out,
        handover_accept_rate=safe_rate(handovers.accepted, handovers.created),
        handover_timeout_rate=safe_rate(handovers.timed_out, handovers.created),
    )


def daily_series(rows: list[OutcomeRow], start: date, end: date) -> list[DayPoint]:
    """One point per day from start to end, inclusive. Days with nothing are zero."""
    days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    buckets = {day: {"started": 0, "engaged": 0, **dict.fromkeys(OUTCOMES, 0)} for day in days}
    for row in rows:
        bucket = buckets.get(row.day)
        if bucket is None:
            continue
        bucket["started"] += 1
        if row.engaged:
            bucket["engaged"] += 1
            bucket[row.outcome] += 1
    return [
        DayPoint(
            day=day,
            started=buckets[day]["started"],
            engaged=buckets[day]["engaged"],
            booking=buckets[day][BOOKING],
            callback=buckets[day][CALLBACK],
            handover=buckets[day][HANDOVER],
            resolved=buckets[day][RESOLVED],
            abandoned=buckets[day][ABANDONED],
        )
        for day in days
    ]
