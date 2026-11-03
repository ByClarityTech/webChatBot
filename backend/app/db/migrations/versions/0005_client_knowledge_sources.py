"""Per-client knowledge sources and chat threads.

knowledge_sources: one row per uploaded file (later also crawled website pages, kind 'page'),
owned by one client. Postgres is the list of record; Qdrant holds the chunks, each tagged with
the source's client_id and id. The same file may be uploaded by two clients, but only once per
client (content_hash is unique per client).

chat_threads: binds a conversation thread to the client whose widget started it, so a thread
id cannot be continued on another client's widget.

Existing Qdrant chunks have no client; scripts/assign_documents_to_client.py gives them one.

Revision ID: 0005_client_knowledge_sources
Revises: 0004_must_change_password
Create Date: 2026-11-04
"""

from alembic import op

revision = "0005_client_knowledge_sources"
down_revision = "0004_must_change_password"
branch_labels = None
depends_on = None

UPGRADE = [
    """
    CREATE TABLE knowledge_sources (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        client_id     UUID NOT NULL REFERENCES clients (id) ON DELETE RESTRICT,
        kind          VARCHAR(20) NOT NULL DEFAULT 'file' CHECK (kind IN ('file', 'page')),
        filename      VARCHAR(255) NOT NULL,
        file_type     VARCHAR(20) NOT NULL,
        size_bytes    BIGINT NOT NULL CHECK (size_bytes >= 0),
        -- SHA-256 of the file, hex.
        content_hash  CHAR(64) NOT NULL,
        status        VARCHAR(20) NOT NULL DEFAULT 'processing'
                      CHECK (status IN ('processing', 'ready', 'failed')),
        chunk_count   INTEGER NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
        error         TEXT,
        uploaded_by   UUID REFERENCES users (id) ON DELETE SET NULL,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_knowledge_sources_client_hash UNIQUE (client_id, content_hash)
    )
    """,
    "CREATE INDEX idx_knowledge_sources_client_created"
    " ON knowledge_sources (client_id, created_at DESC)",
    """
    CREATE TABLE chat_threads (
        thread_id   UUID PRIMARY KEY,
        client_id   UUID NOT NULL REFERENCES clients (id) ON DELETE RESTRICT,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX idx_chat_threads_client_id ON chat_threads (client_id)",
]

DOWNGRADE = [
    "DROP TABLE IF EXISTS chat_threads",
    "DROP TABLE IF EXISTS knowledge_sources",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
