"""Website crawler (FR-02, FR-05): the practice site, its crawled pages and pending changes.

clients.website_url: the practice's site (https://host, no path). The crawl stays on that host
and its www. counterpart.

crawl_runs: one row per crawl. At most one running crawl per client (partial unique index).

crawled_pages: one row per page URL (canonical form). live_content_hash and live_text describe
the version the chatbot uses now; rejected_content_hash is the last version an admin rejected.

pending_knowledge_updates: a proposed change (new, changed or removed) waiting for an admin.
Its text is kept here and is not indexed until it is approved. At most one open change per page.

Revision ID: 0018_website_crawler
Revises: 0017_booking_current_patient
Create Date: 2026-11-13
"""

from alembic import op

revision = "0018_website_crawler"
down_revision = "0017_booking_current_patient"
branch_labels = None
depends_on = None

UPGRADE = [
    """
    ALTER TABLE clients ADD COLUMN website_url TEXT
        CHECK (website_url IS NULL OR website_url LIKE 'https://%')
    """,
    """
    CREATE TABLE crawl_runs (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        client_id     UUID NOT NULL REFERENCES clients (id) ON DELETE RESTRICT,
        trigger       VARCHAR(10) NOT NULL CHECK (trigger IN ('scheduled', 'manual')),
        status        VARCHAR(12) NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running', 'completed', 'failed')),
        pages_fetched INTEGER NOT NULL DEFAULT 0 CHECK (pages_fetched >= 0),
        pages_changed INTEGER NOT NULL DEFAULT 0 CHECK (pages_changed >= 0),
        pages_failed  INTEGER NOT NULL DEFAULT 0 CHECK (pages_failed >= 0),
        error         TEXT,
        requested_by  UUID REFERENCES users (id) ON DELETE SET NULL,
        started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        finished_at   TIMESTAMPTZ
    )
    """,
    "CREATE UNIQUE INDEX uq_crawl_runs_one_running ON crawl_runs (client_id)"
    " WHERE status = 'running'",
    "CREATE INDEX idx_crawl_runs_client_started ON crawl_runs (client_id, started_at DESC)",
    """
    CREATE TABLE crawled_pages (
        id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        client_id             UUID NOT NULL REFERENCES clients (id) ON DELETE RESTRICT,
        url                   TEXT NOT NULL,
        included              BOOLEAN NOT NULL DEFAULT TRUE,
        status                VARCHAR(12) NOT NULL DEFAULT 'active'
                              CHECK (status IN ('active', 'missing')),
        title                 TEXT,
        live_content_hash     CHAR(64),
        live_text             TEXT,
        rejected_content_hash CHAR(64),
        last_status_code      INTEGER,
        last_error            TEXT,
        last_crawled_at       TIMESTAMPTZ,
        created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_crawled_pages_client_url UNIQUE (client_id, url)
    )
    """,
    """
    CREATE TABLE pending_knowledge_updates (
        id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        client_id      UUID NOT NULL REFERENCES clients (id) ON DELETE RESTRICT,
        page_id        UUID NOT NULL REFERENCES crawled_pages (id) ON DELETE CASCADE,
        crawl_run_id   UUID REFERENCES crawl_runs (id) ON DELETE SET NULL,
        change_type    VARCHAR(10) NOT NULL CHECK (change_type IN ('new', 'changed', 'removed')),
        status         VARCHAR(12) NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'approved', 'rejected', 'superseded')),
        proposed_title TEXT,
        proposed_text  TEXT,
        proposed_hash  CHAR(64),
        words_added    INTEGER NOT NULL DEFAULT 0 CHECK (words_added >= 0),
        words_removed  INTEGER NOT NULL DEFAULT 0 CHECK (words_removed >= 0),
        decided_by     UUID REFERENCES users (id) ON DELETE SET NULL,
        decided_at     TIMESTAMPTZ,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE UNIQUE INDEX uq_pending_one_open_per_page"
    " ON pending_knowledge_updates (page_id) WHERE status = 'pending'",
    "CREATE INDEX idx_pending_client_status ON pending_knowledge_updates (client_id, status)",
]

DOWNGRADE = [
    "DROP TABLE IF EXISTS pending_knowledge_updates",
    "DROP TABLE IF EXISTS crawled_pages",
    "DROP TABLE IF EXISTS crawl_runs",
    "ALTER TABLE clients DROP COLUMN IF EXISTS website_url",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
