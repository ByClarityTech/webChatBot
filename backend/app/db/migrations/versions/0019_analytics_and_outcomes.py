"""Analytics: conversation outcomes, links from conversations to unanswered questions, and the
indexes the reports and the outcome labeler read by.

conversation_outcomes: one row per ended, non-test conversation, written by the outcome labeler.
Reports read this table, so a dashboard load never opens a transcript. labeler_version lets a
later rule change relabel history.

knowledge_gap_events: one row each time a visitor's question went unanswered in a conversation.
It links a conversation to the gap it raised.

Revision ID: 0019_analytics_and_outcomes
Revises: 0018_website_crawler
Create Date: 2026-11-13
"""

from alembic import op

revision = "0019_analytics_and_outcomes"
down_revision = "0018_website_crawler"
branch_labels = None
depends_on = None

UPGRADE = [
    """
    CREATE TABLE knowledge_gap_events (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        session_id  UUID NOT NULL REFERENCES chat_sessions (session_id) ON DELETE CASCADE,
        gap_id      UUID NOT NULL REFERENCES knowledge_gaps (id) ON DELETE CASCADE,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX idx_knowledge_gap_events_session ON knowledge_gap_events (session_id)",
    "CREATE INDEX idx_knowledge_gap_events_gap_created"
    " ON knowledge_gap_events (gap_id, created_at)",
    """
    CREATE TABLE conversation_outcomes (
        session_id           UUID PRIMARY KEY REFERENCES chat_sessions (session_id)
                             ON DELETE CASCADE,
        client_id            UUID NOT NULL REFERENCES clients (id) ON DELETE RESTRICT,
        started_at           TIMESTAMPTZ NOT NULL,
        outcome              TEXT NOT NULL CHECK (outcome IN
                             ('booking', 'callback', 'handover', 'resolved', 'abandoned')),
        reason               TEXT NOT NULL,
        engaged              BOOLEAN NOT NULL,
        visitor_messages     INTEGER NOT NULL CHECK (visitor_messages >= 0),
        bot_replies          INTEGER NOT NULL CHECK (bot_replies >= 0),
        unanswered_questions INTEGER NOT NULL CHECK (unanswered_questions >= 0),
        duration_seconds     INTEGER NOT NULL CHECK (duration_seconds >= 0),
        labeler_version      INTEGER NOT NULL,
        labeled_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX idx_conversation_outcomes_client_started"
    " ON conversation_outcomes (client_id, started_at)",
    "CREATE INDEX idx_conversation_outcomes_client_outcome"
    " ON conversation_outcomes (client_id, outcome, started_at)",
    # The labeler's candidates: ended, non-test conversations.
    "CREATE INDEX idx_chat_sessions_ended_nontest ON chat_sessions (status, closed_at)"
    " WHERE is_test_session = FALSE",
    # Outcome lookups by conversation.
    "CREATE INDEX idx_bookings_thread_id ON bookings (thread_id)",
    "CREATE INDEX idx_callback_requests_session ON callback_requests (session_id)",
    "CREATE INDEX idx_live_handovers_session ON live_handovers (session_id)",
]

DOWNGRADE = [
    "DROP INDEX IF EXISTS idx_live_handovers_session",
    "DROP INDEX IF EXISTS idx_callback_requests_session",
    "DROP INDEX IF EXISTS idx_bookings_thread_id",
    "DROP INDEX IF EXISTS idx_chat_sessions_ended_nontest",
    "DROP TABLE IF EXISTS conversation_outcomes",
    "DROP TABLE IF EXISTS knowledge_gap_events",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
