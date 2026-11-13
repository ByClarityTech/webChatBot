"""Assembles the analytics report, the conversation list, the patient groups, and the transcript
view from the database and the pure modules.

Client scope is decided by the route. A None client_id means all clients (Super Admin only,
and only for the report and the outcome export; never for transcripts or phone lookups).
"""

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from app.booking.config import default_business_hours, get_business_hours_config
from app.db.models import analytics as db
from app.schemas.analytics import (
    AnalyticsResponse,
    ContactOut,
    ConversationDetailOut,
    ConversationItemOut,
    ConversationListOut,
    DayPointOut,
    FunnelStep,
    OutcomeCountOut,
    PatientGroupListOut,
    PatientGroupOut,
    PeriodOut,
    StaffMessageOut,
    SummaryOut,
    TopGapOut,
    TranscriptLineOut,
    UnansweredOut,
)
from app.services.analytics.csv_export import conversations_csv, summary_csv
from app.services.analytics.filters import Period
from app.services.analytics.metrics import (
    HandoverCounts,
    OutcomeRow,
    Summary,
    daily_series,
    safe_rate,
    summarize,
)
from app.services.analytics.outcomes import END_GRACE_MINUTES, OUTCOME_LABELS, OUTCOMES
from app.services.analytics.transcript import conversation_lines, mask_phone
from app.services.contact_privacy import ContactStorageNotConfigured, decrypt_phone

TOP_GAPS_LIMIT = 10
EXPORT_ROW_LIMIT = 5000
NOTE = (
    f"Outcomes are set for conversations that ended at least {END_GRACE_MINUTES} minutes ago. "
    "Test conversations are never counted. Rates use engaged conversations (at least one "
    "visitor message) as the denominator."
)


async def timezone_for(client_id: UUID | None) -> str:
    if client_id is None:
        return default_business_hours().booking_timezone
    return (await get_business_hours_config(client_id)).booking_timezone


def today_in(timezone: str):
    return datetime.now(ZoneInfo(timezone)).date()


async def summary_for(client_id: UUID | None, period: Period) -> tuple[Summary, list[OutcomeRow]]:
    start, end = period.bounds()
    raw = await db.outcome_rows(client_id, start, end, period.timezone)
    rows = [OutcomeRow(day=r[0], outcome=r[1], engaged=r[2], duration_seconds=r[3]) for r in raw]
    in_progress = await db.count_in_progress(client_id, start, end)
    created, accepted, timed_out = await db.handover_counts(client_id, start, end)
    summary = summarize(
        rows,
        in_progress=in_progress,
        handovers=HandoverCounts(created=created, accepted=accepted, timed_out=timed_out),
    )
    return summary, rows


def _summary_out(summary: Summary) -> SummaryOut:
    return SummaryOut(
        started=summary.started,
        ended=summary.ended,
        in_progress=summary.in_progress,
        engaged=summary.engaged,
        bounced=summary.bounced,
        booking_rate=summary.booking_rate,
        callback_rate=summary.callback_rate,
        handover_rate=summary.handover_rate,
        resolution_rate=summary.resolution_rate,
        abandonment_rate=summary.abandonment_rate,
        average_duration_seconds=summary.average_duration_seconds,
        handovers_created=summary.handovers_created,
        handovers_accepted=summary.handovers_accepted,
        handovers_timed_out=summary.handovers_timed_out,
        handover_accept_rate=summary.handover_accept_rate,
        handover_timeout_rate=summary.handover_timeout_rate,
    )


def _funnel(summary: Summary) -> list[FunnelStep]:
    engaged = summary.engaged
    counts = summary.outcome_counts

    def of_engaged(count: int) -> float | None:
        return safe_rate(count, engaged)

    return [
        FunnelStep(
            key="started",
            label="Conversations started",
            count=summary.started,
            percent_of_engaged=None,
        ),
        FunnelStep(
            key="engaged",
            label="Engaged (sent a message)",
            count=engaged,
            percent_of_engaged=of_engaged(engaged),
        ),
        FunnelStep(
            key="booking",
            label="Booked",
            count=counts.get("booking", 0),
            percent_of_engaged=of_engaged(counts.get("booking", 0)),
        ),
        FunnelStep(
            key="callback",
            label="Callback requested",
            count=counts.get("callback", 0),
            percent_of_engaged=of_engaged(counts.get("callback", 0)),
        ),
        FunnelStep(
            key="handover",
            label="Handed to a team member",
            count=counts.get("handover", 0),
            percent_of_engaged=of_engaged(counts.get("handover", 0)),
        ),
    ]


async def build_analytics(client_id: UUID | None, period: Period) -> AnalyticsResponse:
    summary, rows = await summary_for(client_id, period)
    start, end = period.bounds()
    gaps = await db.top_gaps(client_id, start, end, TOP_GAPS_LIMIT)
    return AnalyticsResponse(
        client_id=client_id,
        period=PeriodOut(
            start=period.start, end=period.end, timezone=period.timezone, days=period.days
        ),
        summary=_summary_out(summary),
        funnel=_funnel(summary),
        outcomes=[
            OutcomeCountOut(
                outcome=outcome,
                label=OUTCOME_LABELS[outcome],
                count=summary.outcome_counts.get(outcome, 0),
                percent_of_engaged=safe_rate(
                    summary.outcome_counts.get(outcome, 0), summary.engaged
                ),
            )
            for outcome in OUTCOMES
        ],
        daily=[
            DayPointOut(
                day=point.day,
                started=point.started,
                engaged=point.engaged,
                booking=point.booking,
                callback=point.callback,
                handover=point.handover,
                resolved=point.resolved,
                abandoned=point.abandoned,
            )
            for point in daily_series(rows, period.start, period.end)
        ],
        top_gaps=[
            TopGapOut(
                gap_id=gap[0],
                question=gap[1],
                reason=gap[2],
                asks=int(gap[3]),
                last_asked_at=gap[4],
            )
            for gap in gaps
        ],
        note=NOTE,
    )


async def export_summary_csv(client_id: UUID | None, period: Period) -> str:
    summary, _ = await summary_for(client_id, period)
    return summary_csv(
        summary,
        start=period.start.isoformat(),
        end=period.end.isoformat(),
        timezone=period.timezone,
    )


def _status(row_status: str) -> str:
    return "ended" if row_status == "ended" else "in_progress"


def _item(row: tuple) -> ConversationItemOut:
    (session_id, started_at, closed_at, status, conversation_type, outcome, reason, engaged,
     visitor, bots, unanswered, duration) = row  # fmt: skip
    return ConversationItemOut(
        session_id=session_id,
        started_at=started_at,
        ended_at=closed_at,
        duration_seconds=duration,
        status=_status(status),
        conversation_type=conversation_type,
        outcome=outcome,
        outcome_label=OUTCOME_LABELS.get(outcome) if outcome else None,
        reason=reason,
        engaged=engaged,
        visitor_messages=visitor,
        bot_replies=bots,
        unanswered_questions=unanswered,
    )


async def list_conversations(
    client_id: UUID,
    period: Period,
    *,
    outcome: str | None,
    phone_hash: str | None,
    limit: int,
    offset: int,
) -> ConversationListOut:
    start, end = period.bounds()
    rows, total = await db.list_conversations(
        client_id, start, end, outcome=outcome, phone_hash=phone_hash, limit=limit, offset=offset
    )
    return ConversationListOut(
        items=[_item(row) for row in rows], total=total, limit=limit, offset=offset
    )


async def export_conversations_csv(client_id: UUID | None, period: Period) -> str:
    """The outcome list for the range, with no names, phone numbers or message text."""
    if client_id is None:
        raise ValueError("Choose a client to export its conversation list")
    start, end = period.bounds()
    rows, total = await db.list_conversations(
        client_id, start, end, outcome=None, phone_hash=None, limit=EXPORT_ROW_LIMIT + 1, offset=0
    )
    if total > EXPORT_ROW_LIMIT:
        raise ValueError(
            f"This range has more than {EXPORT_ROW_LIMIT} conversations. Choose a shorter range."
        )
    items = [_item(row).model_dump() for row in rows]
    return conversations_csv(items)


def _mask_from_token(token: object) -> str | None:
    try:
        return mask_phone(decrypt_phone(bytes(token)))
    except ContactStorageNotConfigured:
        return None


async def patient_groups(
    client_id: UUID, period: Period, *, limit: int, offset: int
) -> PatientGroupListOut:
    start, end = period.bounds()
    rows, total = await db.patient_groups(client_id, start, end, limit=limit, offset=offset)
    keys = [row[0].strip() for row in rows]
    # Only the current page's contacts are read, and only their phone is decrypted.
    contacts = await db.latest_contacts(client_id, keys)
    groups: list[PatientGroupOut] = []
    for (raw_hash, conversations, first_seen, last_seen), key in zip(rows, keys, strict=True):
        contact = contacts.get(key)
        groups.append(
            PatientGroupOut(
                patient_key=key[:16],
                masked_phone=_mask_from_token(contact[1]) if contact else None,
                name=contact[0] if contact else None,
                conversations=int(conversations),
                first_seen_at=first_seen,
                last_seen_at=last_seen,
                latest_session_id=contact[2] if contact else None,
            )
        )
    return PatientGroupListOut(groups=groups, total=total, limit=limit, offset=offset)


async def conversation_detail(
    client_id: UUID, session_id: UUID, graph
) -> ConversationDetailOut | None:
    """The transcript and outcome of one conversation. None when it is not this client's, or is
    a test conversation. Raises if the saved conversation cannot be read: the route then refuses
    to answer rather than show a partial transcript."""
    row = await db.conversation_row(client_id, session_id)
    if row is None:
        return None
    (sid, thread_id, started_at, closed_at, status, conversation_type, outcome, reason,
     engaged, visitor, bots, unanswered, duration) = row  # fmt: skip

    snapshot = await graph.aget_state({"configurable": {"thread_id": str(thread_id)}})
    lines = conversation_lines(list((snapshot.values or {}).get("messages") or []))

    gap_rows = await db.gap_events_for_session(session_id)
    staff = await db.staff_messages_for_thread(session_id)
    contact_row = await db.contact_for_session(session_id)
    contact = None
    if contact_row is not None:
        contact = ContactOut(name=contact_row[0], masked_phone=_mask_from_token(contact_row[1]))

    return ConversationDetailOut(
        session_id=sid,
        started_at=started_at,
        ended_at=closed_at,
        status=_status(status),
        conversation_type=conversation_type,
        outcome=outcome,
        outcome_label=OUTCOME_LABELS.get(outcome) if outcome else None,
        reason=reason,
        engaged=engaged,
        visitor_messages=visitor,
        bot_replies=bots,
        duration_seconds=duration,
        unanswered=[UnansweredOut(question=q, asked_at=t) for q, t in gap_rows],
        contact=contact,
        transcript=[
            TranscriptLineOut(position=line.position, role=line.role, text=line.text)
            for line in lines
        ],
        staff_messages=[
            StaffMessageOut(sender=sender, body=body, created_at=created)
            for sender, body, created in staff
        ],
        notice=(
            "Viewing this conversation is recorded in the audit log. Visitor messages can contain "
            "personal health details. Do not copy them outside the practice's systems."
        ),
    )
