"""Notification emails to practice staff (FR-34): recipients and a durable outbox.

- client_notification_recipients: up to five addresses per client (enforced in code, with the
  insert guarded by a count). A recipient can be switched off without being removed.
- notification_outbox: one row per (kind, source, recipient). It is the email log too. Rows hold
  only IDs, a fixed subject, and a recipient address. No visitor details are stored here.
  The dispatcher claims due rows with FOR UPDATE SKIP LOCKED and records the outcome.

Revision ID: 0015_notifications
Revises: 0014_staff_inbox
Create Date: 2026-11-13
"""

from alembic import op

revision = "0015_notifications"
down_revision = "0014_staff_inbox"
branch_labels = None
depends_on = None

UPGRADE = [
    """
    CREATE TABLE client_notification_recipients (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        client_id   UUID NOT NULL REFERENCES clients (id) ON DELETE CASCADE,
        email       TEXT NOT NULL CHECK (LENGTH(email) BETWEEN 3 AND 254 AND email LIKE '%_@_%'),
        enabled     BOOLEAN NOT NULL DEFAULT TRUE,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        created_by  UUID REFERENCES users (id) ON DELETE SET NULL
    )
    """,
    "CREATE UNIQUE INDEX client_notification_recipients_one "
    "ON client_notification_recipients (client_id, LOWER(email))",
    """
    CREATE TABLE notification_outbox (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        client_id       UUID NOT NULL REFERENCES clients (id) ON DELETE CASCADE,
        kind            TEXT NOT NULL CHECK (kind IN ('booking', 'callback')),
        source_id       UUID NOT NULL,
        recipient       TEXT NOT NULL CHECK (LENGTH(recipient) BETWEEN 3 AND 254),
        subject         TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued', 'sent', 'failed')),
        attempts        INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_error      TEXT CHECK (last_error IS NULL OR LENGTH(last_error) <= 60),
        ses_message_id  TEXT,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        sent_at         TIMESTAMPTZ,
        UNIQUE (kind, source_id, recipient)
    )
    """,
    "CREATE INDEX notification_outbox_due ON notification_outbox (status, next_attempt_at)",
]

DOWNGRADE = [
    "DROP TABLE IF EXISTS notification_outbox",
    "DROP TABLE IF EXISTS client_notification_recipients",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
