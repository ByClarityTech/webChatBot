"""Chat sessions, consent texts and records, and encrypted contact details.

- consent_texts: versioned wording. Rows are never edited; approved_at NULL = draft, never
  shown to visitors. Seeded with the PRD draft wording, not approved.
- chat_sessions: one per conversation. Ended by closing the widget ('closed') or after 30
  minutes idle ('idle'). is_test_session is set only from an authenticated admin session.
- consent_records: what the visitor was shown and accepted, as a text snapshot with SHA-256.
- session_contacts: name, phone (Fernet-encrypted) and phone_hash (HMAC, for lookups).

Revision ID: 0012_chat_sessions_and_consent
Revises: 0011_invite_tokens
Create Date: 2026-11-12
"""

from alembic import op

revision = "0012_chat_sessions_and_consent"
down_revision = "0011_invite_tokens"
branch_labels = None
depends_on = None

SHARE_DRAFT = (
    "By sharing my name and phone number, I agree that [Practice Name] may call or text me about "
    "my questions and appointment requests. Texts are not encrypted, so please don't share "
    "sensitive health details by text. See our Privacy Policy."
)
CALLBACK_DRAFT = (
    "I agree that [Practice Name] may call or text me at this number about this request. Consent "
    "is not a condition of receiving care."
)

UPGRADE = [
    """
    CREATE TABLE consent_texts (
        purpose      TEXT NOT NULL CHECK (purpose IN ('share_contact', 'callback')),
        version      INTEGER NOT NULL CHECK (version >= 1),
        body         TEXT NOT NULL,
        approved_at  TIMESTAMPTZ,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (purpose, version)
    )
    """,
    """
    CREATE TABLE chat_sessions (
        session_id            UUID PRIMARY KEY,
        thread_id             UUID NOT NULL UNIQUE REFERENCES chat_threads (thread_id)
                              ON DELETE RESTRICT,
        client_id             UUID NOT NULL REFERENCES clients (id) ON DELETE RESTRICT,
        type                  TEXT NOT NULL DEFAULT 'anonymous'
                              CHECK (type IN ('anonymous', 'known')),
        status                TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended')),
        end_reason            TEXT CHECK (end_reason IN ('closed', 'idle')),
        consent_given         BOOLEAN NOT NULL DEFAULT FALSE,
        consent_text_version  INTEGER,
        is_test_session       BOOLEAN NOT NULL DEFAULT FALSE,
        started_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_activity_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        closed_at             TIMESTAMPTZ,
        CHECK ((status = 'ended') = (closed_at IS NOT NULL)),
        CHECK ((status = 'ended') = (end_reason IS NOT NULL)),
        CHECK ((type = 'known') = consent_given)
    )
    """,
    "CREATE INDEX chat_sessions_metrics ON chat_sessions (client_id, is_test_session, started_at)",
    "CREATE INDEX chat_sessions_idle ON chat_sessions (client_id, status, last_activity_at)",
    """
    CREATE TABLE consent_records (
        id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        session_id     UUID NOT NULL REFERENCES chat_sessions (session_id) ON DELETE CASCADE,
        purpose        TEXT NOT NULL,
        text_version   INTEGER NOT NULL,
        text_snapshot  TEXT NOT NULL,
        text_sha256    CHAR(64) NOT NULL,
        accepted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (session_id, purpose, text_version),
        FOREIGN KEY (purpose, text_version) REFERENCES consent_texts (purpose, version)
    )
    """,
    """
    CREATE TABLE session_contacts (
        session_id          UUID PRIMARY KEY REFERENCES chat_sessions (session_id)
                            ON DELETE CASCADE,
        full_name           TEXT NOT NULL,
        phone_encrypted     BYTEA NOT NULL,
        phone_hash          CHAR(64) NOT NULL,
        is_current_patient  BOOLEAN NOT NULL,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX session_contacts_phone_hash ON session_contacts (phone_hash)",
    f"""
    INSERT INTO consent_texts (purpose, version, body, approved_at) VALUES
        ('share_contact', 1, '{SHARE_DRAFT}', NULL),
        ('callback', 1, '{CALLBACK_DRAFT}', NULL)
    """,
]

DOWNGRADE = [
    "DROP TABLE IF EXISTS session_contacts",
    "DROP TABLE IF EXISTS consent_records",
    "DROP TABLE IF EXISTS chat_sessions",
    "DROP TABLE IF EXISTS consent_texts",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
