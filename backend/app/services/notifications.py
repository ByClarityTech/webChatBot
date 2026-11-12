"""Sends the notification outbox to practice staff (FR-34).

The dispatcher wakes every INTERVAL_SECONDS, claims due rows, and sends each one through SES in
a worker thread, so a slow provider call never blocks the event loop. SES is called once per
dispatch. The outbox owns the retries, so the schedule survives a restart.

Retryable failures (throttling, SES unavailable, unreachable) wait 1, 2, 4, then 8 minutes.
Each row gets MAX_ATTEMPTS sends in total. A rejected address or a missing configuration fails
at once. Rows keep only a fixed error code, never provider text.
"""

import asyncio
import logging
from datetime import timedelta
from uuid import UUID

from app.db.models import notification_outbox as outbox
from app.services.chat_sessions import utc_now
from app.services.email import get_email_sender
from app.services.email.base import EmailDeliveryError, EmailSender
from app.services.email.notifications import booking_notification, callback_notification

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
LEASE = timedelta(minutes=5)
BATCH_SIZE = 20
INTERVAL_SECONDS = 10.0


def retry_delay(attempts: int) -> timedelta:
    """The wait after the given number of failed attempts: 1, 2, 4, 8 minutes."""
    return timedelta(minutes=2 ** (attempts - 1))


def build_message(item: outbox.DueNotification):
    if item.kind == outbox.KIND_BOOKING:
        return booking_notification(item.recipient, item.client_name)
    return callback_notification(item.recipient, item.client_name, item.callback_source)


async def enqueue_booking(client_id: UUID, booking_id: UUID) -> None:
    await outbox.enqueue(
        client_id=client_id,
        kind=outbox.KIND_BOOKING,
        source_id=booking_id,
        subject=outbox.BOOKING_SUBJECT,
        now=utc_now(),
    )


async def dispatch_once(sender: EmailSender | None = None) -> int:
    """Sends one batch of due rows. Returns how many rows were claimed."""
    now = utc_now()
    items = await outbox.claim_due(now=now, lease_until=now + LEASE, limit=BATCH_SIZE)
    if not items:
        return 0
    sender = sender or get_email_sender(max_attempts=1)
    for item in items:
        await _send_one(item, sender)
    return len(items)


async def _send_one(item: outbox.DueNotification, sender: EmailSender) -> None:
    try:
        message = build_message(item)
    except Exception as exc:
        logger.error("Notification render failed: kind=%s error=%s", item.kind, type(exc).__name__)
        await outbox.mark_failed(item.id, error_code="RENDER_FAILED")
        return

    try:
        message_id = await asyncio.to_thread(
            sender.send_email,
            message.to_email,
            message.subject,
            message.html_content,
            message.text_content,
        )
    except EmailDeliveryError as exc:
        code, retryable = exc.code, exc.retryable
    except Exception as exc:
        # Unknown failure: treat as transient, and log only the exception type.
        code, retryable = "SEND_CRASHED", True
        logger.warning("Notification send crashed: kind=%s error=%s", item.kind, type(exc).__name__)
    else:
        await outbox.mark_sent(item.id, ses_message_id=message_id, now=utc_now())
        logger.info("Notification sent: kind=%s attempt=%d", item.kind, item.attempts)
        return

    if retryable and item.attempts < MAX_ATTEMPTS:
        await outbox.mark_retry(
            item.id, next_attempt_at=utc_now() + retry_delay(item.attempts), error_code=code
        )
        logger.warning(
            "Notification retry scheduled: kind=%s code=%s attempt=%d",
            item.kind,
            code,
            item.attempts,
        )
    else:
        await outbox.mark_failed(item.id, error_code=code)
        logger.error(
            "Notification failed: kind=%s code=%s attempt=%d",
            item.kind,
            code,
            item.attempts,
        )


async def run_dispatcher(interval: float = INTERVAL_SECONDS) -> None:
    """Runs until cancelled. A failed cycle is logged and the loop carries on."""
    while True:
        try:
            await dispatch_once()
        except Exception as exc:
            logger.error("Notification dispatch failed: error=%s", type(exc).__name__)
        await asyncio.sleep(interval)
