"""Unanswered questions: gap logging, dedupe, drafting, dismissing, the fallback card, the
once-per-session live-chat offer, the [[NO_ANSWER]] marker, and degraded keyword answers.

Database functions are replaced by in-memory fakes. The SQL itself needs a live database.
"""

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from auth_helpers import make_client_record, signed_in
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.public.nodes.generate import generate_node
from app.api.routes import chat as chat_routes
from app.api.routes.admin import faq_gaps as admin_faq_gaps
from app.api.routes.admin import settings as admin_settings
from app.core.roles import UserType
from app.db.models.faq import STATUS_APPROVED, STATUS_DRAFT, FaqRecord
from app.db.models.knowledge_gap import KnowledgeGap
from app.services import fallback as fb
from app.services import knowledge_gaps as kg
from app.services.keyword_answer import best_match
from app.services.practice_contact import clean_practice_phone

SA, ADMIN, SUPPORT = UserType.super_admin, UserType.admin, UserType.support
T0 = datetime(2030, 7, 1, 10, 0, tzinfo=UTC)
CLIENT = make_client_record("clinic")
CLIENT_ID = CLIENT.id
# The conftest stub replaces claim_live_chat per test; the real one is kept for these tests.
REAL_CLAIM = fb.claim_live_chat


# ---------------------------------------------------------------- normalization and dedupe


def test_repeat_wordings_share_one_key():
    assert kg.query_hash(kg.normalize_query("Hours?")) == kg.query_hash(kg.normalize_query("hours"))
    assert kg.normalize_query("  What   are your HOURS!! ") == "what are your hours"
    assert kg.query_hash(kg.normalize_query("do you take insurance")) != kg.query_hash(
        kg.normalize_query("do you take cards")
    )


def test_short_messages_are_not_tracked_as_questions():
    assert not kg.is_loggable(kg.normalize_query("hi there"))
    assert kg.is_loggable(kg.normalize_query("is parking free"))


class GapStore:
    """In-memory knowledge_gaps table with the upsert rule from the SQL."""

    def __init__(self):
        self.rows: dict = {}

    async def upsert(
        self, *, client_id, query_hash, query_normalized, sample_query, reason, top_score
    ):
        key = (client_id, query_hash)
        if key in self.rows:
            row = self.rows[key]
            object.__setattr__(row, "ask_count", row.ask_count + 1)
            object.__setattr__(row, "last_seen_at", T0)
            if top_score is not None:
                object.__setattr__(row, "top_score", top_score)
            return row.id, row.ask_count
        gap = KnowledgeGap(
            id=uuid4(),
            client_id=client_id,
            query_hash=query_hash,
            query_normalized=query_normalized,
            sample_query=sample_query,
            reason=reason,
            top_score=top_score,
            ask_count=1,
            status="open",
            drafted_faq_id=None,
            first_seen_at=T0,
            last_seen_at=T0,
        )
        self.rows[key] = gap
        return gap.id, 1


@pytest.fixture
def store(monkeypatch):
    s = GapStore()
    monkeypatch.setattr("app.services.knowledge_gaps.upsert_gap", s.upsert)
    return s


async def test_recording_the_same_question_raises_the_count_not_the_rows(store):
    await kg.record_gap(CLIENT_ID, "Do you take Delta insurance?", reason="no_match", top_score=0.2)
    count = await kg.record_gap(
        CLIENT_ID, "do you take delta insurance", reason="no_match", top_score=0.3
    )
    assert count == 2
    assert len(store.rows) == 1
    only = next(iter(store.rows.values()))
    assert only.top_score == 0.3, "the latest score is kept for calibration"
    assert only.sample_query == "Do you take Delta insurance?", "the first wording is the sample"


async def test_other_clients_are_not_merged(store):
    await kg.record_gap(CLIENT_ID, "is parking free here", reason="no_match", top_score=None)
    await kg.record_gap(uuid4(), "is parking free here", reason="no_match", top_score=None)
    assert len(store.rows) == 2


async def test_a_gap_log_line_never_contains_the_question(store, caplog):
    secret = "my son has a rash after the penicillin injection"
    with caplog.at_level(logging.INFO):
        await kg.record_gap(CLIENT_ID, secret, reason="model_declined", top_score=None)
    assert "Knowledge gap recorded" in caplog.text
    assert "penicillin" not in caplog.text


async def test_unknown_reason_is_refused(store):
    with pytest.raises(ValueError):
        await kg.record_gap(CLIENT_ID, "is parking free here", reason="guess", top_score=None)


async def test_long_questions_are_cut_to_the_column_limit(store):
    await kg.record_gap(CLIENT_ID, "word " * 80, reason="no_match", top_score=None)
    assert len(next(iter(store.rows.values())).sample_query) <= 200


# ---------------------------------------------------------------- marker and card


@pytest.mark.parametrize(
    ("text", "clean", "found"),
    [
        ("[[NO_ANSWER]] I don't have that.", "I don't have that.", True),
        ("Sorry. [[NO_ANSWER]] Please call us.", "Sorry.  Please call us.", True),
        ("We are open 9 to 5.", "We are open 9 to 5.", False),
    ],
)
def test_marker_is_removed_wherever_it_appears(text, clean, found):
    cleaned, declined = fb.strip_marker(text)
    assert declined is found
    assert "[[NO_ANSWER]]" not in cleaned
    assert " ".join(cleaned.split()) == " ".join(clean.split())


def test_generate_strips_the_marker_and_reports_it(monkeypatch):
    model = MagicMock()
    model.invoke.return_value = AIMessage(content="[[NO_ANSWER]] I don't have that. Call us.")
    monkeypatch.setattr("app.agents.public.nodes.generate.get_chat_model", lambda: model)
    state = {
        "messages": [HumanMessage(content="is parking free here")],
        "retrieved_docs": [{"text": "We are open 9 to 5.", "metadata": {}}],
    }
    result = generate_node(state)
    assert result["answer_declined"] is True
    assert "[[NO_ANSWER]]" not in result["messages"][0].content


def test_card_offers_only_what_the_practice_can_use():
    bare = fb.build_card("en", contact_phone=None, callback=False, live_chat=False)
    assert bare["call"] is None and bare["text"] is None
    assert bare["callback"] is False and bare["live_chat"] is False

    full = fb.build_card("en", contact_phone="+13125550187", callback=True, live_chat=True)
    assert full["call"] == "tel:+13125550187"
    assert full["text"] == "sms:+13125550187"
    assert full["labels"]["callback"] == "Request a callback"


def test_card_labels_follow_the_visitors_language():
    spanish = fb.build_card("es", contact_phone=None, callback=False, live_chat=False)
    assert spanish["labels"]["call"] == "Llamar a la oficina"
    assert fb.copy_for("fr")["intro"] == fb.copy_for("en")["intro"]


# ---------------------------------------------------------------- once-per-session live chat


@pytest.fixture
def handover(monkeypatch):
    """A stateful stand-in for the atomic claim on chat_sessions.handover_offered_at."""
    offered: set = set()

    async def claim(session_id, now):
        if session_id in offered:
            return False
        offered.add(session_id)
        return True

    monkeypatch.setattr("app.services.fallback.claim_live_chat_offer", claim)
    return offered


async def test_live_chat_is_never_offered_while_disabled(handover, monkeypatch):
    monkeypatch.setenv("LIVE_CHAT_ENABLED", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    session = uuid4()
    assert await REAL_CLAIM(session, T0) is False
    assert not handover
    get_settings.cache_clear()


async def test_live_chat_is_offered_once_per_conversation(handover, monkeypatch):
    monkeypatch.setenv("LIVE_CHAT_ENABLED", "true")
    from app.core.config import get_settings

    get_settings.cache_clear()
    session = uuid4()
    assert await REAL_CLAIM(session, T0) is True
    assert await REAL_CLAIM(session, T0 + timedelta(minutes=1)) is False
    assert await REAL_CLAIM(uuid4(), T0) is True, "a new conversation may be offered"
    get_settings.cache_clear()


# ---------------------------------------------------------------- keyword answers


def _faq(question, answer, status=STATUS_APPROVED):
    return FaqRecord(
        id=uuid4(),
        client_id=CLIENT_ID,
        question=question,
        answer=answer,
        status=status,
        default_order=None,
        source_id=None,
        source_filename=None,
        created_by=None,
        created_at=T0,
        updated_at=T0,
    )


def test_keyword_answer_needs_shared_words_and_an_approved_faq():
    faqs = [
        _faq("What are your opening hours?", "We are open 9 to 5."),
        _faq("Do you offer parking?", "Yes, free parking.", status=STATUS_DRAFT),
    ]
    match = best_match(faqs, "what are the opening hours")
    assert match is not None and match.answer == "We are open 9 to 5."
    assert best_match(faqs, "is there parking") is None, "drafts never answer"
    assert best_match(faqs, "tell me about lasers") is None


def test_a_single_word_question_can_match_on_one_word():
    faqs = [_faq("What are your hours?", "We are open 9 to 5.")]
    assert best_match(faqs, "hours") is not None


# ---------------------------------------------------------------- drafting and dismissing (FR-29)


@pytest.fixture
def admin_gap_db(monkeypatch, store):
    """Gap rows plus the FAQ inserts and the status changes, all in memory."""
    state = {"faqs": [], "drafted": {}, "dismissed": set()}

    async def get_gap(gap_id, client_id):
        for gap in store.rows.values():
            if gap.id == gap_id and gap.client_id == client_id:
                return gap
        return None

    async def list_open(client_id, limit):
        return [g for g in store.rows.values() if g.client_id == client_id and g.status == "open"][
            :limit
        ]

    async def existing(client_id):
        return {f.question.lower() for f in state["faqs"] if f.client_id == client_id}

    async def insert(*, client_id, question, answer, created_by, **_):
        faq = _faq(question, answer, status=STATUS_DRAFT)
        faq.client_id = client_id
        faq.created_by = created_by
        state["faqs"].append(faq)
        return faq

    async def mark_drafted(gap_id, faq_id):
        for gap in store.rows.values():
            if gap.id == gap_id and gap.status == "open":
                object.__setattr__(gap, "status", "drafted")
                return True
        return False

    async def mark_dismissed(gap_id, client_id):
        for gap in store.rows.values():
            if gap.id == gap_id and gap.status == "open":
                object.__setattr__(gap, "status", "dismissed")
                return True
        return False

    monkeypatch.setattr("app.services.knowledge_gaps.get_gap", get_gap)
    monkeypatch.setattr("app.services.knowledge_gaps.list_open_gaps", list_open)
    monkeypatch.setattr("app.services.knowledge_gaps.existing_questions", existing)
    monkeypatch.setattr("app.services.knowledge_gaps.insert_faq", insert)
    monkeypatch.setattr("app.services.knowledge_gaps.mark_gap_drafted", mark_drafted)
    monkeypatch.setattr("app.services.knowledge_gaps.mark_gap_dismissed", mark_dismissed)
    return state


async def _gap(store, question="is parking free here", count=3):
    for _ in range(count):
        await kg.record_gap(CLIENT_ID, question, reason="no_match", top_score=None)
    return next(iter(store.rows.values()))


async def test_drafting_makes_an_empty_draft_and_closes_the_gap(store, admin_gap_db):
    gap = await _gap(store)
    actor = SimpleNamespace(id=uuid4())
    faq = await kg.draft_from_gap(CLIENT_ID, gap.id, actor)
    assert faq.status == STATUS_DRAFT
    assert faq.answer == "", "no answer is written for the person"
    assert faq.question == "is parking free here"
    assert next(iter(store.rows.values())).status == "drafted"


async def test_drafting_a_question_already_in_the_faqs_is_refused(store, admin_gap_db):
    admin_gap_db["faqs"].append(_faq("is parking free here", "Yes.", status=STATUS_DRAFT))
    gap = await _gap(store)
    with pytest.raises(Exception) as raised:
        await kg.draft_from_gap(CLIENT_ID, gap.id, SimpleNamespace(id=uuid4()))
    assert raised.value.status_code == 409


async def test_only_open_gaps_can_be_drafted_or_dismissed(store, admin_gap_db):
    gap = await _gap(store)
    await kg.dismiss_gap(CLIENT_ID, gap.id)
    with pytest.raises(Exception) as raised:
        await kg.draft_from_gap(CLIENT_ID, gap.id, SimpleNamespace(id=uuid4()))
    assert raised.value.status_code == 409


async def test_a_gap_of_another_client_is_not_found(store, admin_gap_db):
    gap = await _gap(store)
    with pytest.raises(Exception) as raised:
        await kg.get_client_gap(uuid4(), gap.id)
    assert raised.value.status_code == 404


# ---------------------------------------------------------------- admin routes and RBAC


@pytest.fixture
def gap_app(monkeypatch, store, admin_gap_db):
    monkeypatch.setattr("app.services.client_scope.get_client", AsyncMock(return_value=CLIENT))
    monkeypatch.setattr(admin_faq_gaps, "resolve_client_scope", AsyncMock(return_value=CLIENT))
    test_app = FastAPI()
    test_app.include_router(admin_faq_gaps.router, prefix="/api/v1")
    test_app.include_router(admin_settings.router, prefix="/api/v1")
    return test_app


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_support_cannot_see_or_act_on_gaps(gap_app, store, admin_gap_db):
    gap = await _gap(store)
    with signed_in(gap_app, SUPPORT):
        async with _client(gap_app) as client:
            listing = await client.get("/api/v1/admin/faq-gaps")
            draft = await client.post(f"/api/v1/admin/faq-gaps/{gap.id}/draft")
    assert listing.status_code == 403
    assert draft.status_code == 403


async def test_admin_lists_the_most_asked_open_gaps(gap_app, store, admin_gap_db):
    await _gap(store, "is parking free here", count=5)
    await _gap(store, "do you have wifi", count=1)
    with signed_in(gap_app, ADMIN):
        async with _client(gap_app) as client:
            response = await client.get("/api/v1/admin/faq-gaps")
    gaps = response.json()["gaps"]
    assert [g["question"] for g in gaps][:1] == ["is parking free here"]
    assert gaps[0]["ask_count"] == 5


async def test_admin_drafts_a_gap_and_the_action_is_audited(
    gap_app, store, admin_gap_db, audit_rows
):
    gap = await _gap(store)
    with signed_in(gap_app, ADMIN):
        async with _client(gap_app) as client:
            response = await client.post(f"/api/v1/admin/faq-gaps/{gap.id}/draft")
    assert response.status_code == 201
    assert response.json()["answer"] == ""
    assert [row["action"] for row in audit_rows] == ["faq_gap.draft"]
    assert audit_rows[0]["resource_id"] == str(gap.id), (
        "the audit row holds the id, not the question"
    )


async def test_dismissing_a_gap_is_audited(gap_app, store, admin_gap_db, audit_rows):
    gap = await _gap(store)
    with signed_in(gap_app, ADMIN):
        async with _client(gap_app) as client:
            response = await client.post(f"/api/v1/admin/faq-gaps/{gap.id}/dismiss")
    assert response.status_code == 204
    assert [row["action"] for row in audit_rows] == ["faq_gap.dismiss"]


async def test_practice_phone_is_normalized_and_cleared(gap_app, monkeypatch):
    saved = {}

    async def set_phone(client_id, phone):
        saved["phone"] = phone
        return SimpleNamespace(contact_phone=phone)

    monkeypatch.setattr("app.api.routes.admin.settings.set_client_contact_phone", set_phone)
    with signed_in(gap_app, ADMIN):
        async with _client(gap_app) as client:
            response = await client.put(
                "/api/v1/admin/settings/contact", json={"phone": "(312) 555-0187"}
            )
            cleared = await client.put("/api/v1/admin/settings/contact", json={"phone": ""})
    assert response.json() == {"phone": "+13125550187"}
    assert saved["phone"] is None
    assert cleared.json() == {"phone": None}


async def test_invalid_practice_phone_is_400(gap_app, monkeypatch):
    monkeypatch.setattr("app.api.routes.admin.settings.set_client_contact_phone", AsyncMock())
    with signed_in(gap_app, ADMIN):
        async with _client(gap_app) as client:
            response = await client.put(
                "/api/v1/admin/settings/contact", json={"phone": "555-0187"}
            )
    assert response.status_code == 400


async def test_support_cannot_change_the_practice_phone(gap_app):
    with signed_in(gap_app, SUPPORT):
        async with _client(gap_app) as client:
            response = await client.put(
                "/api/v1/admin/settings/contact", json={"phone": "3125550187"}
            )
    assert response.status_code == 403


def test_clean_practice_phone_accepts_only_us_numbers():
    assert clean_practice_phone("312.555.0187") == "+13125550187"
    assert clean_practice_phone("  ") is None
    with pytest.raises(ValueError):
        clean_practice_phone("+44 20 7946 0958")


async def test_an_approved_faq_needs_an_answer(monkeypatch):
    from app.services import faqs as faq_service

    draft = _faq("is parking free here", "", status=STATUS_DRAFT)
    monkeypatch.setattr(faq_service, "get_client_faq", AsyncMock(return_value=draft))
    with pytest.raises(Exception) as raised:
        await faq_service.change_faq(
            CLIENT, draft.id, question=None, answer=None, status_=STATUS_APPROVED
        )
    assert raised.value.status_code == 400


# ---------------------------------------------------------------- chat route: fallback behaviour


@pytest.fixture
def chat_app(monkeypatch):
    client = make_client_record(slug="clinic")
    client.contact_phone = "+13125550187"

    async def get_client_by_slug(slug):
        return client if slug == "clinic" else None

    monkeypatch.setattr("app.services.public_client.get_client_by_slug", get_client_by_slug)
    monkeypatch.setattr("app.api.routes.chat.verify_client_origin", lambda request, c: None)
    from auth_helpers import fake_session_resolver

    monkeypatch.setattr("app.api.routes.chat.resolve_for_message", fake_session_resolver())
    app = FastAPI()
    app.state.agent_graph = MagicMock(ainvoke=AsyncMock())
    app.include_router(chat_routes.router, prefix="/api/v1")
    return app


async def _chat(app, message="is parking free here", **extra):
    async with _client(app) as client:
        return await client.post(
            "/api/v1/chat", json={"message": message, "client": "clinic", **extra}
        )


async def test_empty_retrieval_shows_the_card_and_logs_no_match(chat_app, monkeypatch):
    record = AsyncMock(return_value=1)
    monkeypatch.setattr("app.api.routes.chat.record_gap", record)
    chat_app.state.agent_graph.ainvoke = AsyncMock(
        return_value={
            "messages": [AIMessage(content=fb.copy_for("en")["intro"])],
            "retrieved_docs": [],
            "answer_declined": True,
        }
    )
    response = await _chat(chat_app)
    body = response.json()
    assert body["answer_source"] == "fallback"
    assert body["fallback"]["call"] == "tel:+13125550187"
    assert record.await_args.kwargs["reason"] == "no_match"
    assert record.await_args.kwargs["top_score"] is None


async def test_declined_answer_logs_model_declined_with_the_top_score(chat_app, monkeypatch):
    record = AsyncMock(return_value=1)
    monkeypatch.setattr("app.api.routes.chat.record_gap", record)
    chat_app.state.agent_graph.ainvoke = AsyncMock(
        return_value={
            "messages": [AIMessage(content="I don't have that. Call us.")],
            "retrieved_docs": [
                {"text": "Hours", "filename": "h.md", "score": 0.31, "metadata": {}}
            ],
            "answer_declined": True,
        }
    )
    response = await _chat(chat_app)
    assert response.json()["reply"] == "I don't have that. Call us."
    assert record.await_args.kwargs["reason"] == "model_declined"
    assert record.await_args.kwargs["top_score"] == pytest.approx(0.31)


async def test_a_test_chat_does_not_log_gaps(chat_app, monkeypatch):
    record = AsyncMock(return_value=1)
    monkeypatch.setattr("app.api.routes.chat.record_gap", record)
    monkeypatch.setattr(
        "app.api.routes.chat.optional_admin_user", AsyncMock(return_value=MagicMock(id=uuid4()))
    )
    chat_app.state.agent_graph.ainvoke = AsyncMock(
        return_value={"messages": [], "retrieved_docs": [], "answer_declined": True}
    )
    await _chat(chat_app, test_session=True)
    record.assert_not_awaited()


async def test_missing_key_answers_from_an_approved_faq(chat_app, monkeypatch):
    record = AsyncMock(return_value=1)
    monkeypatch.setattr("app.api.routes.chat.record_gap", record)
    monkeypatch.setattr(
        "app.api.routes.chat.answer_from_faqs",
        AsyncMock(return_value=_faq("Is parking free?", "Yes, parking is free.")),
    )
    chat_app.state.agent_graph.ainvoke = AsyncMock(side_effect=ValueError("OPENAI_API_KEY"))
    response = await _chat(chat_app, "is parking free")
    body = response.json()
    assert body["answer_source"] == "faq"
    assert body["reply"] == "Yes, parking is free."
    record.assert_not_awaited()


async def test_provider_error_with_no_faq_match_shows_the_card_as_keyword_miss(
    chat_app, monkeypatch
):
    record = AsyncMock(return_value=1)
    monkeypatch.setattr("app.api.routes.chat.record_gap", record)
    chat_app.state.agent_graph.ainvoke = AsyncMock(side_effect=RuntimeError("503 from provider"))
    response = await _chat(chat_app, "do you have wifi here")
    body = response.json()
    assert body["answer_source"] == "fallback"
    assert record.await_args.kwargs["reason"] == "keyword_miss"


async def test_degraded_mode_still_refuses_a_diagnosis_question(chat_app, monkeypatch):
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr("app.api.routes.chat.answer_from_faqs", lookup)
    chat_app.state.agent_graph.ainvoke = AsyncMock(side_effect=RuntimeError("provider down"))
    response = await _chat(chat_app, "do I have a cavity")
    body = response.json()
    assert body["answer_source"] == "safety"
    assert body["safety"] == "diagnosis"
    lookup.assert_not_awaited()


async def test_degraded_mode_still_handles_an_emergency(chat_app, monkeypatch):
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr("app.api.routes.chat.answer_from_faqs", lookup)
    chat_app.state.agent_graph.ainvoke = AsyncMock(side_effect=RuntimeError("provider down"))
    response = await _chat(chat_app, "I can't breathe and my face is swollen")
    body = response.json()
    assert body["answer_source"] == "safety"
    assert body["safety"] == "urgent"
    lookup.assert_not_awaited()


async def test_second_fallback_in_one_conversation_has_no_live_chat(
    chat_app, monkeypatch, handover
):
    monkeypatch.setattr("app.services.fallback.claim_live_chat", REAL_CLAIM)
    monkeypatch.setenv("LIVE_CHAT_ENABLED", "true")
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr("app.api.routes.chat.record_gap", AsyncMock(return_value=1))
    chat_app.state.agent_graph.ainvoke = AsyncMock(
        return_value={"messages": [], "retrieved_docs": [], "answer_declined": True}
    )
    first = (await _chat(chat_app)).json()
    thread = first["thread_id"]
    second = (await _chat(chat_app, thread_id=thread)).json()
    assert first["fallback"]["live_chat"] is True
    assert second["fallback"]["live_chat"] is False
    get_settings.cache_clear()


async def test_reply_never_shows_the_marker(chat_app, monkeypatch):
    monkeypatch.setattr("app.api.routes.chat.record_gap", AsyncMock(return_value=1))
    chat_app.state.agent_graph.ainvoke = AsyncMock(
        return_value={
            "messages": [AIMessage(content="[[NO_ANSWER]] Sorry, call the office.")],
            "retrieved_docs": [{"text": "x", "filename": "f", "score": 0.1, "metadata": {}}],
            "answer_declined": True,
        }
    )
    response = await _chat(chat_app)
    assert "[[NO_ANSWER]]" not in response.text
