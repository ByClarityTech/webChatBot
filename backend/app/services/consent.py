"""Consent for sharing contact details in chat: offer the approved wording, record acceptance.

Wording is stored as versioned rows (db/models/consent.py). A visitor is only ever offered an
approved version, and each acceptance keeps the exact rendered text with its SHA-256, so a
later edit cannot change what was agreed to.
"""

import hashlib
from dataclasses import dataclass
from uuid import UUID

from app.db.models.chat_session import (
    ChatSession,
    get_session,
    mark_session_known,
    upsert_session_contact,
)
from app.db.models.consent import (
    SHARE_CONTACT,
    ConsentText,
    approve_consent_text,
    get_consent_text,
    get_latest_approved_text,
    insert_consent_record,
)
from app.services.chat_sessions import is_idle, utc_now
from app.services.contact_privacy import encrypt_phone, normalize_us_phone, phone_hash

PRACTICE_PLACEHOLDER = "[Practice Name]"
MAX_NAME_LENGTH = 255

SESSION_ENDED_MESSAGE = "This conversation has ended. Start a new one to continue."
NO_APPROVED_WORDING_MESSAGE = "Sharing details is not available right now. You can keep chatting."


class ConsentError(Exception):
    """Base class; the route maps each subclass to one HTTP status."""


class ConsentWordingUnavailableError(ConsentError):
    """No approved wording for the version asked for."""


class ConsentSessionEndedError(ConsentError):
    """The session is closed, idle, or does not belong to this thread and client."""


class InvalidContactError(ConsentError):
    """A name or phone that cannot be stored."""


@dataclass(frozen=True)
class OfferedConsent:
    purpose: str
    version: int
    text: str


def render(text: ConsentText, practice_name: str) -> str:
    return text.body.replace(PRACTICE_PLACEHOLDER, practice_name)


def sha256_of(rendered: str) -> str:
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


async def offered_wording(
    practice_name: str, purpose: str = SHARE_CONTACT
) -> OfferedConsent | None:
    """The latest approved wording for this purpose, with the practice filled in, or None."""
    latest = await get_latest_approved_text(purpose)
    if latest is None:
        return None
    return OfferedConsent(latest.purpose, latest.version, render(latest, practice_name))


async def approve(purpose: str, version: int) -> ConsentText | None:
    return await approve_consent_text(purpose, version)


async def session_for_consent(session_id: UUID, thread_id: UUID, client_id: UUID) -> ChatSession:
    """The active session that the consent is about. Anything else is refused."""
    session = await get_session(session_id)
    if session is None or session.thread_id != thread_id or session.client_id != client_id:
        raise ConsentSessionEndedError(SESSION_ENDED_MESSAGE)
    if session.status != "active" or is_idle(session, utc_now()):
        raise ConsentSessionEndedError(SESSION_ENDED_MESSAGE)
    return session


def _clean_name(raw: str) -> str:
    name = " ".join((raw or "").split())
    if not name or len(name) > MAX_NAME_LENGTH:
        raise InvalidContactError("Enter your name")
    return name


async def record_share_consent(
    *,
    session: ChatSession,
    practice_name: str,
    version: int,
    full_name: str,
    phone: str,
    is_current_patient: bool,
    purpose: str = SHARE_CONTACT,
) -> None:
    """Stores the consent record and the contact, and makes the session known.

    Raises ConsentWordingUnavailableError, InvalidContactError, or ContactStorageNotConfigured
    (from contact_privacy) before anything is written.
    """
    wording = await get_consent_text(purpose, version)
    if wording is None or not wording.approved:
        raise ConsentWordingUnavailableError(NO_APPROVED_WORDING_MESSAGE)

    name = _clean_name(full_name)
    try:
        e164 = normalize_us_phone(phone)
    except ValueError as exc:
        raise InvalidContactError(str(exc)) from exc
    encrypted = encrypt_phone(e164)  # raises ContactStorageNotConfigured when unset
    lookup = phone_hash(e164)

    rendered = render(wording, practice_name)
    await insert_consent_record(
        session_id=session.session_id,
        purpose=purpose,
        version=version,
        text_snapshot=rendered,
        text_sha256=sha256_of(rendered),
    )
    await upsert_session_contact(
        session_id=session.session_id,
        full_name=name,
        phone_encrypted=encrypted,
        phone_hash=lookup,
        is_current_patient=is_current_patient,
    )
    await mark_session_known(session.session_id, version)
