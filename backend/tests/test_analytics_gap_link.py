"""record_gap links an unanswered question to its conversation when the session is known, and
the migration for analytics follows the crawler's. No database."""

import importlib.util
from pathlib import Path
from uuid import uuid4

from app.services import knowledge_gaps as kg

CLIENT = uuid4()
SESSION = uuid4()
GAP = uuid4()


def _store(monkeypatch):
    events: list[tuple] = []

    async def upsert(**_fields):
        return GAP, 3

    async def insert_event(session_id, gap_id):
        events.append((session_id, gap_id))

    monkeypatch.setattr("app.services.knowledge_gaps.upsert_gap", upsert)
    monkeypatch.setattr("app.services.knowledge_gaps.insert_gap_event", insert_event)
    return events


async def test_an_unanswered_question_is_linked_to_its_conversation(monkeypatch):
    events = _store(monkeypatch)

    count = await kg.record_gap(
        CLIENT, "Do you take Delta insurance?", reason="no_match", top_score=0.1,
        session_id=SESSION,
    )  # fmt: skip

    assert count == 3
    assert events == [(SESSION, GAP)]


async def test_without_a_session_nothing_is_linked(monkeypatch):
    events = _store(monkeypatch)

    await kg.record_gap(CLIENT, "Do you take Delta insurance?", reason="no_match", top_score=0.1)

    assert events == []


async def test_a_short_message_is_not_a_question_and_links_nothing(monkeypatch):
    events = _store(monkeypatch)

    result = await kg.record_gap(
        CLIENT, "thanks", reason="no_match", top_score=None, session_id=SESSION
    )

    assert result is None
    assert events == []


def test_migration_0019_follows_0018_and_creates_the_outcome_table():
    path = Path(__file__).resolve().parents[1] / (
        "app/db/migrations/versions/0019_analytics_and_outcomes.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0019", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0019_analytics_and_outcomes"
    assert module.down_revision == "0018_website_crawler"
    text = "\n".join(module.UPGRADE)
    assert "CREATE TABLE conversation_outcomes" in text
    assert "CREATE TABLE knowledge_gap_events" in text
    assert "idx_bookings_thread_id" in text
    assert "idx_callback_requests_session" in text
    assert "idx_live_handovers_session" in text
