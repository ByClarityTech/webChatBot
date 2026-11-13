"""Booking refinements (FR-16, FR-17, FR-31, FR-32, FR-33).

Covers: the 7-day slot suggestions (one calendar query, business days, notice, busy times, the
three earliest), the 60-second cache, the 1.5-second timeout that returns no suggestions, the
booking route's required current-patient answer, the coded 409s (SLOT_TAKEN vs SESSION_ENDED),
the related-question chips (starred, not yet asked, capped, never repeated), and the admin
"not recorded" answer for old bookings.

The calendar and database calls are replaced by fakes.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from auth_helpers import make_client_record
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError

from app.api.routes import booking as booking_routes
from app.api.routes import chat as chat_routes
from app.api.routes.admin import bookings as admin_bookings
from app.booking import suggestions
from app.booking.availability import SlotNotAvailableError
from app.booking.schemas import Slot
from app.booking.service import BookingConflictError
from app.integrations.google.schemas import BusyPeriod
from app.schemas.chat import ChatResponse
from app.services.related_questions import asked_texts, pick_related, question_key

CLINIC = make_client_record("clinic")
# 2030-07-01 is a Monday. Hours 09:00 to 17:00 UTC, 30-minute slots, Monday to Friday.
MONDAY_8AM = datetime(2030, 7, 1, 8, 0, tzinfo=UTC)
CONFIG = SimpleNamespace(
    business_days=[1, 2, 3, 4, 5],
    business_hours_start="09:00",
    business_hours_end="17:00",
    booking_slot_duration_minutes=30,
    booking_slot_buffer_minutes=0,
    booking_timezone="UTC",
    source="env",
    booking_min_notice_minutes=0,
)


# ---------------------------------------------------------------- suggestions


class CalendarFake:
    def __init__(self, busy=None, bookings=None) -> None:
        self.busy = busy or []
        self.bookings = bookings or []
        self.freebusy_calls = 0

    async def freebusy(self, **kwargs):
        self.freebusy_calls += 1
        return SimpleNamespace(busy=self.busy)

    async def blocking(self, client_id, **kwargs):
        return self.bookings


@pytest.fixture
def calendar(monkeypatch):
    fake = CalendarFake()
    monkeypatch.setattr(
        suggestions, "get_client", AsyncMock(return_value=SimpleNamespace(calendar_mode="google"))
    )
    monkeypatch.setattr(suggestions, "is_google_calendar_connected", AsyncMock(return_value=True))
    monkeypatch.setattr(suggestions, "get_business_hours_config", AsyncMock(return_value=CONFIG))
    monkeypatch.setattr(suggestions, "query_freebusy", fake.freebusy)
    monkeypatch.setattr(suggestions, "list_blocking_bookings", fake.blocking)
    suggestions.clear_suggestion_cache()
    yield fake
    suggestions.clear_suggestion_cache()


async def test_returns_the_three_earliest_open_slots_skipping_busy_time(calendar):
    calendar.busy = [
        BusyPeriod(
            start=datetime(2030, 7, 1, 9, 0, tzinfo=UTC),
            end=datetime(2030, 7, 1, 9, 30, tzinfo=UTC),
        )
    ]
    slots = await suggestions.compute_suggestions(CLINIC.id, now=MONDAY_8AM)
    assert [s.start for s in slots] == [
        datetime(2030, 7, 1, 9, 30, tzinfo=UTC),
        datetime(2030, 7, 1, 10, 0, tzinfo=UTC),
        datetime(2030, 7, 1, 10, 30, tzinfo=UTC),
    ]


async def test_one_calendar_query_covers_the_whole_week(calendar):
    await suggestions.compute_suggestions(CLINIC.id, now=MONDAY_8AM)
    assert calendar.freebusy_calls == 1


async def test_weekends_are_skipped_and_the_next_week_is_used(calendar, monkeypatch):
    friday_evening = datetime(2030, 7, 5, 18, 0, tzinfo=UTC)
    slots = await suggestions.compute_suggestions(CLINIC.id, now=friday_evening)
    # Friday is over, the weekend is closed, so the first slot is Monday 9 am.
    assert slots[0].start == datetime(2030, 7, 8, 9, 0, tzinfo=UTC)


async def test_minimum_notice_moves_the_first_slot_later(calendar, monkeypatch):
    monkeypatch.setattr(
        suggestions,
        "get_business_hours_config",
        AsyncMock(
            return_value=SimpleNamespace(**{**CONFIG.__dict__, "booking_min_notice_minutes": 90})
        ),
    )
    slots = await suggestions.compute_suggestions(CLINIC.id, now=MONDAY_8AM)
    assert slots[0].start == datetime(2030, 7, 1, 9, 30, tzinfo=UTC)


async def test_no_more_than_three_suggestions(calendar):
    slots = await suggestions.compute_suggestions(CLINIC.id, now=MONDAY_8AM)
    assert len(slots) == 3


async def test_results_are_cached_for_a_minute_per_client(calendar):
    first = await suggestions.suggest_slots(CLINIC.id, now=MONDAY_8AM)
    second = await suggestions.suggest_slots(CLINIC.id, now=MONDAY_8AM)
    assert first == second
    assert calendar.freebusy_calls == 1


async def test_a_failed_lookup_is_not_cached(calendar, monkeypatch):
    monkeypatch.setattr(
        suggestions, "query_freebusy", AsyncMock(side_effect=RuntimeError("calendar down"))
    )
    with pytest.raises(RuntimeError):
        await suggestions.suggest_slots(CLINIC.id, now=MONDAY_8AM)
    assert CLINIC.id not in suggestions._cache


async def test_calendar_not_connected_is_reported(calendar, monkeypatch):
    monkeypatch.setattr(suggestions, "is_google_calendar_connected", AsyncMock(return_value=False))
    with pytest.raises(suggestions.CalendarNotConnectedError):
        await suggestions.compute_suggestions(CLINIC.id, now=MONDAY_8AM)


# ---------------------------------------------------------------- suggestions route


@pytest.fixture
def booking_app(monkeypatch):
    async def get_client_by_slug(slug):
        return CLINIC if slug == CLINIC.slug else None

    monkeypatch.setattr("app.services.public_client.get_client_by_slug", get_client_by_slug)
    monkeypatch.setattr(
        "app.api.routes.booking.get_chat_thread_client", AsyncMock(return_value=CLINIC.id)
    )
    monkeypatch.setattr(
        "app.api.routes.booking.get_business_hours_config",
        AsyncMock(return_value=SimpleNamespace(booking_timezone="Europe/London")),
    )
    session = SimpleNamespace(type="known", session_id=uuid4(), thread_id=uuid4())
    monkeypatch.setattr(
        "app.api.routes.booking.active_session_for", AsyncMock(return_value=session)
    )
    test_app = FastAPI()
    test_app.include_router(booking_routes.router, prefix="/api/v1")
    return test_app


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_suggestions_route_returns_the_slots(booking_app, monkeypatch):
    slot = Slot(start=MONDAY_8AM, end=MONDAY_8AM + timedelta(minutes=30))
    monkeypatch.setattr(booking_routes, "suggest_slots", AsyncMock(return_value=[slot]))
    async with _client(booking_app) as http:
        response = await http.get("/api/v1/booking/suggestions", params={"client": "clinic"})
    assert response.status_code == 200
    assert len(response.json()["slots"]) == 1
    assert response.json()["timezone"] == "Europe/London"


async def test_a_slow_calendar_gives_no_suggestions_within_the_timeout(booking_app, monkeypatch):
    import asyncio

    async def slow(client_id):
        await asyncio.sleep(1)
        return [Slot(start=MONDAY_8AM, end=MONDAY_8AM)]

    monkeypatch.setattr(booking_routes, "suggest_slots", slow)
    monkeypatch.setattr(booking_routes, "TIMEOUT_SECONDS", 0.05)
    async with _client(booking_app) as http:
        response = await http.get("/api/v1/booking/suggestions", params={"client": "clinic"})
    assert response.status_code == 200
    assert response.json()["slots"] == []


async def test_a_calendar_error_gives_no_suggestions(booking_app, monkeypatch):
    monkeypatch.setattr(
        booking_routes,
        "suggest_slots",
        AsyncMock(side_effect=suggestions.CalendarNotConnectedError("off")),
    )
    async with _client(booking_app) as http:
        response = await http.get("/api/v1/booking/suggestions", params={"client": "clinic"})
    assert response.status_code == 200
    assert response.json()["slots"] == []


# ---------------------------------------------------------------- booking: answer and codes


BODY = {
    "client": "clinic",
    "name": "Jane Doe",
    "phone": "+15551234567",
    "email": "jane@example.com",
    "slot_start": "2030-07-02T10:00:00+05:30",
    "thread_id": str(uuid4()),
}


async def test_a_booking_without_the_current_patient_answer_is_refused(booking_app):
    async with _client(booking_app) as http:
        response = await http.post("/api/v1/booking", json=BODY)
    assert response.status_code == 400
    assert response.json()["code"] == "CURRENT_PATIENT_REQUIRED"


async def test_the_answer_is_passed_on_to_the_booking(booking_app, monkeypatch):
    create = AsyncMock(side_effect=SlotNotAvailableError("taken"))
    monkeypatch.setattr(booking_routes, "create_booking", create)
    async with _client(booking_app) as http:
        await http.post("/api/v1/booking", json={**BODY, "is_current_patient": True})
    assert create.await_args.kwargs["is_current_patient"] is True


async def test_a_taken_slot_is_a_409_with_the_slot_taken_code(booking_app, monkeypatch):
    monkeypatch.setattr(
        booking_routes, "create_booking", AsyncMock(side_effect=BookingConflictError("taken"))
    )
    async with _client(booking_app) as http:
        response = await http.post("/api/v1/booking", json={**BODY, "is_current_patient": False})
    assert response.status_code == 409
    assert response.json() == {"detail": "taken", "code": "SLOT_TAKEN"}


async def test_an_ended_conversation_is_a_409_with_the_session_ended_code(booking_app, monkeypatch):
    monkeypatch.setattr(booking_routes, "active_session_for", AsyncMock(return_value=None))
    async with _client(booking_app) as http:
        response = await http.post("/api/v1/booking", json={**BODY, "is_current_patient": False})
    assert response.status_code == 409
    assert response.json()["code"] == "SESSION_ENDED"


# ---------------------------------------------------------------- related questions


def test_starred_questions_come_in_order_and_are_capped_at_three():
    chosen = pick_related(["Q1?", "Q2?", "Q3?", "Q4?"], [], "something else")
    assert chosen == ["Q1?", "Q2?", "Q3?"]


def test_questions_already_asked_are_skipped_whatever_the_case_or_punctuation():
    chosen = pick_related(
        ["Are you open on Sunday?", "Do you take insurance?", "Where are you?"],
        ["are you open on sunday"],
        "hi",
    )
    assert chosen == ["Do you take insurance?", "Where are you?"]


def test_the_question_just_sent_is_not_offered_back():
    assert pick_related(["Do you take insurance?"], [], "do you take insurance") == []


def test_duplicate_starred_questions_are_offered_once():
    assert pick_related(["Hours?", "hours?", "Parking?"], [], "x") == ["Hours?", "Parking?"]


def test_no_starred_questions_means_no_chips():
    assert pick_related([], [], "x") == []


def test_only_the_visitors_own_messages_count_as_asked():
    messages = [
        HumanMessage(content="Do you take insurance?"),
        AIMessage(content="Yes we do."),
        HumanMessage(content="Hours?"),
    ]
    assert asked_texts(messages) == ["Do you take insurance?", "Hours?"]


def test_question_key_ignores_case_spacing_and_punctuation():
    assert question_key("  Are you OPEN, on Sunday?? ") == "are you open on sunday"


def test_the_response_cannot_carry_more_than_three_chips():
    with pytest.raises(ValidationError):
        ChatResponse(
            reply="x",
            thread_id="t",
            sources=[],
            related_questions=["a", "b", "c", "d"],
        )


def test_a_response_without_chips_has_none():
    response = ChatResponse(reply="x", thread_id="t", sources=[])
    assert response.related_questions == []


async def test_chips_are_read_from_the_practice_starred_list(monkeypatch):
    monkeypatch.setattr(
        chat_routes,
        "list_default_questions",
        AsyncMock(return_value=["Hours?", "Parking?", "Insurance?"]),
    )
    chips = await chat_routes._related_chips(CLINIC, asked=["Hours?"], current="Parking?")
    assert chips == ["Insurance?"]


async def test_a_failed_starred_list_gives_no_chips(monkeypatch):
    monkeypatch.setattr(
        chat_routes, "list_default_questions", AsyncMock(side_effect=ConnectionError("down"))
    )
    assert await chat_routes._related_chips(CLINIC, asked=[], current="x") == []


# ---------------------------------------------------------------- admin view


def test_an_old_booking_shows_no_answer_rather_than_a_guess():
    record = SimpleNamespace(
        id=uuid4(),
        name="Jane",
        phone="+15551234567",
        email="jane@example.com",
        slot_start=MONDAY_8AM,
        slot_end=MONDAY_8AM,
        status="confirmed",
        created_at=MONDAY_8AM,
        google_event_id=None,
        is_current_patient=None,
    )
    assert admin_bookings._to_detail(record).is_current_patient is None
