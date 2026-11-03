"""users.must_change_password: set when an admin chooses a user's password.

New users created by an admin, and admin password resets, must pick their own password at
the next sign-in. Until they do, the API allows only /auth/me, /auth/password and logout.
Existing users are not affected (default false).

Revision ID: 0004_must_change_password
Revises: 0003_jwt_auth
Create Date: 2026-11-04
"""

from alembic import op

revision = "0004_must_change_password"
down_revision = "0003_jwt_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT FALSE")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS must_change_password")
