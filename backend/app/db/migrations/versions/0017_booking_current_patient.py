"""Bookings record whether the visitor is a current patient (FR-31, FR-32).

The column is nullable. Bookings made before this migration have no answer, and the admin views
show them as "Not recorded". New bookings must answer; the API refuses a booking without it.
The answer stays in the database and the admin views. It is not sent to Google Calendar.

Revision ID: 0017_booking_current_patient
Revises: 0016_widget_branding
Create Date: 2026-11-13
"""

from alembic import op

revision = "0017_booking_current_patient"
down_revision = "0016_widget_branding"
branch_labels = None
depends_on = None

UPGRADE = [
    "ALTER TABLE bookings ADD COLUMN is_current_patient BOOLEAN",
]

DOWNGRADE = [
    "ALTER TABLE bookings DROP COLUMN IF EXISTS is_current_patient",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
