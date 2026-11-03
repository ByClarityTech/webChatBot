"""JWT login: drop server-side sessions; users get tokens_valid_after.

Login now issues a signed JWT instead of storing a session row. Tokens issued before
users.tokens_valid_after are refused, which is how deactivation and password resets end
a user's existing tokens without a session table.

Revision ID: 0003_jwt_auth
Revises: 0002_auth_tables
Create Date: 2026-11-04
"""

from alembic import op

revision = "0003_jwt_auth"
down_revision = "0002_auth_tables"
branch_labels = None
depends_on = None

UPGRADE = [
    "DROP TABLE IF EXISTS user_sessions",
    "ALTER TABLE users ADD COLUMN tokens_valid_after TIMESTAMPTZ",
]

# Restores 0002's user_sessions exactly (existing sessions are not recovered: users log in again).
DOWNGRADE = [
    "ALTER TABLE users DROP COLUMN IF EXISTS tokens_valid_after",
    """
    CREATE TABLE user_sessions (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id       UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        token_hash    CHAR(64) NOT NULL UNIQUE,
        client_ip     VARCHAR(45),
        user_agent    VARCHAR(500),
        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at    TIMESTAMPTZ NOT NULL,
        revoked_at    TIMESTAMPTZ
    )
    """,
    "CREATE INDEX idx_user_sessions_user_id ON user_sessions (user_id)",
    "CREATE INDEX idx_user_sessions_expires_at ON user_sessions (expires_at)",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
