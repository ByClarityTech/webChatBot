"""Unanswered questions (knowledge gaps), once-per-session handover, and the practice phone.

- knowledge_gaps: one row per client and normalized question. ask_count grows on repeats.
  sample_query holds one original wording for the admin. It is never written to server logs.
- chat_sessions.handover_offered_at: set when a conversation was offered live chat. Once set,
  that conversation is not offered it again.
- clients.contact_phone: E.164 number behind the Call and Text buttons.
- faqs.answer may be empty for drafts only. A draft made from a gap has no answer yet; an
  approved FAQ must still have one.

Revision ID: 0013_knowledge_gaps
Revises: 0012_chat_sessions_and_consent
Create Date: 2026-11-13
"""

from alembic import op

revision = "0013_knowledge_gaps"
down_revision = "0012_chat_sessions_and_consent"
branch_labels = None
depends_on = None

UPGRADE = [
    """
    CREATE TABLE knowledge_gaps (
        id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        client_id        UUID NOT NULL REFERENCES clients (id) ON DELETE CASCADE,
        query_hash       CHAR(64) NOT NULL,
        query_normalized TEXT NOT NULL,
        sample_query     TEXT NOT NULL,
        reason           TEXT NOT NULL
                         CHECK (reason IN ('no_match', 'model_declined', 'keyword_miss')),
        top_score        DOUBLE PRECISION,
        ask_count        INTEGER NOT NULL DEFAULT 1 CHECK (ask_count >= 1),
        status           TEXT NOT NULL DEFAULT 'open'
                         CHECK (status IN ('open', 'drafted', 'dismissed')),
        drafted_faq_id   UUID REFERENCES faqs (id) ON DELETE SET NULL,
        first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (client_id, query_hash)
    )
    """,
    "CREATE INDEX knowledge_gaps_top ON knowledge_gaps (client_id, status, ask_count DESC, "
    "last_seen_at DESC)",
    "ALTER TABLE chat_sessions ADD COLUMN handover_offered_at TIMESTAMPTZ",
    "ALTER TABLE clients ADD COLUMN contact_phone TEXT "
    r"CHECK (contact_phone IS NULL OR contact_phone ~ '^\+1[0-9]{10}$')",
    "ALTER TABLE faqs ALTER COLUMN answer DROP NOT NULL",
    "ALTER TABLE faqs DROP CONSTRAINT IF EXISTS faqs_answer_check",
    "ALTER TABLE faqs ADD CONSTRAINT faqs_answer_check "
    "CHECK (status = 'draft' OR (answer IS NOT NULL AND LENGTH(TRIM(answer)) > 0))",
]

DOWNGRADE = [
    "ALTER TABLE faqs DROP CONSTRAINT IF EXISTS faqs_answer_check",
    "UPDATE faqs SET answer = 'Draft' WHERE answer IS NULL OR LENGTH(TRIM(answer)) = 0",
    "ALTER TABLE faqs ALTER COLUMN answer SET NOT NULL",
    "ALTER TABLE faqs ADD CONSTRAINT faqs_answer_check CHECK (LENGTH(TRIM(answer)) > 0)",
    "ALTER TABLE clients DROP COLUMN IF EXISTS contact_phone",
    "ALTER TABLE chat_sessions DROP COLUMN IF EXISTS handover_offered_at",
    "DROP TABLE IF EXISTS knowledge_gaps",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
