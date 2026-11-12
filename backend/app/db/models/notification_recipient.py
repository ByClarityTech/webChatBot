"""Practice staff who receive notification emails (FR-34). Up to MAX_RECIPIENTS per client."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg

from app.db.session import get_db_connection

MAX_RECIPIENTS = 5


class DuplicateRecipient(Exception):
    pass


class RecipientLimitReached(Exception):
    pass


@dataclass(frozen=True)
class NotificationRecipient:
    id: UUID
    client_id: UUID
    email: str
    enabled: bool
    created_at: datetime


_COLUMNS = "id, client_id, email, enabled, created_at"


def _row(row: tuple) -> NotificationRecipient:
    return NotificationRecipient(*row)


async def list_recipients(client_id: UUID) -> list[NotificationRecipient]:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {_COLUMNS} FROM client_notification_recipients "
                "WHERE client_id = %s ORDER BY created_at, id",
                (client_id,),
            )
            rows = await cur.fetchall()
    return [_row(row) for row in rows]


async def add_recipient(
    client_id: UUID, email: str, *, actor_id: UUID | None, now: datetime
) -> NotificationRecipient:
    """The count and the insert run in one statement, so a sixth address cannot slip in."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute(
                    f"""
                    INSERT INTO client_notification_recipients
                        (client_id, email, enabled, created_at, created_by)
                    SELECT %s, %s, TRUE, %s, %s
                    WHERE (SELECT COUNT(*) FROM client_notification_recipients
                           WHERE client_id = %s) < %s
                    RETURNING {_COLUMNS}
                    """,
                    (client_id, email, now, actor_id, client_id, MAX_RECIPIENTS),
                )
            except psycopg.errors.UniqueViolation as exc:
                await conn.rollback()
                raise DuplicateRecipient from exc
            row = await cur.fetchone()
        await conn.commit()
    if row is None:
        raise RecipientLimitReached
    return _row(row)


async def set_recipient_enabled(
    client_id: UUID, recipient_id: UUID, enabled: bool
) -> NotificationRecipient | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"UPDATE client_notification_recipients SET enabled = %s "
                f"WHERE id = %s AND client_id = %s RETURNING {_COLUMNS}",
                (enabled, recipient_id, client_id),
            )
            row = await cur.fetchone()
        await conn.commit()
    return _row(row) if row else None


async def delete_recipient(client_id: UUID, recipient_id: UUID) -> bool:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM client_notification_recipients WHERE id = %s AND client_id = %s",
                (recipient_id, client_id),
            )
            deleted = cur.rowcount > 0
        await conn.commit()
    return deleted
