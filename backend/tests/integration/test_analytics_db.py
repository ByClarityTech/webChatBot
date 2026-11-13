"""Analytics against a real database (migration 0019). Skipped unless TEST_DATABASE_URL is set.

Checks what only PostgreSQL can show: the labeler finds each ended conversation once, relabels it
only when the labeler version changes, never sees test conversations, and a phone lookup stays
inside one clinic.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import analytics as analytics_db
from app.services.analytics.outcomes import LABELER_VERSION

pytestmark = pytest.mark.integration

PHONE_HASH = "c" * 64


def _clinic(conn, slug: str):
    return conn.execute(
        "INSERT INTO clients (name, slug) VALUES (%s, %s) RETURNING id", (slug, slug)
    ).fetchone()[0]


def _ended_session(conn, client_id, *, is_test: bool = False, ended_minutes_ago: int = 10):
    thread = conn.execute(
        "INSERT INTO chat_threads (thread_id, client_id) VALUES (gen_random_uuid(), %s)"
        " RETURNING thread_id",
        (client_id,),
    ).fetchone()[0]
    closed = datetime.now(UTC) - timedelta(minutes=ended_minutes_ago)
    return conn.execute(
        """
        INSERT INTO chat_sessions (session_id, thread_id, client_id, status, end_reason,
                                   is_test_session, started_at, last_activity_at, closed_at)
        VALUES (gen_random_uuid(), %s, %s, 'ended', 'idle', %s, %s, %s, %s)
        RETURNING session_id, thread_id
        """,
        (thread, client_id, is_test, closed - timedelta(minutes=3), closed, closed),
    ).fetchone()


async def test_a_conversation_is_a_candidate_once_per_labeler_version(db):
    clinic = _clinic(db, "clinic-a")
    session_id, _ = _ended_session(db, clinic)

    candidates = await analytics_db.label_candidates(LABELER_VERSION, 50)
    assert [c.session_id for c in candidates] == [session_id]

    await analytics_db.upsert_outcome(
        {
            "session_id": session_id, "client_id": clinic, "started_at": datetime.now(UTC),
            "outcome": "resolved", "reason": "x", "engaged": True, "visitor_messages": 1,
            "bot_replies": 1, "unanswered_questions": 0, "duration_seconds": 60,
            "labeler_version": LABELER_VERSION,
        }
    )  # fmt: skip
    assert await analytics_db.label_candidates(LABELER_VERSION, 50) == []
    assert len(await analytics_db.label_candidates(LABELER_VERSION + 1, 50)) == 1


async def test_test_conversations_and_recent_endings_are_never_candidates(db):
    clinic = _clinic(db, "clinic-b")
    _ended_session(db, clinic, is_test=True)
    _ended_session(db, clinic, ended_minutes_ago=1)

    assert await analytics_db.label_candidates(LABELER_VERSION, 50) == []


async def test_a_phone_lookup_stays_inside_one_clinic(db):
    clinic_a = _clinic(db, "clinic-x")
    clinic_b = _clinic(db, "clinic-y")
    for clinic in (clinic_a, clinic_b):
        session_id, _ = _ended_session(db, clinic)
        db.execute(
            """
            INSERT INTO session_contacts (session_id, full_name, phone_encrypted, phone_hash,
                                          is_current_patient)
            VALUES (%s, 'Same Person', '\\x00', %s, false)
            """,
            (session_id, PHONE_HASH),
        )
    now = datetime.now(UTC)
    rows, total = await analytics_db.list_conversations(
        clinic_a, now - timedelta(days=1), now + timedelta(days=1),
        outcome=None, phone_hash=PHONE_HASH, limit=50, offset=0,
    )  # fmt: skip
    assert total == 1
    assert len(rows) == 1
