"""faqs: each client's question and answer pairs, and its default questions.

A FAQ starts as a draft (typed by an admin or generated from the client's documents) and only
reaches the chatbot once approved. Up to 4 approved FAQs are the client's default questions,
shown as buttons in the widget; default_order (1-4) is their position, NULL for the rest.
The database enforces the limit: the order must be 1-4, unique per client, and only on
approved FAQs.

source_id points at the knowledge source a generated FAQ came from. Deleting that document
clears source_id but keeps source_filename, so the admin page can show "source removed".

Revision ID: 0006_faqs
Revises: 0005_client_knowledge_sources
Create Date: 2026-11-04
"""

from alembic import op

revision = "0006_faqs"
down_revision = "0005_client_knowledge_sources"
branch_labels = None
depends_on = None

MAX_DEFAULT_QUESTIONS = 4

UPGRADE = [
    f"""
    CREATE TABLE faqs (
        id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        client_id        UUID NOT NULL REFERENCES clients (id) ON DELETE RESTRICT,
        question         VARCHAR(200) NOT NULL CHECK (LENGTH(TRIM(question)) > 0),
        answer           VARCHAR(2000) NOT NULL CHECK (LENGTH(TRIM(answer)) > 0),
        status           VARCHAR(20) NOT NULL DEFAULT 'draft'
                         CHECK (status IN ('draft', 'approved')),
        default_order    SMALLINT CHECK (default_order BETWEEN 1 AND {MAX_DEFAULT_QUESTIONS}),
        source_id        UUID REFERENCES knowledge_sources (id) ON DELETE SET NULL,
        source_filename  VARCHAR(255),
        created_by       UUID REFERENCES users (id) ON DELETE SET NULL,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT ck_faqs_default_is_approved
            CHECK (default_order IS NULL OR status = 'approved'),
        -- Deferred so two defaults can swap places inside one transaction.
        CONSTRAINT uq_faqs_client_default_order UNIQUE (client_id, default_order)
            DEFERRABLE INITIALLY DEFERRED
    )
    """,
    # One FAQ per question per client, ignoring case; generation relies on it to skip repeats.
    "CREATE UNIQUE INDEX uq_faqs_client_question ON faqs (client_id, LOWER(question))",
    "CREATE INDEX idx_faqs_client_created ON faqs (client_id, created_at DESC)",
    "CREATE INDEX idx_faqs_source_id ON faqs (source_id)",
]

DOWNGRADE = [
    "DROP TABLE IF EXISTS faqs",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
