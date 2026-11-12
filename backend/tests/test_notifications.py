"""Notification emails to practice staff (FR-34).

Covers: zero-PHI templates, the backoff schedule, SES error codes with one attempt per dispatch,
the outbox dispatcher (claims, retries, the 5-attempt cap, permanent failures, worker threads),
the enqueue hooks in the callback and booking paths, and the recipient routes.

The outbox and recipient tables are replaced by small in-memory fakes that apply the same
conditions the SQL does. The SQL itself needs a live database; it is checked here by its text
only, and needs a database run before release.
"""

import asyncio
import dataclasses
import logging
import threading
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from auth_helpers import make_client_record, signed_in
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin import notification_recipients as recipient_routes
from app.booking import service as booking_service
from app.core.roles import UserType
from app.db.models import callback_request as callback_model
from app.db.models import notification_outbox as outbox
from app.db.models.callback_request import SOURCE_NO_STAFF, SOURCE_TIMEOUT
from app.db.models.notification_recipient import (
    MAX_RECIPIENTS,
    DuplicateRecipient,
    NotificationRecipient,
    RecipientLimitReached,
)
from app.services import notifications as dispatcher
from app.services.email.base import EmailDeliveryError
from app.services.email.console import ConsoleEmailSender
from app.services.email.notifications import (
    BOOKING_SUBJECT,
    CALLBACK_SUBJECT,
    booking_notification,
    callback_notification,
)
from app.services.email.ses import SesEmailSender

T0 = datetime(2030, 7, 1, 10, 0, tzinfo=UTC)
CLINIC_ID = uuid4()
PHI_NAME = "Jane Doe"
PHI_PHONE = "3125550187"
PHI_EMAIL = "jane.doe@example.org"
PHI_MESSAGE = "my tooth hurts since Tuesday"


# ---------------------------------------------------------------- templates


def test_booking_email_carries_no_visitor_details():
    message = booking_notification("staff@clinic.com", "Clarity Dental")
    everything = " ".join(
        [message.subject, message.html_content, message.text_content, message.to_email]
    )
    for phi in (PHI_NAME, PHI_PHONE, PHI_EMAIL, PHI_MESSAGE):
        assert phi not in everything
    assert message.subject == BOOKING_SUBJECT
    assert "/admin/bookings" in message.text_content
    assert "Clarity Dental" in message.text_content


def test_callback_email_has_source_label_and_no_visitor_details():
    message = callback_notification("staff@clinic.com", "Clarity Dental", SOURCE_TIMEOUT)
    assert message.subject == CALLBACK_SUBJECT
    assert "Not answered in 3 minutes" in message.text_content
    assert "Not answered in 3 minutes" in message.html_content
    assert "/admin/inbox" in message.text_content
    for phi in (PHI_NAME, PHI_PHONE, PHI_EMAIL, PHI_MESSAGE):
        assert phi not in message.html_content + message.text_content


@pytest.mark.parametrize(
    ("source", "label"),
    [
        (SOURCE_NO_STAFF, "No staff online"),
        (SOURCE_TIMEOUT, "Not answered in 3 minutes"),
        ("Callback requested in chat", "Asked in chat"),
    ],
)
def test_each_callback_source_has_its_email_label(source, label):
    assert label in callback_notification("a@b.com", "P", source).text_content


def test_unknown_callback_source_gets_a_neutral_label():
    text = callback_notification("a@b.com", "P", "something new").text_content
    assert "Reason: Callback requested." in text


def test_practice_name_is_escaped_in_html():
    message = booking_notification("a@b.com", "<script>alert(1)</script> Dental")
    assert "<script>" not in message.html_content
    assert "&lt;script&gt;" in message.html_content


# ---------------------------------------------------------------- backoff schedule


@pytest.mark.parametrize(
    ("attempts", "minutes"),
    [(1, 1), (2, 2), (3, 4), (4, 8)],
)
def test_retry_waits_one_two_four_eight_minutes(attempts, minutes):
    assert dispatcher.retry_delay(attempts) == timedelta(minutes=minutes)


# ---------------------------------------------------------------- SES: one attempt, error codes


def _ses(client, max_attempts):
    return SesEmailSender(
        access_key_id="AKIAEXAMPLEKEYID",
        secret_access_key="unused",
        region="ap-south-1",
        from_email="tech@claritytechlabs.com",
        from_name="Clarity Dental AI",
        max_attempts=max_attempts,
        client=client,
    )


def test_single_attempt_throttling_is_retryable_and_called_once():
    error = ClientError({"Error": {"Code": "Throttling", "Message": "slow down"}}, "SendEmail")
    client = MagicMock()
    client.send_email.side_effect = error
    with pytest.raises(EmailDeliveryError) as raised:
        _ses(client, 1).send_email("a@b.com", "S", "<p>x</p>", "x")
    assert raised.value.code == "SES_THROTTLED"
    assert raised.value.retryable is True
    assert client.send_email.call_count == 1


def test_service_unavailable_maps_to_retryable_code():
    error = ClientError({"Error": {"Code": "ServiceUnavailable", "Message": ""}}, "SendEmail")
    client = MagicMock()
    client.send_email.side_effect = error
    with pytest.raises(EmailDeliveryError) as raised:
        _ses(client, 1).send_email("a@b.com", "S", "<p>x</p>", "x")
    assert raised.value.code == "SES_UNAVAILABLE"
    assert raised.value.retryable is True


def test_unreachable_is_retryable_and_hides_the_cause():
    client = MagicMock()
    client.send_email.side_effect = BotoCoreError()
    with pytest.raises(EmailDeliveryError) as raised:
        _ses(client, 1).send_email("a@b.com", "S", "<p>x</p>", "x")
    assert raised.value.code == "SES_UNREACHABLE"
    assert raised.value.retryable is True
    assert raised.value.__cause__ is None


def test_success_returns_the_message_id():
    client = MagicMock()
    client.send_email.return_value = {"MessageId": "ses-123"}
    assert _ses(client, 1).send_email("a@b.com", "S", "<p>x</p>", "x") == "ses-123"


def test_console_sender_fails_outside_development_without_retry(caplog):
    sender = ConsoleEmailSender(app_env="production")
    with caplog.at_level(logging.INFO), pytest.raises(EmailDeliveryError) as raised:
        sender.send_email("jane@clinic.com", "S", "<p>x</p>", "body")
    assert raised.value.code == "SES_NOT_CONFIGURED"
    assert raised.value.retryable is False
    assert "jane@clinic.com" not in caplog.text


# ---------------------------------------------------------------- outbox fake


@dataclasses.dataclass
class Row:
    id: object
    client_id: object
    kind: str
    source_id: object
    recipient: str
    subject: str
    client_name: str
    callback_source: str | None
    status: str = outbox.STATUS_QUEUED
    attempts: int = 0
    next_attempt_at: datetime = T0
    last_error: str | None = None
    ses_message_id: str | None = None
    sent_at: datetime | None = None


class FakeOutbox:
    def __init__(self) -> None:
        self.rows: dict = {}

    def add(self, *, kind=outbox.KIND_CALLBACK, source=SOURCE_TIMEOUT, attempts=0) -> object:
        row_id = uuid4()
        self.rows[row_id] = Row(
            id=row_id,
            client_id=CLINIC_ID,
            kind=kind,
            source_id=uuid4(),
            recipient="staff@clinic.com",
            subject=outbox.BOOKING_SUBJECT
            if kind == outbox.KIND_BOOKING
            else outbox.CALLBACK_SUBJECT,
            client_name="Clarity Dental",
            callback_source=source if kind == outbox.KIND_CALLBACK else None,
            attempts=attempts,
        )
        return row_id

    async def claim_due(self, *, now, lease_until, limit):
        due = [
            row
            for row in self.rows.values()
            if row.status == outbox.STATUS_QUEUED and row.next_attempt_at <= now
        ]
        due.sort(key=lambda row: row.next_attempt_at)
        claimed = []
        for row in due[:limit]:
            row.attempts += 1
            row.next_attempt_at = lease_until
            claimed.append(
                outbox.DueNotification(
                    id=row.id,
                    client_id=row.client_id,
                    kind=row.kind,
                    source_id=row.source_id,
                    recipient=row.recipient,
                    subject=row.subject,
                    attempts=row.attempts,
                    client_name=row.client_name,
                    callback_source=row.callback_source,
                )
            )
        return claimed

    async def mark_sent(self, row_id, *, ses_message_id, now):
        row = self.rows[row_id]
        row.status, row.sent_at, row.ses_message_id, row.last_error = (
            outbox.STATUS_SENT,
            now,
            ses_message_id,
            None,
        )

    async def mark_retry(self, row_id, *, next_attempt_at, error_code):
        row = self.rows[row_id]
        row.next_attempt_at, row.last_error = next_attempt_at, error_code

    async def mark_failed(self, row_id, *, error_code):
        row = self.rows[row_id]
        row.status, row.last_error = outbox.STATUS_FAILED, error_code


class RecordingSender:
    """Each send pops one outcome: None (success), or an exception to raise."""

    def __init__(self, outcomes=None) -> None:
        self.outcomes = list(outcomes or [])
        self.calls: list[tuple] = []
        self.thread_ids: list[int] = []

    def send_email(self, to_email, subject, html_content, text_content):
        self.calls.append((to_email, subject))
        self.thread_ids.append(threading.get_ident())
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, BaseException):
            raise outcome
        return "ses-msg-1"


@pytest.fixture
def fake_outbox(monkeypatch):
    fake = FakeOutbox()
    monkeypatch.setattr(outbox, "claim_due", fake.claim_due)
    monkeypatch.setattr(outbox, "mark_sent", fake.mark_sent)
    monkeypatch.setattr(outbox, "mark_retry", fake.mark_retry)
    monkeypatch.setattr(outbox, "mark_failed", fake.mark_failed)
    monkeypatch.setattr(dispatcher, "utc_now", lambda: T0)
    return fake


RETRYABLE = EmailDeliveryError("SES_THROTTLED", retryable=True)
PERMANENT = EmailDeliveryError("SES_REJECTED")


# ---------------------------------------------------------------- dispatcher


async def test_sent_row_records_the_provider_message_id(fake_outbox):
    row_id = fake_outbox.add(kind=outbox.KIND_BOOKING)
    sender = RecordingSender()
    assert await dispatcher.dispatch_once(sender) == 1
    row = fake_outbox.rows[row_id]
    assert row.status == outbox.STATUS_SENT
    assert row.ses_message_id == "ses-msg-1"
    assert row.sent_at == T0
    assert sender.calls == [("staff@clinic.com", BOOKING_SUBJECT)]


async def test_send_runs_in_a_worker_thread_and_does_not_block_the_loop(fake_outbox):
    fake_outbox.add()
    loop_thread = threading.get_ident()

    class SlowSender(RecordingSender):
        def send_email(self, *args):
            import time

            time.sleep(0.3)
            return super().send_email(*args)

    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    task = asyncio.create_task(ticker())
    sender = SlowSender()
    await dispatcher.dispatch_once(sender)
    task.cancel()
    assert ticks > 5
    assert sender.thread_ids[0] != loop_thread


async def test_retryable_failure_waits_one_minute_then_retries(fake_outbox):
    row_id = fake_outbox.add()
    await dispatcher.dispatch_once(RecordingSender([RETRYABLE]))
    row = fake_outbox.rows[row_id]
    assert row.status == outbox.STATUS_QUEUED
    assert row.next_attempt_at == T0 + timedelta(minutes=1)
    assert row.last_error == "SES_THROTTLED"


async def test_backoff_doubles_across_attempts_and_stops_at_five(fake_outbox, monkeypatch):
    row_id = fake_outbox.add()
    sender = RecordingSender([RETRYABLE] * 5)
    clock = T0
    waits = []
    for _ in range(5):
        monkeypatch.setattr(dispatcher, "utc_now", lambda c=clock: c)
        await dispatcher.dispatch_once(sender)
        row = fake_outbox.rows[row_id]
        if row.status == outbox.STATUS_FAILED:
            break
        waits.append(row.next_attempt_at - clock)
        clock = row.next_attempt_at

    assert waits == [
        timedelta(minutes=1),
        timedelta(minutes=2),
        timedelta(minutes=4),
        timedelta(minutes=8),
    ]
    assert len(sender.calls) == 5
    assert fake_outbox.rows[row_id].status == outbox.STATUS_FAILED
    assert fake_outbox.rows[row_id].last_error == "SES_THROTTLED"


async def test_permanent_failure_is_not_retried(fake_outbox):
    row_id = fake_outbox.add()
    sender = RecordingSender([PERMANENT])
    await dispatcher.dispatch_once(sender)
    row = fake_outbox.rows[row_id]
    assert row.status == outbox.STATUS_FAILED
    assert row.last_error == "SES_REJECTED"
    assert row.attempts == 1
    assert len(sender.calls) == 1


async def test_unexpected_crash_is_retried_and_logs_only_the_type(fake_outbox, caplog):
    row_id = fake_outbox.add()
    with caplog.at_level(logging.WARNING):
        await dispatcher.dispatch_once(RecordingSender([RuntimeError(f"leak {PHI_EMAIL}")]))
    row = fake_outbox.rows[row_id]
    assert row.status == outbox.STATUS_QUEUED
    assert row.last_error == "SEND_CRASHED"
    assert PHI_EMAIL not in caplog.text
    assert "RuntimeError" in caplog.text


async def test_production_without_ses_fails_the_row_instead_of_marking_it_sent(fake_outbox):
    row_id = fake_outbox.add()
    await dispatcher.dispatch_once(ConsoleEmailSender(app_env="production"))
    row = fake_outbox.rows[row_id]
    assert row.status == outbox.STATUS_FAILED
    assert row.last_error == "SES_NOT_CONFIGURED"
    assert row.sent_at is None


async def test_a_row_is_claimed_once_during_its_lease(fake_outbox):
    row_id = fake_outbox.add()
    sender = RecordingSender([RETRYABLE])
    await dispatcher.dispatch_once(sender)
    # Still inside the retry wait and the lease: the next pass finds nothing to send.
    assert await dispatcher.dispatch_once(sender) == 0
    assert fake_outbox.rows[row_id].attempts == 1


async def test_empty_queue_sends_nothing(fake_outbox):
    sender = RecordingSender()
    assert await dispatcher.dispatch_once(sender) == 0
    assert sender.calls == []


def test_enqueue_copies_only_enabled_recipients_and_ignores_repeats():
    sql = " ".join(outbox._ENQUEUE_SQL.split())
    assert "WHERE client_id = %s AND enabled" in sql
    assert "ON CONFLICT (kind, source_id, recipient) DO NOTHING" in sql


# ---------------------------------------------------------------- enqueue hooks


class FakeCursor:
    def __init__(self, row) -> None:
        self.row = row
        self.executed: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.executed.append((sql, params))

    async def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, cursor) -> None:
        self._cursor = cursor
        self.committed = False

    def cursor(self):
        return self._cursor

    async def commit(self):
        self.committed = True


def _fake_db(monkeypatch, module, row):
    cursor = FakeCursor(row)
    connection = FakeConnection(cursor)

    class Ctx:
        async def __aenter__(self):
            return connection

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(module, "get_db_connection", lambda: Ctx())
    return cursor, connection


async def test_new_callback_queues_its_email_in_the_same_transaction(monkeypatch):
    callback_id = uuid4()
    row = (callback_id, CLINIC_ID, None, "open", SOURCE_TIMEOUT, T0, None, None)
    cursor, connection = _fake_db(monkeypatch, callback_model, row)

    created = await callback_model.ensure_open_callback(
        client_id=CLINIC_ID, session_id=None, source=SOURCE_TIMEOUT, now=T0
    )

    assert created is not None and created.id == callback_id
    enqueue = [params for sql, params in cursor.executed if "notification_outbox" in sql]
    assert enqueue == [(outbox.KIND_CALLBACK, callback_id, CALLBACK_SUBJECT, T0, CLINIC_ID)]
    assert connection.committed


async def test_callback_that_already_exists_queues_nothing(monkeypatch):
    cursor, _ = _fake_db(monkeypatch, callback_model, None)
    created = await callback_model.ensure_open_callback(
        client_id=CLINIC_ID, session_id=uuid4(), source=SOURCE_NO_STAFF, now=T0
    )
    assert created is None
    assert not [sql for sql, _ in cursor.executed if "notification_outbox" in sql]


async def test_booking_queues_its_email_after_confirmation(monkeypatch):
    record = _booking_record()
    monkeypatch.setattr(booking_service, "_create_booking", AsyncMock(return_value=record))
    queued = AsyncMock()
    monkeypatch.setattr(booking_service, "enqueue_booking", queued)

    result = await booking_service.create_booking(
        client_id=CLINIC_ID,
        name=PHI_NAME,
        phone=PHI_PHONE,
        email=PHI_EMAIL,
        slot_start=T0,
    )

    assert result is record
    queued.assert_awaited_once_with(CLINIC_ID, record.id)


async def test_a_failed_booking_notification_does_not_undo_the_booking(monkeypatch, caplog):
    record = _booking_record()
    monkeypatch.setattr(booking_service, "_create_booking", AsyncMock(return_value=record))
    monkeypatch.setattr(
        booking_service, "enqueue_booking", AsyncMock(side_effect=ConnectionError(PHI_EMAIL))
    )
    with caplog.at_level(logging.ERROR):
        result = await booking_service.create_booking(
            client_id=CLINIC_ID, name=PHI_NAME, phone=PHI_PHONE, email=PHI_EMAIL, slot_start=T0
        )
    assert result is record
    assert PHI_EMAIL not in caplog.text


def _booking_record():
    from app.db.models.booking import BookingRecord

    return BookingRecord(
        id=uuid4(),
        name=PHI_NAME,
        phone=PHI_PHONE,
        email=PHI_EMAIL,
        slot_start=T0,
        slot_end=T0 + timedelta(minutes=30),
        google_event_id=None,
        thread_id=None,
        status="confirmed",
        created_at=T0,
        client_id=CLINIC_ID,
    )


# ---------------------------------------------------------------- recipient routes


class RecipientStore:
    def __init__(self) -> None:
        self.rows: dict = {}

    def _for(self, client_id):
        return [r for r in self.rows.values() if r.client_id == client_id]

    async def list(self, client_id):
        return sorted(self._for(client_id), key=lambda r: r.created_at)

    async def add(self, client_id, email, *, actor_id, now):
        if len(self._for(client_id)) >= MAX_RECIPIENTS:
            raise RecipientLimitReached
        if any(r.email.lower() == email.lower() for r in self._for(client_id)):
            raise DuplicateRecipient
        item = NotificationRecipient(
            id=uuid4(), client_id=client_id, email=email, enabled=True, created_at=now
        )
        self.rows[item.id] = item
        return item

    async def set_enabled(self, client_id, recipient_id, enabled):
        item = self.rows.get(recipient_id)
        if item is None or item.client_id != client_id:
            return None
        self.rows[recipient_id] = dataclasses.replace(item, enabled=enabled)
        return self.rows[recipient_id]

    async def delete(self, client_id, recipient_id):
        item = self.rows.get(recipient_id)
        if item is None or item.client_id != client_id:
            return False
        del self.rows[recipient_id]
        return True


@pytest.fixture
def recipient_app(monkeypatch):
    store = RecipientStore()
    audit = AsyncMock()

    async def get_client(client_id):
        return make_client_record(client_id=client_id)

    monkeypatch.setattr("app.services.client_scope.get_client", get_client)
    module = "app.api.routes.admin.notification_recipients."
    monkeypatch.setattr(module + "list_recipients", store.list)
    monkeypatch.setattr(module + "add_recipient", store.add)
    monkeypatch.setattr(module + "set_recipient_enabled", store.set_enabled)
    monkeypatch.setattr(module + "delete_recipient", store.delete)
    monkeypatch.setattr(recipient_routes, "log_admin_action", audit)
    app = FastAPI()
    app.include_router(recipient_routes.router, prefix="/api/v1")
    return app, store, audit


BASE = "/api/v1/admin/settings/notification-recipients"


async def test_admin_adds_lists_toggles_and_removes_a_recipient(recipient_app):
    app, store, audit = recipient_app
    with signed_in(app, UserType.admin):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
            added = await http.post(BASE, json={"email": "front@clinic.com"})
            assert added.status_code == 201
            item_id = added.json()["id"]
            listed = await http.get(BASE)
            assert listed.json()["recipients"][0]["email"] == "front@clinic.com"
            assert listed.json()["limit"] == MAX_RECIPIENTS
            toggled = await http.patch(f"{BASE}/{item_id}", json={"enabled": False})
            assert toggled.json()["enabled"] is False
            removed = await http.delete(f"{BASE}/{item_id}")
            assert removed.status_code == 204
            missing = await http.delete(f"{BASE}/{item_id}")
            assert missing.status_code == 404
    actions = [call.args[0] for call in audit.await_args_list]
    assert actions == [
        "settings.notification_recipient_add",
        "settings.notification_recipient_update",
        "settings.notification_recipient_delete",
    ]
    # Audit rows name the recipient by ID, never by address.
    assert all("front@clinic.com" not in str(call) for call in audit.await_args_list)


async def test_sixth_recipient_is_refused(recipient_app):
    app, store, _ = recipient_app
    with signed_in(app, UserType.admin) as user:
        for i in range(MAX_RECIPIENTS):
            await store.add(user.client_id, f"staff{i}@clinic.com", actor_id=None, now=T0)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
            response = await http.post(BASE, json={"email": "sixth@clinic.com"})
    assert response.status_code == 409
    assert "up to 5" in response.json()["detail"]


async def test_duplicate_address_is_refused(recipient_app):
    app, _, _ = recipient_app
    with signed_in(app, UserType.admin):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
            await http.post(BASE, json={"email": "front@clinic.com"})
            again = await http.post(BASE, json={"email": "FRONT@clinic.com"})
    assert again.status_code == 409


async def test_invalid_address_is_rejected(recipient_app):
    app, _, _ = recipient_app
    with signed_in(app, UserType.admin):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
            response = await http.post(BASE, json={"email": "not-an-address"})
    assert response.status_code == 422


async def test_support_cannot_manage_recipients(recipient_app):
    app, _, _ = recipient_app
    with signed_in(app, UserType.support):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
            response = await http.get(BASE)
    assert response.status_code == 403
