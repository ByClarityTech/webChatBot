"""Widget branding, publish status, logo, and installation checks (FR-11, FR-12).

- client_widget_settings: one row per client, created on first save. Holds the live/paused
  status, the bot name, the greeting, the header colors, and the logo bytes. Kept apart from
  clients so the logo is never loaded with every client lookup.
- widget_verifications: one row per installation check. Used for the hourly limit and for
  audit. Stores the URL the admin checked and a fixed result code, never the page content.

Revision ID: 0016_widget_branding
Revises: 0015_notifications
Create Date: 2026-11-13
"""

from alembic import op

revision = "0016_widget_branding"
down_revision = "0015_notifications"
branch_labels = None
depends_on = None

UPGRADE = [
    """
    CREATE TABLE client_widget_settings (
        client_id          UUID PRIMARY KEY REFERENCES clients (id) ON DELETE CASCADE,
        status             TEXT NOT NULL DEFAULT 'live' CHECK (status IN ('live', 'paused')),
        bot_name           TEXT CHECK (bot_name IS NULL OR LENGTH(TRIM(bot_name)) BETWEEN 1 AND 60),
        greeting           TEXT CHECK (greeting IS NULL OR LENGTH(greeting) <= 200),
        header_color       TEXT CHECK (header_color IS NULL OR header_color ~ '^#[0-9A-Fa-f]{6}$'),
        header_text_color  TEXT CHECK (header_text_color IS NULL
                           OR header_text_color ~ '^#[0-9A-Fa-f]{6}$'),
        logo_content_type  TEXT CHECK (logo_content_type IS NULL
                           OR logo_content_type IN ('image/png', 'image/jpeg', 'image/webp')),
        logo_bytes         BYTEA CHECK (logo_bytes IS NULL OR OCTET_LENGTH(logo_bytes) <= 262144),
        logo_sha256        TEXT CHECK (logo_sha256 IS NULL OR logo_sha256 ~ '^[0-9a-f]{64}$'),
        CHECK ((logo_bytes IS NULL) = (logo_content_type IS NULL)),
        CHECK ((logo_bytes IS NULL) = (logo_sha256 IS NULL)),
        updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_by         UUID REFERENCES users (id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE widget_verifications (
        id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        client_id    UUID NOT NULL REFERENCES clients (id) ON DELETE CASCADE,
        checked_url  TEXT NOT NULL CHECK (LENGTH(checked_url) <= 2048),
        result       TEXT NOT NULL CHECK (LENGTH(result) <= 40),
        checked_by   UUID REFERENCES users (id) ON DELETE SET NULL,
        checked_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX widget_verifications_recent ON widget_verifications (client_id, checked_at)",
]

DOWNGRADE = [
    "DROP TABLE IF EXISTS widget_verifications",
    "DROP TABLE IF EXISTS client_widget_settings",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
