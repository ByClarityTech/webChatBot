"""Calendar mode per client (Google, built-in, or no online booking), minimum notice, and a
database rule against overlapping bookings.

- clients.calendar_mode: 'google' (busy times from the client's Google Calendar), 'built_in'
  (business hours minus the client's own bookings) or 'none' (no online booking). Clients
  that already have a Google connection start on 'google', the rest on 'none'.
- business_hours_settings.min_notice_minutes: how soon a slot may start, from now.
- bookings: an exclusion constraint replaces the same-start unique index, so two active
  (pending or confirmed) bookings of one client can never overlap in time, even after the
  slot length changes. Needs the btree_gist extension (trusted: a database owner can
  create it).

Revision ID: 0009_built_in_calendar
Revises: 0008_client_bookings_required
Create Date: 2026-11-04
"""

from alembic import op

revision = "0009_built_in_calendar"
down_revision = "0008_client_bookings_required"
branch_labels = None
depends_on = None

UPGRADE = [
    "CREATE EXTENSION IF NOT EXISTS btree_gist",
    # ---- calendar mode
    """
    ALTER TABLE clients ADD COLUMN calendar_mode VARCHAR(20) NOT NULL DEFAULT 'none'
        CHECK (calendar_mode IN ('none', 'google', 'built_in'))
    """,
    "UPDATE clients SET calendar_mode = 'google' WHERE id IN (SELECT client_id FROM google_tokens)",
    # ---- minimum notice
    """
    ALTER TABLE business_hours_settings ADD COLUMN min_notice_minutes INTEGER NOT NULL DEFAULT 60
        CHECK (min_notice_minutes BETWEEN 0 AND 10080)
    """,
    # ---- no overlapping active bookings per client
    "ALTER TABLE bookings ADD CONSTRAINT ck_bookings_slot_order CHECK (slot_end > slot_start)",
    """
    ALTER TABLE bookings ADD CONSTRAINT ex_bookings_client_no_overlap
        EXCLUDE USING gist (client_id WITH =, tstzrange(slot_start, slot_end) WITH &&)
        WHERE (status IN ('pending', 'confirmed'))
    """,
    "DROP INDEX IF EXISTS uq_bookings_client_slot_active",
]

DOWNGRADE = [
    """
    CREATE UNIQUE INDEX uq_bookings_client_slot_active ON bookings (client_id, slot_start)
        WHERE status IN ('pending', 'confirmed')
    """,
    "ALTER TABLE bookings DROP CONSTRAINT IF EXISTS ex_bookings_client_no_overlap",
    "ALTER TABLE bookings DROP CONSTRAINT IF EXISTS ck_bookings_slot_order",
    "ALTER TABLE business_hours_settings DROP COLUMN IF EXISTS min_notice_minutes",
    "ALTER TABLE clients DROP COLUMN IF EXISTS calendar_mode",
    # btree_gist is left installed: other objects may use it.
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
