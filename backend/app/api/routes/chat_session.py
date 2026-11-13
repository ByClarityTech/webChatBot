"""Visitor side of a conversation session: start, status, consent, and close.

All routes take the client slug and check the request origin like the chat itself. Each
one refuses a thread or session that does not belong to that client.
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.api.deps.auth import optional_admin_user
from app.core.origin_check import verify_client_origin
from app.db.models.live_handover import get_open_handover_for_session as get_active_session_for
from app.schemas.consent import (
    ConsentOfferResponse,
    SessionCloseRequest,
    SessionCloseResponse,
    SessionStartRequest,
    SessionStartResponse,
    SessionStatusResponse,
    ShareConsentRequest,
    ShareConsentResponse,
)
from app.schemas.users import SLUG_PATTERN
from app.services import live_chat as live
from app.services.chat_sessions import active_session_for, close_session, start_new_session
from app.services.consent import (
    NO_APPROVED_WORDING_MESSAGE,
    ConsentSessionEndedError,
    ConsentWordingUnavailableError,
    InvalidContactError,
    offered_wording,
    record_share_consent,
    session_for_consent,
)
from app.services.contact_privacy import ContactStorageNotConfigured
from app.services.public_client import get_active_client, get_live_client

router = APIRouter(prefix="/chat", tags=["chat-session"])

ClientQuery = Annotated[str, Query(min_length=2, max_length=100, pattern=SLUG_PATTERN)]

CONSENT_NOT_TICKED = "Tick the box to share your details, or continue without sharing."
CONTACTS_NOT_CONFIGURED = "Sharing details is not available on this server right now."


@router.post("/session/start", response_model=SessionStartResponse)
async def start_session(body: SessionStartRequest, request: Request) -> SessionStartResponse:
    """A new conversation, opened when the visitor makes the welcome choice."""
    client = await get_live_client(body.client)
    verify_client_origin(request, client)
    admin = await optional_admin_user(request) if body.test_session else None
    handle = await start_new_session(client.id, is_test_session=admin is not None)
    return SessionStartResponse(thread_id=str(handle.thread_id), session_id=str(handle.session_id))


@router.get("/session", response_model=SessionStatusResponse)
async def session_status(
    request: Request,
    client: ClientQuery,
    thread_id: Annotated[UUID | None, Query()] = None,
) -> SessionStatusResponse:
    """Whether the visitor's stored conversation is still open. Changes nothing."""
    record = await get_active_client(client)
    verify_client_origin(request, record)
    session = await active_session_for(thread_id, record.id)
    if session is None:
        return SessionStatusResponse(active=False)
    return SessionStatusResponse(
        active=True, session_id=str(session.session_id), known=session.type == "known"
    )


@router.get("/consent-text", response_model=ConsentOfferResponse)
async def consent_text(
    request: Request,
    client: ClientQuery,
    purpose: Annotated[Literal["share_contact", "callback"], Query()] = "share_contact",
) -> ConsentOfferResponse:
    """Approved wording for sharing details or asking for a callback, with the practice named."""
    record = await get_active_client(client)
    verify_client_origin(request, record)
    offer = await offered_wording(record.name, purpose)
    if offer is None:
        # No approved wording: the widget offers anonymous chat only.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=NO_APPROVED_WORDING_MESSAGE
        )
    return ConsentOfferResponse(purpose=offer.purpose, version=offer.version, text=offer.text)


@router.post("/session/consent", response_model=ShareConsentResponse)
async def share_consent(body: ShareConsentRequest, request: Request) -> ShareConsentResponse:
    client = await get_active_client(body.client)
    verify_client_origin(request, client)
    if not body.consent_accepted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=CONSENT_NOT_TICKED)
    try:
        session = await session_for_consent(body.session_id, body.thread_id, client.id)
        await record_share_consent(
            session=session,
            practice_name=client.name,
            version=body.text_version,
            full_name=body.full_name,
            phone=body.phone,
            is_current_patient=body.is_current_patient,
            purpose=body.purpose,
        )
    except ConsentSessionEndedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ConsentWordingUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvalidContactError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContactStorageNotConfigured as exc:
        # The reason goes to the log; the visitor gets a plain message.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=CONTACTS_NOT_CONFIGURED
        ) from exc
    return ShareConsentResponse(known=True, session_id=str(session.session_id))


@router.post("/session/close", response_model=SessionCloseResponse)
async def close(body: SessionCloseRequest, request: Request) -> SessionCloseResponse:
    """Sent when the widget's page is closed (pagehide). The visitor sees nothing."""
    client = await get_active_client(body.client)
    verify_client_origin(request, client)
    ended = await close_session(body.thread_id, client.id)
    # A live chat with staff ends with the conversation.
    open_handover = await get_active_session_for(body.thread_id, client.id)
    if open_handover is not None:
        await live.visitor_ends(open_handover)
    return SessionCloseResponse(ended=ended)
