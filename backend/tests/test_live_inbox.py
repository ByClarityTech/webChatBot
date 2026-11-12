"""Staff inbox and live handover: presence, offline fallback, atomic acceptance, the 3-minute
timeout, reply rules, visitor routing, AI draft gating, callbacks, RBAC, and audit logging.

The live tables are replaced by a small in-memory store that applies the same conditions the
SQL does. The SQL itself needs a live database.
"""

import dataclasses
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from auth_helpers import make_client_record, make_user, signed_in
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage

from app.api.routes import chat as chat_routes
from app.api.routes import live_chat as live_chat_routes
from app.api.routes.admin import inbox as inbox_routes
from app.core.config import get_settings
from app.core.roles import UserType
from app.db.models.callback_request import SOURCE_NO_STAFF, SOURCE_TIMEOUT, CallbackRequest
from app.db.models.chat_session import ChatSession
from app.db.models.live_handover import LiveHandover, LiveMessage, OpenHandoverExists
from app.db.models.staff_presence import ONLINE_WINDOW
from app.services import live_chat as live
from app.services.broker import Broker, sse_message, stream_frames, visitor_channel

SA, ADMIN, SUPPORT = UserType.super_admin, UserType.admin, UserType.support
T0 = datetime(2030, 7, 1, 10, 0, tzinfo=UTC)
CLINIC = make_client_record("clinic")
CLINIC_ID = CLINIC.id


# ---------------------------------------------------------------- in-memory live tables


class LiveStore:
    def __init__(self) -> None:
        self.clock = T0
        self.handovers: dict = {}
        self.messages: list = []
        self.callbacks: list = []
        self.online = 0

    def open_for(self, session_id):
        return next(
            (
                h
                for h in self.handovers.values()
                if h.session_id == session_id and h.status in ("pending", "accepted")
            ),
            None,
        )

    async def online_staff_count(self, client_id, now):
        return self.online

    async def create_handover(
        self, *, session_id, client_id, requested_at, expires_at, stream_token_hash
    ):
        if self.open_for(session_id) is not None:
            raise OpenHandoverExists()
        handover = LiveHandover(
            id=uuid4(),
            session_id=session_id,
            client_id=client_id,
            status="pending",
            requested_at=requested_at,
            expires_at=expires_at,
            accepted_by=None,
            accepted_at=None,
            ended_at=None,
            end_reason=None,
            stream_token_hash=stream_token_hash,
        )
        self.handovers[handover.id] = handover
        return handover

    async def get_handover(self, handover_id):
        return self.handovers.get(handover_id)

    async def get_open_handover_for_session(self, session_id):
        return self.open_for(session_id)

    async def accept_handover(self, handover_id, *, client_id, user_id, now):
        h = self.handovers.get(handover_id)
        if h is None or h.client_id != client_id or h.status != "pending" or not h.expires_at > now:
            return False
        self.handovers[handover_id] = dataclasses.replace(
            h, status="accepted", accepted_by=user_id, accepted_at=now
        )
        return True

    async def end_handover(self, handover_id, *, reason, now):
        h = self.handovers.get(handover_id)
        if h is None or h.status not in ("pending", "accepted"):
            return False
        self.handovers[handover_id] = dataclasses.replace(
            h, status="ended", end_reason=reason, ended_at=now
        )
        return True

    async def expire_overdue_pending(self, now):
        moved = []
        for hid, h in list(self.handovers.items()):
            if h.status == "pending" and h.expires_at <= now:
                updated = dataclasses.replace(
                    h, status="timed_out", end_reason="timed_out", ended_at=now
                )
                self.handovers[hid] = updated
                moved.append(updated)
        return moved

    async def end_handovers_of_ended_sessions(self, now):
        return []

    async def insert_message(self, *, handover_id, client_id, sender, author_user_id, body):
        message = LiveMessage(
            id=uuid4(),
            handover_id=handover_id,
            sender=sender,
            author_user_id=author_user_id,
            body=body,
            created_at=self.clock,
        )
        self.messages.append(message)
        return message

    async def list_messages(self, handover_id):
        return [m for m in self.messages if m.handover_id == handover_id]

    async def ensure_open_callback(self, *, client_id, session_id, source, now):
        if session_id is not None and any(
            c.session_id == session_id and c.status == "open" for c in self.callbacks
        ):
            return None
        callback = CallbackRequest(
            id=uuid4(),
            client_id=client_id,
            session_id=session_id,
            status="open",
            source=source,
            created_at=now,
            handled_by=None,
            handled_at=None,
        )
        self.callbacks.append(callback)
        return callback


@pytest.fixture
def store(monkeypatch):
    s = LiveStore()
    for name in (
        "create_handover",
        "get_handover",
        "get_open_handover_for_session",
        "accept_handover",
        "end_handover",
        "expire_overdue_pending",
        "end_handovers_of_ended_sessions",
        "insert_message",
        "list_messages",
        "ensure_open_callback",
    ):
        monkeypatch.setattr(f"app.services.live_chat.{name}", getattr(s, name))
    monkeypatch.setattr("app.services.live_chat.online_staff_count", s.online_staff_count)
    monkeypatch.setattr("app.services.live_chat.prune_presence", AsyncMock())
    monkeypatch.setattr("app.services.live_chat.touch_session", AsyncMock())
    monkeypatch.setattr("app.services.live_chat.utc_now", lambda: s.clock)
    monkeypatch.setenv("LIVE_CHAT_ENABLED", "true")
    get_settings.cache_clear()
    yield s
    get_settings.cache_clear()


def _session(session_id=None, thread_id=None, client_id=CLINIC_ID) -> ChatSession:
    return ChatSession(
        session_id=session_id or uuid4(),
        thread_id=thread_id or uuid4(),
        client_id=client_id,
        type="anonymous",
        status="active",
        end_reason=None,
        consent_given=False,
        consent_text_version=None,
        is_test_session=False,
        started_at=T0,
        last_activity_at=T0,
        closed_at=None,
    )


# ---------------------------------------------------------------- presence and offline fallback


def test_the_online_window_is_sixty_seconds():
    assert ONLINE_WINDOW == timedelta(seconds=60)


async def test_offline_request_goes_straight_to_a_callback(store):
    session = _session()
    store.online = 0
    outcome = await live.request_live(session, CLINIC_ID)
    assert outcome.status == "callback"
    assert outcome.handover_id is None and outcome.token is None
    assert not store.handovers, "no handover waits for a person who is not there"
    assert store.callbacks[0].source == SOURCE_NO_STAFF


async def test_online_request_waits_for_a_person_with_a_token(store):
    store.online = 2
    session = _session()
    outcome = await live.request_live(session, CLINIC_ID)
    assert outcome.status == "pending"
    assert outcome.token and len(outcome.token) >= 43
    assert outcome.expires_at == T0 + timedelta(minutes=3)
    stored = store.handovers[outcome.handover_id]
    assert stored.stream_token_hash == live.token_hash(outcome.token), "only the hash is kept"
    assert outcome.token not in str(stored)


async def test_a_second_request_for_the_same_conversation_is_refused(store):
    store.online = 1
    session = _session()
    await live.request_live(session, CLINIC_ID)
    with pytest.raises(Exception) as raised:
        await live.request_live(session, CLINIC_ID)
    assert raised.value.status_code == 409


async def test_presence_route_returns_the_online_count_and_records_the_tab(monkeypatch):
    heartbeat = AsyncMock()
    monkeypatch.setattr(inbox_routes, "heartbeat", heartbeat)
    monkeypatch.setattr(inbox_routes, "online_staff_count", AsyncMock(return_value=3))
    monkeypatch.setattr("app.services.client_scope.get_client", AsyncMock(return_value=CLINIC))
    app = FastAPI()
    app.include_router(inbox_routes.router, prefix="/api/v1")
    actor = make_user(ADMIN)
    actor.client_id = CLINIC_ID
    with signed_in(app, ADMIN):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/admin/inbox/presence", json={"tab_id": "tab-abcdef12"}
            )
    assert response.status_code == 200
    assert response.json() == {"online": 3}
    assert heartbeat.await_args.kwargs["tab_id"] == "tab-abcdef12"


# ---------------------------------------------------------------- atomic acceptance and replies


async def test_only_one_of_two_racing_accepts_wins(store):
    store.online = 2
    outcome = await live.request_live(_session(), CLINIC_ID)
    first, second = make_user(SUPPORT), make_user(SUPPORT)
    first.client_id = second.client_id = CLINIC_ID
    await live.accept(outcome.handover_id, CLINIC_ID, first)
    with pytest.raises(Exception) as raised:
        await live.accept(outcome.handover_id, CLINIC_ID, second)
    assert raised.value.status_code == 409
    assert store.handovers[outcome.handover_id].accepted_by == first.id


async def test_a_late_accept_after_the_timeout_loses(store):
    store.online = 1
    outcome = await live.request_live(_session(), CLINIC_ID)
    store.clock = T0 + timedelta(minutes=3, seconds=1)
    staff = make_user(SUPPORT)
    staff.client_id = CLINIC_ID
    with pytest.raises(Exception) as raised:
        await live.accept(outcome.handover_id, CLINIC_ID, staff)
    assert raised.value.status_code == 409


async def test_another_clients_staff_cannot_accept(store):
    store.online = 1
    outcome = await live.request_live(_session(), CLINIC_ID)
    outsider = make_user(SUPPORT)
    outsider.client_id = uuid4()
    with pytest.raises(Exception) as raised:
        await live.accept(outcome.handover_id, outsider.client_id, outsider)
    assert raised.value.status_code == 404


async def test_only_the_accepter_may_reply_and_only_once_accepted(store):
    store.online = 1
    outcome = await live.request_live(_session(), CLINIC_ID)
    agent, other = make_user(SUPPORT), make_user(SUPPORT)
    agent.client_id = other.client_id = CLINIC_ID
    with pytest.raises(Exception) as early:
        await live.staff_reply(outcome.handover_id, CLINIC_ID, agent, "hello")
    assert early.value.status_code == 409
    await live.accept(outcome.handover_id, CLINIC_ID, agent)
    with pytest.raises(Exception) as stranger:
        await live.staff_reply(outcome.handover_id, CLINIC_ID, other, "hello")
    assert stranger.value.status_code == 403
    message = await live.staff_reply(
        outcome.handover_id, CLINIC_ID, agent, "  Hi, how can I help?  "
    )
    assert message.sender == "staff" and message.body == "Hi, how can I help?"


# ---------------------------------------------------------------- the 3-minute timeout


async def test_nobody_accepting_in_three_minutes_becomes_a_callback(store):
    store.online = 1
    session = _session()
    outcome = await live.request_live(session, CLINIC_ID)
    queue = live.broker.subscribe(visitor_channel(outcome.handover_id))
    store.clock = T0 + timedelta(minutes=3)
    await live.sweep(store.clock)

    assert store.handovers[outcome.handover_id].status == "timed_out"
    assert store.callbacks[-1].source == SOURCE_TIMEOUT
    assert store.callbacks[-1].session_id == session.session_id
    event, data = queue.get_nowait()
    assert event == "handover_timeout" and data["reason"] == "timed_out"


async def test_before_three_minutes_nothing_times_out(store):
    store.online = 1
    outcome = await live.request_live(_session(), CLINIC_ID)
    store.clock = T0 + timedelta(minutes=2, seconds=59)
    await live.sweep(store.clock)
    assert store.handovers[outcome.handover_id].status == "pending"
    assert not store.callbacks


async def test_accepting_within_the_window_stops_the_timeout(store):
    store.online = 1
    outcome = await live.request_live(_session(), CLINIC_ID)
    staff = make_user(SUPPORT)
    staff.client_id = CLINIC_ID
    store.clock = T0 + timedelta(minutes=1)
    await live.accept(outcome.handover_id, CLINIC_ID, staff)
    store.clock = T0 + timedelta(minutes=10)
    await live.sweep(store.clock)
    assert store.handovers[outcome.handover_id].status == "accepted"
    assert not store.callbacks


# ---------------------------------------------------------------- events and streams


def test_sse_frames_have_an_event_name_and_one_json_line():
    frame = sse_message("agent_message", {"body": "Hi\nthere"})
    assert frame.startswith("event: agent_message\ndata: ")
    assert frame.endswith("\n\n")
    assert "\n" not in frame.split("data: ", 1)[1].rstrip("\n")


async def test_a_stream_ends_when_the_handover_ends():
    broker = Broker()
    queue = broker.subscribe("visitor:x")
    broker.publish("visitor:x", "agent_message", {"body": "hi"})
    broker.publish("visitor:x", "handover_ended", {"reason": "staff_ended"})
    frames = [frame async for frame in stream_frames(queue)]
    assert frames[0].startswith("event: agent_message")
    assert frames[-1].startswith("event: handover_ended")


def test_a_slow_stream_drops_events_without_blocking():
    broker = Broker()
    broker.subscribe("staff:x")
    for _ in range(1000):
        broker.publish("staff:x", "visitor_message", {})
    assert broker.subscriber_count("staff:x") == 1


async def test_unsubscribed_streams_receive_nothing():
    broker = Broker()
    queue = broker.subscribe("c")
    broker.unsubscribe("c", queue)
    broker.publish("c", "x", {})
    assert queue.empty()


# ---------------------------------------------------------------- visitor routes and chat routing


@pytest.fixture
def visitor_app(monkeypatch, store):
    async def get_client_by_slug(slug):
        return CLINIC if slug == "clinic" else None

    monkeypatch.setattr("app.services.public_client.get_client_by_slug", get_client_by_slug)
    monkeypatch.setattr("app.api.routes.live_chat.verify_client_origin", lambda r, c: None)
    session = _session(client_id=CLINIC_ID)
    monkeypatch.setattr(
        "app.api.routes.live_chat.active_session_for", AsyncMock(return_value=session)
    )
    app = FastAPI()
    app.include_router(live_chat_routes.router, prefix="/api/v1")
    app.state.session = session
    return app


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_visitor_request_returns_a_token_once_and_the_stream_needs_it(visitor_app, store):
    store.online = 1
    session = visitor_app.state.session
    async with _client(visitor_app) as client:
        response = await client.post(
            "/api/v1/chat/live/request",
            json={
                "client": "clinic",
                "thread_id": str(session.thread_id),
                "session_id": str(session.session_id),
            },
        )
        body = response.json()
        assert body["status"] == "pending" and body["token"]
        handover_id = body["handover_id"]
        wrong = await client.get(
            "/api/v1/chat/live/stream",
            params={"client": "clinic", "handover_id": handover_id},
            headers={"X-Live-Token": "not-the-token"},
        )
        none = await client.get(
            "/api/v1/chat/live/stream",
            params={"client": "clinic", "handover_id": handover_id},
        )
    assert wrong.status_code == 404
    assert none.status_code == 404


async def test_visitor_leave_needs_the_token(visitor_app, store):
    store.online = 1
    session = visitor_app.state.session
    async with _client(visitor_app) as client:
        body = (
            await client.post(
                "/api/v1/chat/live/request",
                json={
                    "client": "clinic",
                    "thread_id": str(session.thread_id),
                    "session_id": str(session.session_id),
                },
            )
        ).json()
        refused = await client.post(
            "/api/v1/chat/live/leave",
            params={"client": "clinic", "handover_id": body["handover_id"]},
            headers={"X-Live-Token": "wrong"},
        )
        left = await client.post(
            "/api/v1/chat/live/leave",
            params={"client": "clinic", "handover_id": body["handover_id"]},
            headers={"X-Live-Token": body["token"]},
        )
    assert refused.status_code == 404
    assert left.status_code == 204
    assert next(iter(store.handovers.values())).end_reason == "visitor_left"


async def test_a_visitor_message_while_pending_is_stored_and_acknowledged(store, monkeypatch):
    store.online = 1
    session = _session()
    outcome = await live.request_live(session, CLINIC_ID)
    handover = store.handovers[outcome.handover_id]
    result = await _post_chat_turn(monkeypatch, handover, session, "is anyone there?")
    assert result["answer_source"] == "live"
    assert "shortly" in result["reply"]
    assert store.messages[-1].body == "is anyone there?" and store.messages[-1].sender == "visitor"


async def test_an_accepted_chat_is_not_answered_by_the_model(store, monkeypatch):
    store.online = 1
    session = _session()
    outcome = await live.request_live(session, CLINIC_ID)
    staff = make_user(SUPPORT)
    staff.client_id = CLINIC_ID
    await live.accept(outcome.handover_id, CLINIC_ID, staff)
    handover = store.handovers[outcome.handover_id]
    graph = MagicMock()
    graph.ainvoke = AsyncMock()
    result = await _post_chat_turn(
        monkeypatch, handover, session, "can I book friday?", graph=graph
    )
    graph.ainvoke.assert_not_awaited()
    assert result["answer_source"] == "live" and result["reply"] == ""


async def test_an_emergency_during_a_live_chat_still_gets_the_emergency_reply(store, monkeypatch):
    store.online = 1
    session = _session()
    outcome = await live.request_live(session, CLINIC_ID)
    staff = make_user(SUPPORT)
    staff.client_id = CLINIC_ID
    await live.accept(outcome.handover_id, CLINIC_ID, staff)
    handover = store.handovers[outcome.handover_id]
    result = await _post_chat_turn(monkeypatch, handover, session, "I can't breathe")
    assert result["safety"] == "urgent"
    assert store.messages[-1].body == "I can't breathe", "staff still see the message"


async def _post_chat_turn(monkeypatch, handover, session, text, graph=None):
    monkeypatch.setattr("app.api.routes.chat.get_active_client", AsyncMock(return_value=CLINIC))
    monkeypatch.setattr("app.api.routes.chat.verify_client_origin", lambda r, c: None)
    monkeypatch.setattr("app.api.routes.chat.check_chat_rate_limit", lambda r: None)
    monkeypatch.setattr(
        "app.api.routes.chat.resolve_for_message",
        AsyncMock(
            return_value=MagicMock(
                thread_id=session.thread_id,
                session_id=session.session_id,
                reset=False,
                is_test_session=False,
            )
        ),
    )
    monkeypatch.setattr(
        "app.api.routes.chat.get_open_handover_for_session", AsyncMock(return_value=handover)
    )
    app = FastAPI()
    app.include_router(chat_routes.router, prefix="/api/v1")
    app.state.agent_graph = graph or MagicMock(
        ainvoke=AsyncMock(return_value={"messages": [AIMessage(content="model")]})
    )
    async with _client(app) as client:
        response = await client.post("/api/v1/chat", json={"message": text, "client": "clinic"})
    assert response.status_code == 200
    return response.json()


# ---------------------------------------------------------------- AI draft gating


@pytest.fixture
def staff_app(monkeypatch, store):
    async def get_client(client_id):
        return make_client_record(client_id=client_id)

    monkeypatch.setattr("app.services.client_scope.get_client", get_client)
    module = "app.api.routes.admin.inbox."
    monkeypatch.setattr(module + "get_handover", store.get_handover)
    monkeypatch.setattr(module + "list_messages", store.list_messages)
    monkeypatch.setattr(module + "get_session", AsyncMock(return_value=_session()))
    monkeypatch.setattr(module + "get_session_contact", AsyncMock(return_value=None))
    monkeypatch.setattr(module + "list_queue", AsyncMock(return_value=[]))
    monkeypatch.setattr(module + "list_callbacks", AsyncMock(return_value=[]))
    monkeypatch.setattr(module + "online_staff_count", AsyncMock(return_value=1))
    app = FastAPI()
    app.include_router(inbox_routes.router, prefix="/api/v1")
    app.include_router(inbox_routes.callbacks_router, prefix="/api/v1")
    return app


async def _accepted_handover(store, agent):
    store.online = 1
    session = _session()
    outcome = await live.request_live(session, CLINIC_ID)
    await live.accept(outcome.handover_id, CLINIC_ID, agent)
    await live.visitor_message(store.handovers[outcome.handover_id], "Can I book on Friday?")
    return outcome.handover_id


async def test_ai_draft_is_off_by_default(staff_app, store, monkeypatch):
    agent = make_user(SUPPORT)
    agent.client_id = CLINIC_ID
    handover_id = await _accepted_handover(store, agent)
    with signed_in(staff_app, SUPPORT) as actor:
        actor.client_id = CLINIC_ID
        actor.id = agent.id
        async with _client(staff_app) as client:
            response = await client.post(f"/api/v1/admin/inbox/{handover_id}/draft")
    assert response.status_code == 403
    assert "turned off" in response.json()["detail"]


async def test_ai_draft_when_enabled_is_returned_to_the_agent_only(
    staff_app, store, monkeypatch, audit_rows
):
    monkeypatch.setenv("STAFF_AI_DRAFTS_ENABLED", "true")
    get_settings.cache_clear()
    model = MagicMock()
    model.invoke.return_value = AIMessage(content="Yes, Friday at 10 is free.")
    monkeypatch.setattr("app.services.staff_drafts.get_chat_model", lambda: model)
    agent = make_user(SUPPORT)
    agent.client_id = CLINIC_ID
    handover_id = await _accepted_handover(store, agent)
    with signed_in(staff_app, SUPPORT) as actor:
        actor.client_id = CLINIC_ID
        actor.id = agent.id
        async with _client(staff_app) as client:
            response = await client.post(f"/api/v1/admin/inbox/{handover_id}/draft")
    assert response.json() == {"draft": "Yes, Friday at 10 is free."}
    sent = model.invoke.call_args.args[0][1].content
    assert sent.startswith("Visitor: Can I book on Friday?"), "turns are labelled by role"
    assert not [m for m in store.messages if m.body.startswith("Yes, Friday")], "never stored"
    assert [row["action"] for row in audit_rows] == ["inbox.draft"]
    get_settings.cache_clear()


async def test_only_the_agent_who_accepted_may_request_a_draft(staff_app, store, monkeypatch):
    monkeypatch.setenv("STAFF_AI_DRAFTS_ENABLED", "true")
    get_settings.cache_clear()
    agent = make_user(SUPPORT)
    agent.client_id = CLINIC_ID
    handover_id = await _accepted_handover(store, agent)
    with signed_in(staff_app, SUPPORT) as actor:
        actor.client_id = CLINIC_ID
        async with _client(staff_app) as client:
            response = await client.post(f"/api/v1/admin/inbox/{handover_id}/draft")
    assert response.status_code == 403
    get_settings.cache_clear()


# ---------------------------------------------------------------- RBAC, scoping, and audit


async def test_support_works_the_inbox_but_cannot_save_a_faq(staff_app, store, audit_rows):
    agent = make_user(SUPPORT)
    agent.client_id = CLINIC_ID
    handover_id = await _accepted_handover(store, agent)
    with signed_in(staff_app, SUPPORT) as actor:
        actor.client_id = CLINIC_ID
        actor.id = agent.id
        async with _client(staff_app) as client:
            queue = await client.get("/api/v1/admin/inbox")
            conversation = await client.get(f"/api/v1/admin/inbox/{handover_id}")
            faq = await client.post(
                f"/api/v1/admin/inbox/{handover_id}/save-faq",
                json={"answer": "We are open 9 to 5."},
            )
    assert queue.status_code == 200
    assert conversation.status_code == 200
    assert faq.status_code == 403
    assert [row["action"] for row in audit_rows] == ["inbox.view"]


async def test_opening_a_conversation_is_audited(staff_app, store, audit_rows):
    agent = make_user(SUPPORT)
    agent.client_id = CLINIC_ID
    handover_id = await _accepted_handover(store, agent)
    with signed_in(staff_app, SUPPORT) as actor:
        actor.client_id = CLINIC_ID
        async with _client(staff_app) as client:
            await client.get(f"/api/v1/admin/inbox/{handover_id}")
    assert audit_rows[-1]["action"] == "inbox.view"
    assert audit_rows[-1]["resource_id"] == str(handover_id)


async def test_a_conversation_of_another_client_is_not_found(staff_app, store):
    agent = make_user(SUPPORT)
    agent.client_id = CLINIC_ID
    handover_id = await _accepted_handover(store, agent)
    with signed_in(staff_app, SUPPORT) as actor:
        actor.client_id = uuid4()
        async with _client(staff_app) as client:
            response = await client.get(f"/api/v1/admin/inbox/{handover_id}")
    assert response.status_code == 404


async def test_admin_saves_an_answer_as_a_draft_faq_and_it_is_audited(
    staff_app, store, monkeypatch, audit_rows
):
    agent = make_user(ADMIN)
    agent.client_id = CLINIC_ID
    handover_id = await _accepted_handover(store, agent)
    inserted = {}

    async def insert_faq(**kwargs):
        inserted.update(kwargs)
        return MagicMock(id=uuid4(), status="draft")

    monkeypatch.setattr("app.api.routes.admin.inbox.insert_faq", insert_faq)
    with signed_in(staff_app, ADMIN) as actor:
        actor.client_id = CLINIC_ID
        actor.id = agent.id
        async with _client(staff_app) as client:
            response = await client.post(
                f"/api/v1/admin/inbox/{handover_id}/save-faq",
                json={"answer": "Friday at 10 is free."},
            )
    assert response.status_code == 201
    assert inserted["status"] == "draft", "never approved automatically"
    assert inserted["question"] == "Can I book on Friday?"
    assert inserted["answer"] == "Friday at 10 is free."
    assert audit_rows[-1]["action"] == "faq.draft_from_inbox"


async def test_callbacks_are_handled_once(staff_app, store, monkeypatch, audit_rows):
    callback = CallbackRequest(
        id=uuid4(),
        client_id=CLINIC_ID,
        session_id=uuid4(),
        status="open",
        source=SOURCE_TIMEOUT,
        created_at=T0,
        handled_by=None,
        handled_at=None,
    )
    state = {"status": "open"}

    async def get_callback(callback_id, client_id):
        return callback

    async def mark(callback_id, *, client_id, user_id, now):
        if state["status"] != "open":
            return False
        state["status"] = "handled"
        return True

    monkeypatch.setattr(inbox_routes, "get_callback", get_callback)
    monkeypatch.setattr(inbox_routes, "mark_callback_handled", mark)
    with signed_in(staff_app, SUPPORT) as actor:
        actor.client_id = CLINIC_ID
        async with _client(staff_app) as client:
            first = await client.post(f"/api/v1/admin/callbacks/{callback.id}/handle")
            second = await client.post(f"/api/v1/admin/callbacks/{callback.id}/handle")
    assert first.status_code == 200
    assert second.status_code == 409
    assert audit_rows[-1]["action"] == "callback.handle"


async def test_visitor_routes_refuse_a_handover_from_another_client(
    visitor_app, store, monkeypatch
):
    store.online = 1
    session = visitor_app.state.session
    async with _client(visitor_app) as client:
        body = (
            await client.post(
                "/api/v1/chat/live/request",
                json={
                    "client": "clinic",
                    "thread_id": str(session.thread_id),
                    "session_id": str(session.session_id),
                },
            )
        ).json()
        other = make_client_record(slug="other")
        monkeypatch.setattr(
            "app.api.routes.live_chat.get_active_client", AsyncMock(return_value=other)
        )
        response = await client.post(
            "/api/v1/chat/live/leave",
            params={"client": "other", "handover_id": body["handover_id"]},
            headers={"X-Live-Token": body["token"]},
        )
    assert response.status_code == 404
