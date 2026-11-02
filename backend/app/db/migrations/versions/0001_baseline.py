"""Baseline: tables that init_db created before Alembic.

Uses IF NOT EXISTS so it is safe on databases that already have these tables
(they are left untouched) and creates them on a fresh database.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-02
"""

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

# One statement per op.execute: psycopg rejects several commands in one prepared statement.
STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS google_tokens (
        id              SERIAL PRIMARY KEY,
        access_token    TEXT NOT NULL,
        refresh_token   TEXT NOT NULL,
        token_expiry    TIMESTAMPTZ NOT NULL,
        calendar_id     VARCHAR(255) NOT NULL DEFAULT 'primary',
        connected_email VARCHAR(255),
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bookings (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name            VARCHAR(255) NOT NULL,
        phone           VARCHAR(50) NOT NULL,
        email           VARCHAR(255) NOT NULL,
        slot_start      TIMESTAMPTZ NOT NULL UNIQUE,
        slot_end        TIMESTAMPTZ NOT NULL,
        google_event_id VARCHAR(255),
        thread_id       UUID,
        status          VARCHAR(50) NOT NULL DEFAULT 'confirmed',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_bookings_slot_start ON bookings (slot_start)",
    "CREATE INDEX IF NOT EXISTS idx_bookings_email ON bookings (email)",
    """
    CREATE TABLE IF NOT EXISTS oauth_pending_states (
        state          VARCHAR(255) PRIMARY KEY,
        code_verifier  TEXT NOT NULL,
        expires_at     TIMESTAMPTZ NOT NULL,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_oauth_pending_expires ON oauth_pending_states (expires_at)",
    """
    CREATE TABLE IF NOT EXISTS business_hours_settings (
        id                    SERIAL PRIMARY KEY,
        business_days         INTEGER[] NOT NULL,
        start_time            VARCHAR(5) NOT NULL,
        end_time              VARCHAR(5) NOT NULL,
        slot_duration_minutes INTEGER NOT NULL,
        slot_buffer_minutes   INTEGER NOT NULL,
        timezone              VARCHAR(100) NOT NULL,
        updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS admin_audit_log (
        id              SERIAL PRIMARY KEY,
        action          VARCHAR(100) NOT NULL,
        resource_type   VARCHAR(100),
        resource_id     VARCHAR(255),
        client_ip       VARCHAR(45),
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_admin_audit_log_created_at"
    " ON admin_audit_log (created_at DESC)",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    # Deliberately a no-op: these tables hold existing bookings and settings,
    # and dropping them must never happen through a routine downgrade.
    pass
