"""Conversation sessions: the 30-minute idle rule, rollover, closing, isolation, test flag.

Time is moved with a fake clock and the database is replaced by an in-memory store.
"""

import dataclasses
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from auth_helpers import make_client_record
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage

from app.api.routes import chat as chat_routes
from app.db.models.chat_session import ChatSession
from app.services import chat_sessions as cs
from app.services.chat_sessions import IDLE_TIMEOUT, SessionHandle

T0 = datetime(2030, 7, 1, 10, 0, tzinfo=UTC)
CLIENT = uuid4()
OTHER_CLIENT = uuid4()


class Store:
    """In-memory replacement for the chat_sessions table and its helpers."""

    def __init__(self) -> None:
        self.sessions: dict = {}
        self.clock = T0

    def active_for(self, thread_id):
        return next(
            (
                s
                for s in self.sessions.values()
                if s.thread_id == thread_id and s.status == "active"
            ),
            None,
        )

    async def get_active(self, thread_id):
        return self.active_for(thread_id)

    async def start(self, *, session_id, thread_id, client_id, is_test_session, now):
        self.sessions[session_id] = ChatSession(
            session_id=session_id,
            thread_id=thread_id,
            client_id=client_id,
            type="anonymous",
            status="active",
            end_reason=None,
            consent_given=False,
            consent_text_version=None,
            is_test_session=is_test_session,
            started_at=now,
            last_activity_at=now,
            closed_at=None,
        )

    async def touch(self, session_id, now):
        self.sessions[session_id] = dataclasses.replace(
            self.sessions[session_id], last_activity_at=now
        )

    async def end(self, session_id, *, reason, closed_at):
        current = self.sessions[session_id]
        if current.status == "active":
            self.sessions[session_id] = dataclasses.replace(
                current, status="ended", end_reason=reason, closed_at=closed_at
            )


@pytest.fixture
def store(monkeypatch):
    s = Store()
    monkeypatch.setattr(cs, "get_active_session_for_thread", s.get_active)
    monkeypatch.setattr(cs, "start_session", s.start)
    monkeypatch.setattr(cs, "touch_session", s.touch)
    monkeypatch.setattr(cs, "end_session", s.end)
    monkeypatch.setattr(cs, "utc_now", lambda: s.clock)
    return s


async def open_session(thread_id=None, client=CLIENT, *, is_test=False) -> SessionHandle:
    return await cs.resolve_for_message(thread_id, client, is_test_session=is_test)


# ---------------------------------------------------------------- starting and continuing


async def test_first_message_opens_a_session_that_is_not_a_reset(store):
    handle = await open_session()
    assert handle.reset is False
    assert store.active_for(handle.thread_id).session_id == handle.session_id


async def test_message_within_thirty_minutes_continues_and_refreshes_activity(store):
    first = await open_session()
    store.clock = T0 + timedelta(minutes=29)
    second = await open_session(first.thread_id)
    assert second.session_id == first.session_id
    assert second.thread_id == first.thread_id
    assert second.reset is False
    assert store.sessions[first.session_id].last_activity_at == T0 + timedelta(minutes=29)


async def test_exactly_thirty_minutes_idle_still_continues(store):
    first = await open_session()
    store.clock = T0 + IDLE_TIMEOUT
    again = await open_session(first.thread_id)
    assert again.session_id == first.session_id
    assert again.reset is False


async def test_one_second_past_thirty_minutes_rolls_over_to_a_new_session(store):
    first = await open_session()
    store.clock = T0 + IDLE_TIMEOUT + timedelta(seconds=1)
    second = await open_session(first.thread_id)

    assert second.reset is True
    assert second.thread_id != first.thread_id
    assert second.session_id != first.session_id
    old = store.sessions[first.session_id]
    assert old.status == "ended"
    assert old.end_reason == "idle"


async def test_rollover_ends_the_old_session_where_its_activity_stopped(store):
    first = await open_session()
    store.clock = T0 + timedelta(minutes=10)
    await open_session(first.thread_id)  # last activity moves to minute 10
    store.clock = T0 + timedelta(minutes=50)
    await open_session(first.thread_id)

    assert store.sessions[first.session_id].closed_at == T0 + timedelta(minutes=40)


async def test_message_after_a_closed_session_starts_a_new_one_marked_reset(store):
    first = await open_session()
    assert await cs.close_session(first.thread_id, CLIENT) is True
    second = await open_session(first.thread_id)
    assert second.reset is True
    assert second.session_id != first.session_id
    assert store.sessions[first.session_id].end_reason == "closed"


# ---------------------------------------------------------------- isolation


async def test_another_clients_thread_starts_fresh_and_leaves_the_owner_alone(store):
    owner = await open_session(client=CLIENT)
    intruder = await open_session(owner.thread_id, client=OTHER_CLIENT)
    assert intruder.reset is True
    assert intruder.thread_id != owner.thread_id
    assert store.sessions[owner.session_id].status == "active"


async def test_close_refuses_a_foreign_or_unknown_thread(store):
    owner = await open_session(client=CLIENT)
    assert await cs.close_session(owner.thread_id, OTHER_CLIENT) is False
    assert await cs.close_session(uuid4(), CLIENT) is False
    assert store.sessions[owner.session_id].status == "active"


async def test_test_flag_is_fixed_for_the_whole_conversation(store):
    first = await open_session(is_test=True)
    later = await open_session(first.thread_id, is_test=False)
    assert later.session_id == first.session_id
    assert later.is_test_session is True


# ---------------------------------------------------------------- closing


async def test_close_ends_the_session_as_closed_and_only_once(store):
    first = await open_session()
    store.clock = T0 + timedelta(minutes=3)
    assert await cs.close_session(first.thread_id, CLIENT) is True
    ended = store.sessions[first.session_id]
    assert ended.end_reason == "closed"
    assert ended.closed_at == T0 + timedelta(minutes=3)
    assert await cs.close_session(first.thread_id, CLIENT) is False


async def test_closing_after_idle_records_idle_not_closed(store):
    first = await open_session()
    store.clock = T0 + timedelta(minutes=31)
    await cs.close_session(first.thread_id, CLIENT)
    assert store.sessions[first.session_id].end_reason == "idle"


async def test_status_lookup_reports_open_sessions_and_changes_nothing(store):
    first = await open_session()
    store.clock = T0 + timedelta(minutes=5)
    seen = await cs.active_session_for(first.thread_id, CLIENT)
    assert seen is not None and seen.session_id == first.session_id
    assert store.sessions[first.session_id].last_activity_at == T0

    store.clock = T0 + timedelta(minutes=31)
    assert await cs.active_session_for(first.thread_id, CLIENT) is None
    assert store.sessions[first.session_id].status == "active", "a read never ends a session"


# ---------------------------------------------------------------- chat route: test flag


@pytest.fixture
def chat_app(monkeypatch, store):
    client = make_client_record(slug="clinic")

    async def get_client_by_slug(slug):
        return client if slug == "clinic" else None

    monkeypatch.setattr("app.services.public_client.get_client_by_slug", get_client_by_slug)
    graph = MagicMock()
    graph.ainvoke = AsyncMock(
        return_value={"messages": [AIMessage(content="Hi")], "retrieved_docs": []}
    )
    app = FastAPI()
    app.state.agent_graph = graph
    app.include_router(chat_routes.router, prefix="/api/v1")
    app.state.client = client
    return app


async def _post(app, **body):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(
            "/api/v1/chat", json={"message": "hours?", "client": "clinic", **body}
        )


async def test_visitor_cannot_mark_a_session_as_test(chat_app, monkeypatch):
    spy = AsyncMock(side_effect=lambda *_a, **k: SessionHandle(uuid4(), uuid4(), False, False))
    monkeypatch.setattr(chat_routes, "resolve_for_message", spy)
    monkeypatch.setattr(chat_routes, "optional_admin_user", AsyncMock(return_value=None))

    response = await _post(chat_app, test_session=True)

    assert response.status_code == 200
    assert spy.await_args.kwargs["is_test_session"] is False


async def test_admin_session_may_mark_a_test(chat_app, monkeypatch):
    spy = AsyncMock(side_effect=lambda *_a, **k: SessionHandle(uuid4(), uuid4(), False, True))
    monkeypatch.setattr(chat_routes, "resolve_for_message", spy)
    monkeypatch.setattr(
        chat_routes, "optional_admin_user", AsyncMock(return_value=MagicMock(id=uuid4()))
    )

    await _post(chat_app, test_session=True)

    assert spy.await_args.kwargs["is_test_session"] is True


async def test_chat_response_reports_the_session_and_any_reset(chat_app, monkeypatch):
    session_id = uuid4()
    thread_id = uuid4()
    monkeypatch.setattr(
        chat_routes,
        "resolve_for_message",
        AsyncMock(return_value=SessionHandle(thread_id, session_id, True, False)),
    )
    response = await _post(chat_app)
    body = response.json()
    assert body["session_id"] == str(session_id)
    assert body["thread_id"] == str(thread_id)
    assert body["session_reset"] is True
