"""Single-use links for account invites and password resets (services/invites.py).

- Only a SHA-256 hash of each token is stored, so a database copy holds no usable link.
- purpose: 'invite' (setup after an admin creates the user) or 'password_reset'.
- A row is spent by setting used_at. Expired or spent rows are never accepted.

Revision ID: 0011_invite_tokens
Revises: 0010_client_allowed_domains
Create Date: 2026-11-11
"""

from alembic import op

revision = "0011_invite_tokens"
down_revision = "0010_client_allowed_domains"
branch_labels = None
depends_on = None

UPGRADE = [
    """
    CREATE TABLE invite_tokens (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        token_hash CHAR(64) NOT NULL UNIQUE,
        purpose TEXT NOT NULL CHECK (purpose IN ('invite', 'password_reset')),
        expires_at TIMESTAMPTZ NOT NULL,
        used_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX invite_tokens_user_purpose_idx ON invite_tokens (user_id, purpose)",
]

DOWNGRADE = [
    "DROP TABLE IF EXISTS invite_tokens",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
