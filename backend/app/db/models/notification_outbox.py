"""The notification outbox (FR-34): what to email, to whom, and how the attempts went.

Rows are written in the same transaction as the event that causes them (a callback request)
or right after it (a booking). The ON CONFLICT clause makes a repeated trigger a no-op.
A row is claimed by moving its next_attempt_at forward (a lease) inside a FOR UPDATE SKIP
LOCKED subquery, so two dispatchers never send the same row.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.db.session import get_db_connection

KIND_BOOKING = "booking"
KIND_CALLBACK = "callback"

# Fixed subjects. They live here so the enqueue hooks can use them without importing email code.
BOOKING_SUBJECT = "New booking request from the website"
CALLBACK_SUBJECT = "Callback request from the website"

STATUS_QUEUED = "queued"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"

# Recipients are copied from the list at enqueue time, and only enabled ones are used.
_ENQUEUE_SQL = """
    INSERT INTO notification_outbox
        (client_id, kind, source_id, recipient, subject, next_attempt_at)
    SELECT client_id, %s, %s, email, %s, %s
    FROM client_notification_recipients
    WHERE client_id = %s AND enabled
    ON CONFLICT (kind, source_id, recipient) DO NOTHING
"""


async def enqueue_on(
    cur, *, client_id: UUID, kind: str, source_id: UUID, subject: str, now: datetime
) -> None:
    """Run inside the caller's transaction, on the caller's cursor."""
    await cur.execute(_ENQUEUE_SQL, (kind, source_id, subject, now, client_id))


async def enqueue(
    *, client_id: UUID, kind: str, source_id: UUID, subject: str, now: datetime
) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await enqueue_on(
                cur, client_id=client_id, kind=kind, source_id=source_id, subject=subject, now=now
            )
        await conn.commit()


@dataclass(frozen=True)
class DueNotification:
    id: UUID
    client_id: UUID
    kind: str
    source_id: UUID
    recipient: str
    subject: str
    attempts: int
    client_name: str
    callback_source: str | None


async def claim_due(*, now: datetime, lease_until: datetime, limit: int) -> list[DueNotification]:
    """Claims up to `limit` queued rows whose time has come, and counts the attempt."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                WITH due AS (
                    SELECT id FROM notification_outbox
                    WHERE status = %s AND next_attempt_at <= %s
                    ORDER BY next_attempt_at
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE notification_outbox o
                SET attempts = o.attempts + 1, next_attempt_at = %s
                FROM due
                WHERE o.id = due.id
                RETURNING o.id, o.client_id, o.kind, o.source_id, o.recipient, o.subject,
                          o.attempts
                """,
                (STATUS_QUEUED, now, limit, lease_until),
            )
            claimed = await cur.fetchall()
            if not claimed:
                await conn.commit()
                return []
            ids = [row[0] for row in claimed]
            await cur.execute(
                """
                SELECT o.id, c.name, cr.source
                FROM notification_outbox o
                JOIN clients c ON c.id = o.client_id
                LEFT JOIN callback_requests cr ON o.kind = 'callback' AND cr.id = o.source_id
                WHERE o.id = ANY(%s)
                """,
                (ids,),
            )
            names = {row[0]: (row[1], row[2]) for row in await cur.fetchall()}
        await conn.commit()
    items = []
    for row in claimed:
        client_name, source = names.get(row[0], ("", None))
        items.append(
            DueNotification(
                id=row[0],
                client_id=row[1],
                kind=row[2],
                source_id=row[3],
                recipient=row[4],
                subject=row[5],
                attempts=row[6],
                client_name=client_name,
                callback_source=source,
            )
        )
    return items


async def mark_sent(row_id: UUID, *, ses_message_id: str | None, now: datetime) -> None:
    await _update(
        "UPDATE notification_outbox SET status = %s, sent_at = %s, ses_message_id = %s, "
        "last_error = NULL WHERE id = %s",
        (STATUS_SENT, now, ses_message_id or None, row_id),
    )


async def mark_retry(row_id: UUID, *, next_attempt_at: datetime, error_code: str) -> None:
    await _update(
        "UPDATE notification_outbox SET next_attempt_at = %s, last_error = %s WHERE id = %s",
        (next_attempt_at, error_code, row_id),
    )


async def mark_failed(row_id: UUID, *, error_code: str) -> None:
    await _update(
        "UPDATE notification_outbox SET status = %s, last_error = %s WHERE id = %s",
        (STATUS_FAILED, error_code, row_id),
    )


async def _update(sql: str, params: tuple) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
        await conn.commit()
