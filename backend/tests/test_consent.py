"""Consent for sharing details: wording approval, snapshots, refusal, contacts, and routes.

Database calls are replaced by mocks. Encryption uses real Fernet keys set per test.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from auth_helpers import make_client_record, signed_in
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import booking, chat_session
from app.api.routes.admin import consent as admin_consent
from app.core.config import get_settings
from app.core.roles import UserType
from app.db.models.booking import BookingRecord
from app.db.models.chat_session import ChatSession
from app.db.models.consent import ConsentText
from app.services import consent as consent_service
from app.services.chat_sessions import SessionHandle
from app.services.consent import (
    ConsentSessionEndedError,
    ConsentWordingUnavailableError,
    InvalidContactError,
    record_share_consent,
    render,
    session_for_consent,
    sha256_of,
)
from app.services.contact_privacy import (
    ContactStorageNotConfigured,
    InvalidPhoneError,
    decrypt_phone,
    encrypt_phone,
    normalize_us_phone,
    phone_hash,
)

T0 = datetime(2030, 7, 1, 10, 0, tzinfo=UTC)
PRACTICE = "Example Clinic"
WORDING = "I agree that [Practice Name] may call or text me about my request."
RENDERED = "I agree that Example Clinic may call or text me about my request."


@pytest.fixture
def keys(monkeypatch):
    """Real keys for this test only; the settings cache is rebuilt around it."""
    fernet_key = Fernet.generate_key().decode()
    monkeypatch.setenv("PHONE_ENCRYPTION_KEY", fernet_key)
    monkeypatch.setenv("PHONE_HASH_KEY", "h" * 48)
    get_settings.cache_clear()
    yield fernet_key
    get_settings.cache_clear()


def _text(version=1, approved=True) -> ConsentText:
    return ConsentText(
        purpose="share_contact",
        version=version,
        body=WORDING,
        approved_at=T0 if approved else None,
        created_at=T0,
    )


def _session(**changes) -> ChatSession:
    base = dict(
        session_id=uuid4(),
        thread_id=uuid4(),
        client_id=uuid4(),
        type="anonymous",
        status="active",
        end_reason=None,
        consent_given=False,
        consent_text_version=None,
        is_test_session=False,
        started_at=T0,
        last_activity_at=T0,
        closed_at=None,
    )
    base.update(changes)
    return ChatSession(**base)


@pytest.fixture
def writes(monkeypatch):
    """The four database writes made when consent is recorded, as mocks."""
    mocks = {
        "get_consent_text": AsyncMock(return_value=_text()),
        "insert_consent_record": AsyncMock(),
        "upsert_session_contact": AsyncMock(),
        "mark_session_known": AsyncMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(f"app.services.consent.{name}", mock)
    return mocks


# ---------------------------------------------------------------- phone numbers


@pytest.mark.parametrize(
    "raw",
    ["(312) 555-0187", "1-312-555-0187", "+1 312 555 0187", "3125550187", " 312.555.0187 "],
)
def test_us_numbers_normalize_to_one_form(raw):
    assert normalize_us_phone(raw) == "+13125550187"


@pytest.mark.parametrize(
    "raw",
    ["555-0187", "0125550187", "3120550187", "+44 20 7946 0958", "", "abcdefg"],
)
def test_invalid_numbers_are_refused(raw):
    with pytest.raises(InvalidPhoneError):
        normalize_us_phone(raw)


# ---------------------------------------------------------------- encryption and hashing


def test_phone_round_trips_and_is_not_stored_in_clear(keys):
    token = encrypt_phone("+13125550187")
    assert b"3125550187" not in token
    assert decrypt_phone(token) == "+13125550187"


def test_encryption_is_randomised_but_the_lookup_hash_is_stable(keys):
    assert encrypt_phone("+13125550187") != encrypt_phone("+13125550187")
    assert phone_hash("+13125550187") == phone_hash("+13125550187")
    assert len(phone_hash("+13125550187")) == 64
    assert phone_hash("+13125550187") != phone_hash("+13125550188")


def test_the_hash_depends_on_the_key(monkeypatch, keys):
    first = phone_hash("+13125550187")
    monkeypatch.setenv("PHONE_HASH_KEY", "z" * 48)
    get_settings.cache_clear()
    assert phone_hash("+13125550187") != first


def test_missing_or_bad_keys_refuse_to_store_a_contact(monkeypatch):
    monkeypatch.setenv("PHONE_ENCRYPTION_KEY", "")
    monkeypatch.setenv("PHONE_HASH_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(ContactStorageNotConfigured):
        encrypt_phone("+13125550187")
    with pytest.raises(ContactStorageNotConfigured):
        phone_hash("+13125550187")

    monkeypatch.setenv("PHONE_ENCRYPTION_KEY", "not-a-fernet-key")
    get_settings.cache_clear()
    with pytest.raises(ContactStorageNotConfigured):
        encrypt_phone("+13125550187")
    get_settings.cache_clear()


def test_a_key_change_makes_old_phones_unreadable(monkeypatch, keys):
    token = encrypt_phone("+13125550187")
    monkeypatch.setenv("PHONE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    with pytest.raises(ContactStorageNotConfigured):
        decrypt_phone(token)


# ---------------------------------------------------------------- wording and snapshots


def test_placeholder_is_replaced_and_the_snapshot_hash_matches_the_text():
    assert render(_text(), PRACTICE) == RENDERED
    assert sha256_of(RENDERED) == hashlib.sha256(RENDERED.encode("utf-8")).hexdigest()
    assert sha256_of("abc").startswith("ba7816bf")  # standard SHA-256 test vector


async def test_no_approved_wording_means_nothing_is_offered(monkeypatch):
    monkeypatch.setattr(
        "app.services.consent.get_latest_approved_text", AsyncMock(return_value=None)
    )
    assert await consent_service.offered_wording(PRACTICE) is None


async def test_the_latest_approved_version_is_offered(monkeypatch):
    monkeypatch.setattr(
        "app.services.consent.get_latest_approved_text",
        AsyncMock(return_value=_text(version=3)),
    )
    offer = await consent_service.offered_wording(PRACTICE)
    assert (offer.version, offer.text) == (3, RENDERED)


# ---------------------------------------------------------------- recording consent


async def test_accepting_writes_the_snapshot_contact_and_known_status(keys, writes):
    session = _session()
    await record_share_consent(
        session=session,
        practice_name=PRACTICE,
        version=1,
        full_name="  Jane   Doe ",
        phone="(312) 555-0187",
        is_current_patient=True,
    )

    record = writes["insert_consent_record"].await_args.kwargs
    assert record["text_snapshot"] == RENDERED
    assert record["text_sha256"] == sha256_of(RENDERED)
    assert record["version"] == 1 and record["purpose"] == "share_contact"

    contact = writes["upsert_session_contact"].await_args.kwargs
    assert contact["full_name"] == "Jane Doe"
    assert contact["phone_hash"] == phone_hash("+13125550187")
    assert b"3125550187" not in contact["phone_encrypted"]
    assert decrypt_phone(contact["phone_encrypted"]) == "+13125550187"
    assert contact["is_current_patient"] is True

    writes["mark_session_known"].assert_awaited_once_with(session.session_id, 1)


async def test_an_unapproved_version_writes_nothing(keys, writes):
    writes["get_consent_text"].return_value = _text(approved=False)
    with pytest.raises(ConsentWordingUnavailableError):
        await record_share_consent(
            session=_session(),
            practice_name=PRACTICE,
            version=1,
            full_name="Jane",
            phone="3125550187",
            is_current_patient=False,
        )
    writes["insert_consent_record"].assert_not_awaited()
    writes["upsert_session_contact"].assert_not_awaited()
    writes["mark_session_known"].assert_not_awaited()


async def test_a_bad_phone_or_name_writes_nothing(keys, writes):
    with pytest.raises(InvalidContactError):
        await record_share_consent(
            session=_session(),
            practice_name=PRACTICE,
            version=1,
            full_name="Jane",
            phone="555-0187",
            is_current_patient=False,
        )
    with pytest.raises(InvalidContactError):
        await record_share_consent(
            session=_session(),
            practice_name=PRACTICE,
            version=1,
            full_name="   ",
            phone="3125550187",
            is_current_patient=False,
        )
    writes["insert_consent_record"].assert_not_awaited()


async def test_missing_keys_stop_before_any_write(monkeypatch, writes):
    monkeypatch.setenv("PHONE_ENCRYPTION_KEY", "")
    monkeypatch.setenv("PHONE_HASH_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(ContactStorageNotConfigured):
        await record_share_consent(
            session=_session(),
            practice_name=PRACTICE,
            version=1,
            full_name="Jane",
            phone="3125550187",
            is_current_patient=False,
        )
    writes["insert_consent_record"].assert_not_awaited()
    writes["upsert_session_contact"].assert_not_awaited()
    get_settings.cache_clear()


# ---------------------------------------------------------------- session checks


async def test_consent_is_refused_for_another_thread_or_client(monkeypatch):
    session = _session()

    async def lookup(session_id):
        return session if session_id == session.session_id else None

    monkeypatch.setattr("app.services.consent.get_session", lookup)
    with pytest.raises(ConsentSessionEndedError):
        await session_for_consent(session.session_id, uuid4(), session.client_id)
    with pytest.raises(ConsentSessionEndedError):
        await session_for_consent(session.session_id, session.thread_id, uuid4())
    with pytest.raises(ConsentSessionEndedError):
        await session_for_consent(uuid4(), session.thread_id, session.client_id)


async def test_consent_is_refused_for_an_idle_or_ended_session(monkeypatch):
    idle = _session(last_activity_at=datetime.now(UTC) - timedelta(minutes=45))
    ended = _session(status="ended", end_reason="closed", closed_at=datetime.now(UTC))
    for session in (idle, ended):
        monkeypatch.setattr("app.services.consent.get_session", AsyncMock(return_value=session))
        with pytest.raises(ConsentSessionEndedError):
            await session_for_consent(session.session_id, session.thread_id, session.client_id)


# ---------------------------------------------------------------- routes: visitor side


@pytest.fixture
def visitor_app(monkeypatch):
    client = make_client_record(slug="clinic", client_id=uuid4())
    client.name = PRACTICE
    monkeypatch.setattr(
        "app.services.public_client.get_client_by_slug",
        AsyncMock(return_value=client),
    )
    app = FastAPI()
    app.include_router(chat_session.router, prefix="/api/v1")
    app.state.client = client
    return app


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_offer_is_404_until_wording_is_approved(visitor_app, monkeypatch):
    monkeypatch.setattr(
        "app.services.consent.get_latest_approved_text", AsyncMock(return_value=None)
    )
    async with _client(visitor_app) as client:
        response = await client.get("/api/v1/chat/consent-text", params={"client": "clinic"})
    assert response.status_code == 404


async def test_offer_shows_the_approved_wording_with_the_practice_name(visitor_app, monkeypatch):
    monkeypatch.setattr(
        "app.services.consent.get_latest_approved_text", AsyncMock(return_value=_text())
    )
    async with _client(visitor_app) as client:
        response = await client.get("/api/v1/chat/consent-text", params={"client": "clinic"})
    assert response.json() == {"purpose": "share_contact", "version": 1, "text": RENDERED}


async def test_refusal_is_400_and_records_nothing(visitor_app, writes):
    session = _session()
    async with _client(visitor_app) as client:
        response = await client.post(
            "/api/v1/chat/session/consent",
            json={
                "client": "clinic",
                "thread_id": str(session.thread_id),
                "session_id": str(session.session_id),
                "text_version": 1,
                "full_name": "Jane",
                "phone": "3125550187",
                "is_current_patient": True,
                "consent_accepted": False,
            },
        )
    assert response.status_code == 400
    writes["insert_consent_record"].assert_not_awaited()
    writes["mark_session_known"].assert_not_awaited()


async def test_accepting_returns_known_and_stores_the_record(
    visitor_app, writes, keys, monkeypatch
):
    session = _session(client_id=visitor_app.state.client.id)
    monkeypatch.setattr("app.services.consent.get_session", AsyncMock(return_value=session))
    async with _client(visitor_app) as client:
        response = await client.post(
            "/api/v1/chat/session/consent",
            json={
                "client": "clinic",
                "thread_id": str(session.thread_id),
                "session_id": str(session.session_id),
                "text_version": 1,
                "full_name": "Jane",
                "phone": "3125550187",
                "is_current_patient": False,
                "consent_accepted": True,
            },
        )
    assert response.status_code == 200
    assert response.json() == {"known": True, "session_id": str(session.session_id)}
    writes["insert_consent_record"].assert_awaited_once()


async def test_consent_for_an_ended_session_is_409(visitor_app, writes, monkeypatch):
    session = _session(
        client_id=visitor_app.state.client.id, status="ended", end_reason="closed", closed_at=T0
    )
    monkeypatch.setattr("app.services.consent.get_session", AsyncMock(return_value=session))
    async with _client(visitor_app) as client:
        response = await client.post(
            "/api/v1/chat/session/consent",
            json={
                "client": "clinic",
                "thread_id": str(session.thread_id),
                "session_id": str(session.session_id),
                "text_version": 1,
                "full_name": "Jane",
                "phone": "3125550187",
                "is_current_patient": False,
                "consent_accepted": True,
            },
        )
    assert response.status_code == 409
    writes["insert_consent_record"].assert_not_awaited()


async def test_start_status_and_close_routes(visitor_app, monkeypatch):
    thread_id = uuid4()
    session_id = uuid4()
    monkeypatch.setattr(
        "app.api.routes.chat_session.start_new_session",
        AsyncMock(return_value=SessionHandle(thread_id, session_id, False, False)),
    )
    async with _client(visitor_app) as client:
        started = await client.post("/api/v1/chat/session/start", json={"client": "clinic"})
    assert started.json() == {"thread_id": str(thread_id), "session_id": str(session_id)}

    monkeypatch.setattr(
        "app.api.routes.chat_session.active_session_for",
        AsyncMock(return_value=_session(session_id=session_id, thread_id=thread_id)),
    )
    async with _client(visitor_app) as client:
        status_response = await client.get(
            "/api/v1/chat/session", params={"client": "clinic", "thread_id": str(thread_id)}
        )
    assert status_response.json() == {
        "active": True,
        "session_id": str(session_id),
        "known": False,
        "current_patient": None,
    }

    monkeypatch.setattr("app.api.routes.chat_session.close_session", AsyncMock(return_value=True))
    async with _client(visitor_app) as client:
        closed = await client.post(
            "/api/v1/chat/session/close", json={"client": "clinic", "thread_id": str(thread_id)}
        )
    assert closed.json() == {"ended": True}


# ---------------------------------------------------------------- routes: Super Admin approval


@pytest.fixture
def admin_app():
    app = FastAPI()
    app.include_router(admin_consent.router, prefix="/api/v1")
    return app


async def test_super_admin_approves_a_draft_version(admin_app, audit_rows, monkeypatch):
    approved = _text()
    monkeypatch.setattr(
        "app.services.consent.approve_consent_text", AsyncMock(return_value=approved)
    )
    with signed_in(admin_app, UserType.super_admin):
        async with _client(admin_app) as client:
            response = await client.post("/api/v1/admin/consent-texts/share_contact/1/approve")
    assert response.status_code == 200
    assert response.json()["approved_at"] is not None
    assert [row["action"] for row in audit_rows] == ["consent_text.approve"]
    assert audit_rows[0]["resource_id"] == "share_contact:1"


async def test_admin_cannot_approve_wording(admin_app, monkeypatch):
    approve = AsyncMock()
    monkeypatch.setattr("app.services.consent.approve_consent_text", approve)
    with signed_in(admin_app, UserType.admin):
        async with _client(admin_app) as client:
            response = await client.post("/api/v1/admin/consent-texts/share_contact/1/approve")
    assert response.status_code == 403
    approve.assert_not_awaited()


async def test_approving_an_unknown_version_is_404(admin_app, monkeypatch):
    monkeypatch.setattr("app.services.consent.approve_consent_text", AsyncMock(return_value=None))
    with signed_in(admin_app, UserType.super_admin):
        async with _client(admin_app) as client:
            response = await client.post("/api/v1/admin/consent-texts/share_contact/9/approve")
    assert response.status_code == 404


# ---------------------------------------------------------------- booking: consent in the card


BOOKING_CLINIC = make_client_record("clinic")
BOOKING_BODY = {
    "client": "clinic",
    "name": "Jane Doe",
    "phone": "3125550187",
    "email": "jane@example.com",
    "slot_start": "2030-07-02T10:00:00+05:30",
    "is_current_patient": False,
}


@pytest.fixture
def booking_app(monkeypatch):
    BOOKING_CLINIC.name = PRACTICE
    session = _session(client_id=BOOKING_CLINIC.id)
    state = {"session": session}

    monkeypatch.setattr(
        "app.services.public_client.get_client_by_slug", AsyncMock(return_value=BOOKING_CLINIC)
    )
    monkeypatch.setattr(
        "app.api.routes.booking.get_chat_thread_client", AsyncMock(return_value=BOOKING_CLINIC.id)
    )

    async def active_session_for(thread_id, client_id):
        return state["session"]

    monkeypatch.setattr("app.api.routes.booking.active_session_for", active_session_for)
    app = FastAPI()
    app.include_router(booking.router, prefix="/api/v1")
    app.state.booking = state
    return app


def _booked(**_):
    now = datetime(2030, 7, 2, 10, 0)
    return BookingRecord(
        id=uuid4(),
        name="Jane Doe",
        phone="+13125550187",
        email="jane@example.com",
        slot_start=now,
        slot_end=now + timedelta(minutes=30),
        google_event_id="evt_1",
        thread_id=None,
        status="confirmed",
        created_at=now,
        client_id=BOOKING_CLINIC.id,
    )


def _booking_payload(**changes):
    thread = uuid4()
    return {**BOOKING_BODY, "thread_id": str(thread), **changes}


async def _book(app, **changes):
    async with _client(app) as client:
        return await client.post("/api/v1/booking", json=_booking_payload(**changes))


async def test_anonymous_booking_without_consent_is_refused(booking_app, monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr("app.api.routes.booking.create_booking", create)
    response = await _book(booking_app)
    assert response.status_code == 400
    assert "Tick the box" in response.json()["detail"]
    create.assert_not_awaited()


async def test_booking_without_a_conversation_is_refused(booking_app):
    async with _client(booking_app) as client:
        response = await client.post("/api/v1/booking", json=BOOKING_BODY)
    assert response.status_code == 400


async def test_booking_in_an_ended_conversation_is_409(booking_app, monkeypatch):
    async def none(thread_id, client_id):
        return None

    monkeypatch.setattr("app.api.routes.booking.active_session_for", none)
    response = await _book(booking_app)
    assert response.status_code == 409


async def test_anonymous_booking_with_consent_records_it_then_books(booking_app, monkeypatch):
    record = AsyncMock()
    create = AsyncMock(side_effect=_booked)
    monkeypatch.setattr("app.api.routes.booking.record_share_consent", record)
    monkeypatch.setattr("app.api.routes.booking.create_booking", create)
    response = await _book(
        booking_app,
        consent_accepted=True,
        consent_text_version=1,
        is_current_patient=True,
    )
    record.assert_awaited_once()
    assert record.await_args.kwargs["version"] == 1
    assert record.await_args.kwargs["is_current_patient"] is True
    assert response.status_code == 201


async def test_a_known_visitor_books_without_asking_again(booking_app, monkeypatch):
    booking_app.state.booking["session"] = _session(
        client_id=BOOKING_CLINIC.id, type="known", consent_given=True, consent_text_version=1
    )
    record = AsyncMock()
    monkeypatch.setattr("app.api.routes.booking.record_share_consent", record)
    monkeypatch.setattr("app.api.routes.booking.create_booking", AsyncMock(side_effect=_booked))
    response = await _book(booking_app)
    assert response.status_code == 201
    record.assert_not_awaited()


async def test_booking_with_a_bad_phone_is_400_before_booking(booking_app, monkeypatch, keys):
    create = AsyncMock()
    monkeypatch.setattr("app.api.routes.booking.create_booking", create)
    monkeypatch.setattr(
        "app.api.routes.booking.record_share_consent",
        AsyncMock(side_effect=InvalidContactError("Enter a 10-digit US phone number")),
    )
    response = await _book(
        booking_app,
        phone="555-0187",
        consent_accepted=True,
        consent_text_version=1,
        is_current_patient=False,
    )
    assert response.status_code == 400
    create.assert_not_awaited()
