"""Auth tables: clients, user_types, users, user_sessions; audit log gets user and client.

Revision ID: 0002_auth_tables
Revises: 0001_baseline
Create Date: 2026-11-02
"""

from alembic import op

revision = "0002_auth_tables"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

# Fixed IDs: code and the users check constraint below rely on super_admin = 1.
USER_TYPE_SUPER_ADMIN = 1
USER_TYPE_ADMIN = 2
USER_TYPE_SUPPORT = 3

UPGRADE = [
    """
    CREATE TABLE clients (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name        VARCHAR(255) NOT NULL,
        slug        VARCHAR(100) NOT NULL UNIQUE,
        status      VARCHAR(20) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'suspended')),
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE user_types (
        id    SMALLINT PRIMARY KEY,
        code  VARCHAR(50) NOT NULL UNIQUE,
        name  VARCHAR(100) NOT NULL
    )
    """,
    f"""
    INSERT INTO user_types (id, code, name) VALUES
        ({USER_TYPE_SUPER_ADMIN}, 'super_admin', 'Super Admin'),
        ({USER_TYPE_ADMIN}, 'admin', 'Admin'),
        ({USER_TYPE_SUPPORT}, 'support', 'Support User')
    """,
    f"""
    CREATE TABLE users (
        id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        client_id      UUID REFERENCES clients (id) ON DELETE RESTRICT,
        user_type_id   SMALLINT NOT NULL REFERENCES user_types (id),
        email          VARCHAR(255) NOT NULL,
        password_hash  TEXT NOT NULL,
        full_name      VARCHAR(255) NOT NULL,
        is_active      BOOLEAN NOT NULL DEFAULT TRUE,
        last_login_at  TIMESTAMPTZ,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        -- Super admins are platform-level (no client); admins and support users have one.
        CONSTRAINT ck_users_client_matches_type CHECK (
            (user_type_id = {USER_TYPE_SUPER_ADMIN} AND client_id IS NULL)
            OR (user_type_id <> {USER_TYPE_SUPER_ADMIN} AND client_id IS NOT NULL)
        )
    )
    """,
    # Email is unique across all clients, ignoring case.
    "CREATE UNIQUE INDEX uq_users_email_lower ON users (LOWER(email))",
    "CREATE INDEX idx_users_client_id ON users (client_id)",
    """
    CREATE TABLE user_sessions (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id       UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        -- SHA-256 hex of the cookie token; the raw token is never stored.
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
    """
    ALTER TABLE admin_audit_log
        ADD COLUMN user_id UUID REFERENCES users (id) ON DELETE SET NULL,
        ADD COLUMN client_id UUID REFERENCES clients (id) ON DELETE SET NULL
    """,
    "CREATE INDEX idx_admin_audit_log_client_id ON admin_audit_log (client_id, created_at DESC)",
    "CREATE INDEX idx_admin_audit_log_user_id ON admin_audit_log (user_id)",
]

DOWNGRADE = [
    "DROP INDEX IF EXISTS idx_admin_audit_log_user_id",
    "DROP INDEX IF EXISTS idx_admin_audit_log_client_id",
    "ALTER TABLE admin_audit_log DROP COLUMN IF EXISTS client_id, DROP COLUMN IF EXISTS user_id",
    "DROP TABLE IF EXISTS user_sessions",
    "DROP TABLE IF EXISTS users",
    "DROP TABLE IF EXISTS user_types",
    "DROP TABLE IF EXISTS clients",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
