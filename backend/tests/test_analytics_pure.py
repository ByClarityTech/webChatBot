"""Analytics, pure rules: outcome priority, idle endings, last-turn detection, formulas, periods,
the export's cell sanitizing, and the transcript lines. No database."""

from datetime import date

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.services.analytics.csv_export import CONVERSATION_COLUMNS, conversations_csv, safe_cell
from app.services.analytics.filters import MAX_RANGE_DAYS, resolve_period
from app.services.analytics.metrics import (
    HandoverCounts,
    OutcomeRow,
    daily_series,
    safe_rate,
    summarize,
)
from app.services.analytics.outcomes import (
    ABANDONED,
    BOOKING,
    CALLBACK,
    HANDOVER,
    LABELER_VERSION,
    REASON_BOUNCE,
    REASON_FALLBACK,
    REASON_RESOLVED,
    RESOLVED,
    SessionFacts,
    classify,
    count_turns,
    last_turn,
)
from app.services.analytics.transcript import conversation_lines, mask_phone

TODAY = date(2026, 11, 13)


def facts(**overrides) -> SessionFacts:
    base = dict(
        visitor_messages=3,
        bot_replies=3,
        last_turn="answer",
        has_booking=False,
        has_callback=False,
        has_handover=False,
        unanswered_questions=0,
        duration_seconds=120,
    )
    base.update(overrides)
    return SessionFacts(**base)


# ---------------------------------------------------------------- outcome priority


def test_a_booking_beats_everything_else():
    result = classify(
        facts(has_booking=True, has_callback=True, has_handover=True, last_turn="fallback")
    )
    assert result.outcome == BOOKING


def test_a_callback_beats_a_handover_and_an_answer():
    assert classify(facts(has_callback=True, has_handover=True)).outcome == CALLBACK


def test_a_handover_beats_a_clean_answer():
    assert classify(facts(has_handover=True)).outcome == HANDOVER


def test_a_booking_beats_a_fallback_ending():
    assert classify(facts(has_booking=True, last_turn="fallback")).outcome == BOOKING


def test_an_answer_ending_is_resolved():
    result = classify(facts(last_turn="answer"))
    assert (result.outcome, result.reason, result.engaged) == (RESOLVED, REASON_RESOLVED, True)


# ---------------------------------------------------------------- idle endings (approved rule)


def test_idle_after_an_answer_is_resolved():
    # The label does not depend on how the conversation ended; only on the last reply.
    assert classify(facts(last_turn="answer")).outcome == RESOLVED


def test_idle_after_an_unanswered_fallback_is_abandoned():
    result = classify(facts(last_turn="fallback"))
    assert (result.outcome, result.reason) == (ABANDONED, REASON_FALLBACK)


def test_no_assistant_reply_at_all_is_abandoned():
    assert classify(facts(last_turn="none")).outcome == ABANDONED


def test_a_safety_message_as_the_last_reply_is_abandoned():
    assert classify(facts(last_turn="safety")).outcome == ABANDONED


def test_a_booking_question_left_open_is_abandoned():
    assert classify(facts(last_turn="booking")).outcome == ABANDONED


# ---------------------------------------------------------------- bounces and denominators


def test_a_conversation_with_no_visitor_message_is_a_bounce():
    result = classify(facts(visitor_messages=0, bot_replies=1, last_turn="answer"))
    assert (result.outcome, result.reason, result.engaged) == (ABANDONED, REASON_BOUNCE, False)


def test_a_bounce_with_a_booking_still_counts_as_the_booking():
    # Not realistic, but the rule is priority first: a recorded booking is a booking.
    result = classify(facts(visitor_messages=0, has_booking=True))
    assert result.outcome == BOOKING
    assert result.engaged is False


def test_engaged_means_at_least_one_visitor_message():
    assert classify(facts(visitor_messages=1)).engaged is True


# ---------------------------------------------------------------- last-turn detection


def test_last_turn_is_none_when_the_visitor_spoke_last():
    assert last_turn({"messages": [AIMessage(content="hi"), HumanMessage(content="?")]}) == "none"


def test_last_turn_is_none_for_an_empty_conversation():
    assert last_turn({}) == "none"


def test_last_turn_answer_when_the_model_answered():
    values = {"messages": [HumanMessage(content="hours?"), AIMessage(content="9 to 5")],
              "answer_declined": False, "intent": "faq"}  # fmt: skip
    assert last_turn(values) == "answer"


def test_last_turn_fallback_when_the_answer_was_declined():
    values = {"messages": [HumanMessage(content="x"), AIMessage(content="sorry")],
              "answer_declined": True, "intent": "faq"}  # fmt: skip
    assert last_turn(values) == "fallback"


def test_a_stale_fallback_flag_does_not_override_a_booking_turn():
    # answer_declined was True on an earlier turn; this turn was a booking question.
    values = {"messages": [HumanMessage(content="book"), AIMessage(content="which day?")],
              "answer_declined": True, "intent": "booking"}  # fmt: skip
    assert last_turn(values) == "booking"


def test_a_stale_fallback_flag_does_not_override_a_safety_turn():
    values = {"messages": [HumanMessage(content="chest pain"), AIMessage(content="call 911")],
              "answer_declined": True, "intent": "faq", "safety": "urgent"}  # fmt: skip
    assert last_turn(values) == "safety"


def test_count_turns_counts_visitor_and_assistant_messages():
    messages = [HumanMessage(content="a"), AIMessage(content="b"), HumanMessage(content="c")]
    assert count_turns(messages) == (2, 1)


def test_the_labeler_version_is_set():
    assert LABELER_VERSION == 1


# ---------------------------------------------------------------- formulas


def row(
    outcome: str, *, engaged: bool = True, duration: int = 100, day: date = TODAY
) -> OutcomeRow:
    return OutcomeRow(day=day, outcome=outcome, engaged=engaged, duration_seconds=duration)


def test_safe_rate_is_none_without_a_denominator_and_rounds_otherwise():
    assert safe_rate(1, 0) is None
    assert safe_rate(0, 0) is None
    assert safe_rate(1, 3) == 0.3333


def test_outcome_counts_add_up_to_engaged():
    rows = [row(BOOKING), row(CALLBACK), row(HANDOVER), row(RESOLVED), row(ABANDONED)]
    summary = summarize(rows, in_progress=0, handovers=HandoverCounts(0, 0, 0))
    assert sum(summary.outcome_counts.values()) == summary.engaged == 5


def test_bounces_are_not_engaged_and_do_not_move_the_rates():
    rows = [row(BOOKING), row(ABANDONED, engaged=False), row(ABANDONED, engaged=False)]
    summary = summarize(rows, in_progress=0, handovers=HandoverCounts(0, 0, 0))
    assert (summary.ended, summary.engaged, summary.bounced) == (3, 1, 2)
    assert summary.booking_rate == 1.0
    assert summary.abandonment_rate == 0.0


def test_in_progress_conversations_count_as_started_but_not_in_any_rate():
    rows = [row(RESOLVED)]
    summary = summarize(rows, in_progress=4, handovers=HandoverCounts(0, 0, 0))
    assert summary.started == 5
    assert summary.in_progress == 4
    assert summary.resolution_rate == 1.0  # 1 of 1 engaged ended conversations


def test_rates_are_none_when_nothing_has_ended():
    summary = summarize([], in_progress=2, handovers=HandoverCounts(0, 0, 0))
    assert summary.booking_rate is None
    assert summary.resolution_rate is None
    assert summary.average_duration_seconds is None


def test_each_rate_uses_engaged_as_the_denominator():
    rows = [
        row(BOOKING),
        row(CALLBACK),
        row(HANDOVER),
        row(RESOLVED),
        row(ABANDONED),
        row(ABANDONED),
    ]
    summary = summarize(rows, in_progress=0, handovers=HandoverCounts(0, 0, 0))
    assert summary.booking_rate == round(1 / 6, 4)
    assert summary.callback_rate == round(1 / 6, 4)
    assert summary.handover_rate == round(1 / 6, 4)
    assert summary.resolution_rate == round(1 / 6, 4)
    assert summary.abandonment_rate == round(2 / 6, 4)


def test_average_duration_uses_engaged_conversations_only():
    rows = [
        row(RESOLVED, duration=100),
        row(RESOLVED, duration=200),
        row(ABANDONED, engaged=False, duration=9999),
    ]
    assert (
        summarize(rows, in_progress=0, handovers=HandoverCounts(0, 0, 0)).average_duration_seconds
        == 150.0
    )


def test_handover_accept_and_timeout_rates_use_handovers_created():
    summary = summarize(
        [], in_progress=0, handovers=HandoverCounts(created=4, accepted=3, timed_out=1)
    )
    assert summary.handover_accept_rate == 0.75
    assert summary.handover_timeout_rate == 0.25


def test_handover_rates_are_none_with_no_handovers():
    summary = summarize([], in_progress=0, handovers=HandoverCounts(0, 0, 0))
    assert summary.handover_accept_rate is None


def test_daily_series_fills_empty_days_and_ignores_days_outside_the_range():
    rows = [
        row(BOOKING, day=date(2026, 11, 2)),
        row(ABANDONED, engaged=False, day=date(2026, 11, 3)),
        row(RESOLVED, day=date(2026, 10, 1)),  # outside the range: ignored
    ]
    series = daily_series(rows, date(2026, 11, 1), date(2026, 11, 3))
    assert [point.day for point in series] == [
        date(2026, 11, 1),
        date(2026, 11, 2),
        date(2026, 11, 3),
    ]
    assert (series[1].started, series[1].booking) == (1, 1)
    assert (series[2].started, series[2].engaged, series[2].abandoned) == (1, 0, 0)
    assert sum(point.started for point in series) == 2


# ---------------------------------------------------------------- periods


def test_a_preset_ends_today():
    period = resolve_period(preset="7d", start=None, end=None, timezone="UTC", today=TODAY)
    assert (period.start, period.end, period.days) == (date(2026, 11, 7), TODAY, 7)


@pytest.mark.parametrize("preset", ["1d", "365d", "", "all"])
def test_unknown_presets_are_refused(preset):
    with pytest.raises(ValueError):
        resolve_period(preset=preset, start=None, end=None, timezone="UTC", today=TODAY)


def test_a_preset_and_a_custom_range_together_are_refused():
    with pytest.raises(ValueError):
        resolve_period(preset="7d", start=TODAY, end=TODAY, timezone="UTC", today=TODAY)


def test_a_custom_range_needs_both_ends():
    with pytest.raises(ValueError):
        resolve_period(preset=None, start=TODAY, end=None, timezone="UTC", today=TODAY)


def test_an_end_before_the_start_is_refused():
    with pytest.raises(ValueError):
        resolve_period(preset=None, start=TODAY, end=date(2026, 11, 1), timezone="UTC", today=TODAY)


def test_a_custom_range_up_to_366_days_is_allowed_and_one_more_is_refused():
    start = date(2025, 11, 13)
    allowed = resolve_period(preset=None, start=start, end=TODAY, timezone="UTC", today=TODAY)
    assert allowed.days == MAX_RANGE_DAYS == 366
    with pytest.raises(ValueError):
        resolve_period(
            preset=None, start=date(2025, 11, 12), end=TODAY, timezone="UTC", today=TODAY
        )


def test_bounds_are_local_midnights_converted_to_utc():
    period = resolve_period(
        preset=None, start=TODAY, end=TODAY, timezone="Asia/Kolkata", today=TODAY
    )
    start, end = period.bounds()
    assert start.isoformat() == "2026-11-12T18:30:00+00:00"
    assert end.isoformat() == "2026-11-13T18:30:00+00:00"


# ---------------------------------------------------------------- export sanitizing


@pytest.mark.parametrize("cell", ['=HYPERLINK("x")', "+1+1", "-2+3", "@SUM(A1)", "\tcmd"])
def test_cells_that_spreadsheets_read_as_formulas_are_neutralised(cell):
    assert safe_cell(cell) == "'" + cell


def test_plain_text_and_numbers_are_not_changed():
    assert safe_cell("Booking") == "Booking"
    assert safe_cell(7) == "7"
    assert safe_cell(None) == ""
    assert safe_cell(True) == "yes"


def test_the_conversation_export_has_no_names_phones_hashes_or_message_text():
    rows = [
        {
            "session_id": "1f2e3d4c-0000-0000-0000-000000000001",
            "started_at": "2026-11-10T10:00:00+00:00",
            "ended_at": "2026-11-10T10:05:00+00:00",
            "status": "ended",
            "conversation_type": "known",
            "outcome_label": "Booking",
            "reason": "Booking confirmed",
            "engaged": True,
            "visitor_messages": 4,
            "bot_replies": 4,
            "unanswered_questions": 0,
            "duration_seconds": 300,
        }
    ]
    body = conversations_csv(rows)
    header = body.splitlines()[0].split(",")
    assert tuple(header) == CONVERSATION_COLUMNS
    for forbidden in ("name", "phone", "hash", "transcript", "email"):
        assert not any(forbidden in column for column in header)
    assert "+1312" not in body and "Maria" not in body


# ---------------------------------------------------------------- transcript


def test_transcript_lines_keep_order_and_drop_empty_or_system_messages():
    messages = [
        HumanMessage(content="Are you open Sunday?"),
        AIMessage(content=""),
        AIMessage(content="No, we are closed on Sundays."),
        HumanMessage(content=[{"type": "text", "text": "Thanks"}]),
    ]
    lines = conversation_lines(messages)
    assert [(line.position, line.role) for line in lines] == [
        (1, "visitor"),
        (2, "assistant"),
        (3, "visitor"),
    ]
    assert lines[2].text == "Thanks"


def test_mask_phone_shows_only_the_last_four_digits():
    assert mask_phone("+13125550187") == "(***) ***-0187"
    assert mask_phone(None) is None
    assert mask_phone("12") is None
