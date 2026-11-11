"""Per-client domain allowlist for the public widget (R-03).

- clients.allowed_domains: JSON list of host patterns, e.g. ["example.com", "*.example.com",
  "localhost:3000"]. Empty by default; production requests are refused until it is set.

Revision ID: 0010_client_allowed_domains
Revises: 0009_built_in_calendar
Create Date: 2026-11-10
"""

from alembic import op

revision = "0010_client_allowed_domains"
down_revision = "0009_built_in_calendar"
branch_labels = None
depends_on = None

UPGRADE = [
    """
    ALTER TABLE clients ADD COLUMN allowed_domains JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(allowed_domains) = 'array')
    """,
]

DOWNGRADE = [
    "ALTER TABLE clients DROP COLUMN IF EXISTS allowed_domains",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
