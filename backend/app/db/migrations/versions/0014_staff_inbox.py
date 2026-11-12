"""Staff inbox: live handovers to staff, their messages, staff presence, and callback requests.

- live_handovers: one open handover per conversation. pending -> accepted | timed_out | ended.
  Accepting is a conditional UPDATE, so only one staff member can win a request. The visitor
  stream is authenticated with a random token whose SHA-256 is stored here.
- live_messages: the messages of a handover, from the visitor or from staff.
- staff_presence: one row per staff browser tab, refreshed by heartbeats. Online means a beat
  in the last 60 seconds.
- callback_requests: the Callbacks queue (FR-37). Created on a timeout, when no one is online,
  or when a visitor asks for a callback in chat.

Revision ID: 0014_staff_inbox
Revises: 0013_knowledge_gaps
Create Date: 2026-11-13
"""

from alembic import op

revision = "0014_staff_inbox"
down_revision = "0013_knowledge_gaps"
branch_labels = None
depends_on = None

UPGRADE = [
    """
    CREATE TABLE live_handovers (
        id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        session_id         UUID NOT NULL REFERENCES chat_sessions (session_id) ON DELETE CASCADE,
        client_id          UUID NOT NULL REFERENCES clients (id) ON DELETE CASCADE,
        status             TEXT NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending', 'accepted', 'timed_out', 'ended')),
        requested_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at         TIMESTAMPTZ NOT NULL,
        accepted_by        UUID REFERENCES users (id) ON DELETE SET NULL,
        accepted_at        TIMESTAMPTZ,
        ended_at           TIMESTAMPTZ,
        end_reason         TEXT CHECK (end_reason IN (
                           'staff_ended', 'visitor_left', 'session_ended', 'timed_out')),
        stream_token_hash  CHAR(64) NOT NULL,
        CHECK ((status IN ('timed_out', 'ended')) = (ended_at IS NOT NULL)),
        CHECK ((status = 'accepted') = (accepted_by IS NOT NULL))
    )
    """,
    # At most one open handover per conversation.
    "CREATE UNIQUE INDEX live_handovers_one_open ON live_handovers (session_id) "
    "WHERE status IN ('pending', 'accepted')",
    "CREATE INDEX live_handovers_queue ON live_handovers (client_id, status, expires_at)",
    """
    CREATE TABLE live_messages (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        handover_id     UUID NOT NULL REFERENCES live_handovers (id) ON DELETE CASCADE,
        client_id       UUID NOT NULL REFERENCES clients (id) ON DELETE CASCADE,
        sender          TEXT NOT NULL CHECK (sender IN ('visitor', 'staff')),
        author_user_id  UUID REFERENCES users (id) ON DELETE SET NULL,
        body            TEXT NOT NULL CHECK (LENGTH(TRIM(body)) > 0 AND LENGTH(body) <= 2000),
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX live_messages_thread ON live_messages (handover_id, created_at)",
    """
    CREATE TABLE staff_presence (
        user_id       UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        tab_id        TEXT NOT NULL CHECK (LENGTH(tab_id) BETWEEN 8 AND 64),
        client_id     UUID NOT NULL REFERENCES clients (id) ON DELETE CASCADE,
        last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, tab_id)
    )
    """,
    "CREATE INDEX staff_presence_client ON staff_presence (client_id, last_seen_at)",
    """
    CREATE TABLE callback_requests (
        id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        client_id    UUID NOT NULL REFERENCES clients (id) ON DELETE CASCADE,
        session_id   UUID REFERENCES chat_sessions (session_id) ON DELETE SET NULL,
        status       TEXT NOT NULL DEFAULT 'open'
                     CHECK (status IN ('open', 'handled', 'dismissed')),
        source       TEXT NOT NULL CHECK (LENGTH(TRIM(source)) > 0),
        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        handled_by   UUID REFERENCES users (id) ON DELETE SET NULL,
        handled_at   TIMESTAMPTZ
    )
    """,
    "CREATE INDEX callback_requests_queue ON callback_requests (client_id, status, created_at)",
    # One open callback per conversation.
    "CREATE UNIQUE INDEX callback_requests_one_open ON callback_requests (session_id) "
    "WHERE status = 'open' AND session_id IS NOT NULL",
]

DOWNGRADE = [
    "DROP TABLE IF EXISTS callback_requests",
    "DROP TABLE IF EXISTS staff_presence",
    "DROP TABLE IF EXISTS live_messages",
    "DROP TABLE IF EXISTS live_handovers",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
