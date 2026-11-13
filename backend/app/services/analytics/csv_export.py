"""CSV exports that carry no raw transcript text and no decrypted PII.

The summary is aggregate numbers only. The conversation list has dates, outcome labels, reasons,
message counts and durations. It has no names, no phone numbers or phone hashes, no questions
(which can contain health details), and no message text.

A cell a spreadsheet would read as a formula gets a leading apostrophe.
"""

import csv
import io
from collections.abc import Iterable

from app.services.analytics.metrics import Summary

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

SUMMARY_COLUMNS = ("metric", "value")
CONVERSATION_COLUMNS = (
    "session_id",
    "started_at",
    "ended_at",
    "status",
    "conversation_type",
    "outcome",
    "outcome_reason",
    "engaged",
    "visitor_messages",
    "bot_replies",
    "unanswered_questions",
    "duration_seconds",
)


def safe_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text.startswith(FORMULA_PREFIXES):
        return "'" + text
    return text


def _write(columns: tuple[str, ...], rows: Iterable[Iterable[object]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([safe_cell(cell) for cell in row])
    return buffer.getvalue()


def _percent(value: float | None) -> str:
    return "" if value is None else f"{value * 100:.1f}%"


def summary_csv(summary: Summary, *, start: str, end: str, timezone: str) -> str:
    counts = summary.outcome_counts
    rows = [
        ("period_start", start),
        ("period_end", end),
        ("timezone", timezone),
        ("started", summary.started),
        ("ended", summary.ended),
        ("in_progress", summary.in_progress),
        ("engaged", summary.engaged),
        ("bounced", summary.bounced),
        ("booking", counts.get("booking", 0)),
        ("callback", counts.get("callback", 0)),
        ("handover", counts.get("handover", 0)),
        ("resolved", counts.get("resolved", 0)),
        ("abandoned", counts.get("abandoned", 0)),
        ("booking_rate", _percent(summary.booking_rate)),
        ("callback_rate", _percent(summary.callback_rate)),
        ("handover_rate", _percent(summary.handover_rate)),
        ("resolution_rate", _percent(summary.resolution_rate)),
        ("abandonment_rate", _percent(summary.abandonment_rate)),
        ("average_duration_seconds", summary.average_duration_seconds or ""),
        ("handovers_created", summary.handovers_created),
        ("handovers_accepted", summary.handovers_accepted),
        ("handovers_timed_out", summary.handovers_timed_out),
    ]
    return _write(SUMMARY_COLUMNS, rows)


def conversations_csv(rows: Iterable[dict]) -> str:
    """rows: dicts from the conversation list. Only the keys read here are exported."""
    return _write(
        CONVERSATION_COLUMNS,
        (
            [
                row["session_id"],
                row["started_at"],
                row.get("ended_at"),
                row["status"],
                row["conversation_type"],
                row.get("outcome_label"),
                row.get("reason"),
                row.get("engaged"),
                row.get("visitor_messages"),
                row.get("bot_replies"),
                row.get("unanswered_questions"),
                row.get("duration_seconds"),
            ]
            for row in rows
        ),
    )
