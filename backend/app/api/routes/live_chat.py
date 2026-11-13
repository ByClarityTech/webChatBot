"""Visitor side of a live handover: request a person, stream replies, send, and leave.

The stream is authenticated with the token returned by /request, sent in the X-Live-Token
header. It is never placed in a URL.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.origin_check import verify_client_origin
from app.schemas.users import SLUG_PATTERN
from app.services import live_chat as live
from app.services.broker import broker, sse_message, stream_frames, visitor_channel
from app.services.chat_sessions import active_session_for
from app.services.public_client import get_active_client, get_live_client

router = APIRouter(prefix="/chat/live", tags=["chat-live"])

ClientQuery = Annotated[str, Query(min_length=2, max_length=100, pattern=SLUG_PATTERN)]
TokenHeader = Annotated[str | None, Header(alias="X-Live-Token")]


class LiveRequest(BaseModel):
    client: str = Field(..., min_length=2, max_length=100, pattern=SLUG_PATTERN)
    thread_id: UUID
    session_id: UUID


class LiveRequestResponse(BaseModel):
    status: str
    handover_id: str | None = None
    token: str | None = None
    expires_at: str | None = None


class LiveMessageRequest(BaseModel):
    client: str = Field(..., min_length=2, max_length=100, pattern=SLUG_PATTERN)
    body: str = Field(..., min_length=1, max_length=2000)


def _enabled() -> None:
    if not get_settings().live_chat_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not available")


@router.post("/request", response_model=LiveRequestResponse)
async def request_live_chat(body: LiveRequest, request: Request) -> LiveRequestResponse:
    _enabled()
    client = await get_live_client(body.client)
    verify_client_origin(request, client)
    session = await active_session_for(body.thread_id, client.id)
    if session is None or session.session_id != body.session_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This conversation has ended. Start a new one to continue.",
        )
    outcome = await live.request_live(session, client.id)
    return LiveRequestResponse(
        status=outcome.status,
        handover_id=str(outcome.handover_id) if outcome.handover_id else None,
        token=outcome.token,
        expires_at=outcome.expires_at.isoformat() if outcome.expires_at else None,
    )


@router.get("/stream")
async def stream(
    request: Request,
    client: ClientQuery,
    handover_id: Annotated[UUID, Query()],
    x_live_token: TokenHeader = None,
) -> StreamingResponse:
    _enabled()
    record = await get_active_client(client)
    verify_client_origin(request, record)
    handover = await live.check_visitor_token(handover_id, x_live_token)
    if handover.client_id != record.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=live.NOT_FOUND)
    snapshot = await live.visitor_snapshot(handover)
    queue = broker.subscribe(visitor_channel(handover_id))

    async def events():
        try:
            yield sse_message("state", snapshot)
            async for frame in stream_frames(queue):
                yield frame
        finally:
            broker.unsubscribe(visitor_channel(handover_id), queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/message", status_code=status.HTTP_201_CREATED)
async def send_message(
    body: LiveMessageRequest,
    request: Request,
    handover_id: Annotated[UUID, Query()],
    x_live_token: TokenHeader = None,
) -> dict:
    _enabled()
    client = await get_active_client(body.client)
    verify_client_origin(request, client)
    handover = await live.check_visitor_token(handover_id, x_live_token)
    if handover.client_id != client.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=live.NOT_FOUND)
    message = await live.visitor_message(handover, body.body)
    return {"id": str(message.id)}


@router.post("/leave", status_code=status.HTTP_204_NO_CONTENT)
async def leave(
    request: Request,
    client: ClientQuery,
    handover_id: Annotated[UUID, Query()],
    x_live_token: TokenHeader = None,
) -> None:
    _enabled()
    record = await get_active_client(client)
    verify_client_origin(request, record)
    handover = await live.check_visitor_token(handover_id, x_live_token)
    if handover.client_id != record.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=live.NOT_FOUND)
    await live.visitor_ends(handover)
