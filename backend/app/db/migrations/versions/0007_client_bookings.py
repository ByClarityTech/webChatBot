"""Per-client bookings, business hours and Google Calendar connections (phase 1 of 2).

Adds client_id (nullable for now) to bookings, google_tokens, business_hours_settings and
oauth_pending_states, so existing rows survive. scripts/assign_bookings_to_client.py then
gives existing rows to a client, and migration 0008 makes client_id required.

- bookings: a slot is held per client while a booking is pending or confirmed; a cancelled
  booking no longer blocks its slot. The old global UNIQUE (slot_start) stays until 0008,
  because code before the per-client booking service still saves bookings without a client.
- google_tokens: one connection per client, with a health status (needs_reconnect after a
  revoked or failed refresh).
- business_hours_settings: one row per client.
- oauth_pending_states: which client and user started the connect flow.

Revision ID: 0007_client_bookings
Revises: 0006_faqs
Create Date: 2026-11-04
"""

from alembic import op

revision = "0007_client_bookings"
down_revision = "0006_faqs"
branch_labels = None
depends_on = None

UPGRADE = [
    # ---- bookings
    "ALTER TABLE bookings ADD COLUMN client_id UUID REFERENCES clients (id) ON DELETE RESTRICT",
    """
    ALTER TABLE bookings ADD CONSTRAINT ck_bookings_status
        CHECK (status IN ('pending', 'confirmed', 'cancelled'))
    """,
    # Pending holds the slot while the Google event is created; cancelled frees it.
    """
    CREATE UNIQUE INDEX uq_bookings_client_slot_active ON bookings (client_id, slot_start)
        WHERE status IN ('pending', 'confirmed')
    """,
    "CREATE INDEX idx_bookings_client_slot ON bookings (client_id, slot_start)",
    # ---- google_tokens
    """
    ALTER TABLE google_tokens
        ADD COLUMN client_id UUID REFERENCES clients (id) ON DELETE RESTRICT,
        ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'ok'
            CHECK (status IN ('ok', 'needs_reconnect')),
        ADD COLUMN last_error TEXT
    """,
    "CREATE UNIQUE INDEX uq_google_tokens_client ON google_tokens (client_id)",
    # ---- business_hours_settings
    """
    ALTER TABLE business_hours_settings
        ADD COLUMN client_id UUID REFERENCES clients (id) ON DELETE RESTRICT
    """,
    "CREATE UNIQUE INDEX uq_business_hours_client ON business_hours_settings (client_id)",
    # ---- oauth_pending_states (short-lived: drop half-finished flows from before)
    "DELETE FROM oauth_pending_states",
    """
    ALTER TABLE oauth_pending_states
        ADD COLUMN client_id UUID REFERENCES clients (id) ON DELETE CASCADE,
        ADD COLUMN user_id UUID REFERENCES users (id) ON DELETE CASCADE
    """,
]

DOWNGRADE = [
    """
    ALTER TABLE oauth_pending_states
        DROP COLUMN IF EXISTS user_id,
        DROP COLUMN IF EXISTS client_id
    """,
    "DROP INDEX IF EXISTS uq_business_hours_client",
    "ALTER TABLE business_hours_settings DROP COLUMN IF EXISTS client_id",
    "DROP INDEX IF EXISTS uq_google_tokens_client",
    """
    ALTER TABLE google_tokens
        DROP COLUMN IF EXISTS last_error,
        DROP COLUMN IF EXISTS status,
        DROP COLUMN IF EXISTS client_id
    """,
    "DROP INDEX IF EXISTS idx_bookings_client_slot",
    "DROP INDEX IF EXISTS uq_bookings_client_slot_active",
    "ALTER TABLE bookings DROP CONSTRAINT IF EXISTS ck_bookings_status",
    "ALTER TABLE bookings DROP COLUMN IF EXISTS client_id",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
