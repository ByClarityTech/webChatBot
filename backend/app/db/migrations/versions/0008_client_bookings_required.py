"""Per-client bookings, business hours and Google Calendar connections (phase 2 of 2).

client_id becomes required on bookings, google_tokens and business_hours_settings, and on
the OAuth connect flows. The old global UNIQUE (slot_start) on bookings goes: a slot is now
held per client (uq_bookings_client_slot_active, from 0007), so two clients can book the same
time and a cancelled booking no longer blocks its slot.

Stops without changing anything if a row still has no client: run
scripts/assign_bookings_to_client.py first.

Revision ID: 0008_client_bookings_required
Revises: 0007_client_bookings
Create Date: 2026-11-04
"""

from alembic import op

revision = "0008_client_bookings_required"
down_revision = "0007_client_bookings"
branch_labels = None
depends_on = None

CLIENT_TABLES = ("bookings", "google_tokens", "business_hours_settings")

GUARD = """
DO $$
DECLARE
    missing TEXT := '';
BEGIN
    IF EXISTS (SELECT 1 FROM bookings WHERE client_id IS NULL) THEN
        missing := missing || ' bookings';
    END IF;
    IF EXISTS (SELECT 1 FROM google_tokens WHERE client_id IS NULL) THEN
        missing := missing || ' google_tokens';
    END IF;
    IF EXISTS (SELECT 1 FROM business_hours_settings WHERE client_id IS NULL) THEN
        missing := missing || ' business_hours_settings';
    END IF;
    IF missing <> '' THEN
        RAISE EXCEPTION
            'Rows without a client in:%. Run scripts/assign_bookings_to_client.py first.',
            missing;
    END IF;
END $$
"""

UPGRADE = [
    GUARD,
    *(f"ALTER TABLE {table} ALTER COLUMN client_id SET NOT NULL" for table in CLIENT_TABLES),
    # Connect flows last 15 minutes; any started by older code cannot be finished safely.
    "DELETE FROM oauth_pending_states WHERE client_id IS NULL OR user_id IS NULL",
    """
    ALTER TABLE oauth_pending_states
        ALTER COLUMN client_id SET NOT NULL,
        ALTER COLUMN user_id SET NOT NULL
    """,
    "ALTER TABLE bookings DROP CONSTRAINT IF EXISTS bookings_slot_start_key",
]

DOWNGRADE = [
    # Fails if two clients (or a cancelled and a new booking) now share a slot start.
    "ALTER TABLE bookings ADD CONSTRAINT bookings_slot_start_key UNIQUE (slot_start)",
    """
    ALTER TABLE oauth_pending_states
        ALTER COLUMN user_id DROP NOT NULL,
        ALTER COLUMN client_id DROP NOT NULL
    """,
    *(f"ALTER TABLE {table} ALTER COLUMN client_id DROP NOT NULL" for table in CLIENT_TABLES),
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
