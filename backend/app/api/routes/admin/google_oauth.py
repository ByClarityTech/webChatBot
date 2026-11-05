from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse

from app.api.deps.auth import require_access
from app.booking.mode import MODE_GOOGLE, MODE_NONE
from app.booking.schemas import (
    OAuthDisconnectResponse,
    OAuthStartResponse,
    OAuthStatusResponse,
)
from app.core.config import get_settings
from app.core.permissions import Area, can_access
from app.core.roles import UserType
from app.core.security import TokenConfigError
from app.db.models.client import get_client, set_client_calendar_mode
from app.db.models.google_token import (
    STATUS_OK,
    delete_client_google_token,
    get_client_google_token,
)
from app.db.models.user import UserRecord
from app.integrations.google.oauth import (
    GoogleOAuthError,
    exchange_code_and_store,
    get_client_calendar,
    start_oauth_flow,
    take_pending_flow,
)
from app.services.admin_audit import log_admin_action
from app.services.auth import resolve_token
from app.services.client_scope import resolve_client_scope

router = APIRouter(prefix="/admin/google/oauth", tags=["admin-google-oauth"])

CalendarActor = Annotated[UserRecord, Depends(require_access(Area.calendar))]
# Super Admins name the client; others may leave it out (always their own client).
ClientIdQuery = Annotated[UUID | None, Query()]


def _admin_calendar_redirect_url(
    *, client_id: UUID | None = None, connected: bool = False, error: str | None = None
) -> str:
    settings = get_settings()
    params: dict[str, str] = {}
    if client_id is not None:
        params["client_id"] = str(client_id)
    if connected:
        params["connected"] = "1"
    if error:
        params["error"] = error
    base = f"{settings.admin_frontend_url.rstrip('/')}/admin/calendar"
    return f"{base}?{urlencode(params)}" if params else base


def _redirect(**kwargs) -> RedirectResponse:
    return RedirectResponse(
        url=_admin_calendar_redirect_url(**kwargs),
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


async def _signed_in_user(request: Request) -> UserRecord | None:
    """The admin user of this browser, if any. The callback is a top-level redirect from
    Google, so the SameSite=Lax login cookie comes with it."""
    token = request.cookies.get(get_settings().session_cookie_name)
    if not token:
        return None
    try:
        return await resolve_token(token)
    except TokenConfigError:
        return None


def _may_connect(user: UserRecord, client_id: UUID) -> bool:
    if user.must_change_password or not can_access(user.user_type_code, Area.calendar):
        return False
    return user.user_type_code == UserType.super_admin.value or user.client_id == client_id


@router.get("/start", response_model=None)
async def oauth_start(
    actor: CalendarActor,
    client_id: ClientIdQuery = None,
    redirect: bool = Query(default=False, description="Redirect browser to Google consent"),
):
    client = await resolve_client_scope(actor, client_id)
    try:
        auth_url, _state = await start_oauth_flow(client_id=client.id, user_id=actor.id)
    except GoogleOAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    if redirect:
        return RedirectResponse(url=auth_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    return OAuthStartResponse(auth_url=auth_url)


@router.get("/callback", response_model=None)
async def oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """Public: Google redirects here. The client comes only from the stored flow, and only the
    signed-in user who started that flow can finish it."""
    if error:
        return _redirect(error=f"Google OAuth error: {error}")
    if not code or not state:
        return _redirect(error="Missing authorization code or state")

    try:
        flow = await take_pending_flow(state)
    except GoogleOAuthError as exc:
        return _redirect(error=str(exc))

    user = await _signed_in_user(request)
    if user is None:
        return _redirect(error="Sign in to the admin, then connect the calendar again.")
    if user.id != flow.user_id or not _may_connect(user, flow.client_id):
        return _redirect(error="This connect link was started by someone else. Start again.")

    target = flow.client_id if user.user_type_code == UserType.super_admin.value else None
    try:
        await exchange_code_and_store(code, flow)
    except GoogleOAuthError as exc:
        return _redirect(client_id=target, error=str(exc))

    # Connecting Google means using it for this client's bookings.
    await set_client_calendar_mode(flow.client_id, MODE_GOOGLE)
    request.state.current_user = user  # for the audit row
    await log_admin_action(
        "calendar.connect",
        resource_type="calendar",
        request=request,
        client_id=flow.client_id,
    )
    return _redirect(client_id=target, connected=True)


@router.get("/status", response_model=OAuthStatusResponse)
async def oauth_status(actor: CalendarActor, client_id: ClientIdQuery = None):
    client = await resolve_client_scope(actor, client_id)
    record = await get_client_google_token(client.id)
    if record is None:
        return OAuthStatusResponse(connected=False)
    if record.status != STATUS_OK:
        return OAuthStatusResponse(
            connected=False,
            needs_reconnect=True,
            connected_email=record.connected_email,
            refresh_error=record.last_error,
        )

    try:
        await get_client_calendar(client.id)
    except GoogleOAuthError as exc:
        return OAuthStatusResponse(
            connected=False,
            needs_reconnect=True,
            connected_email=record.connected_email,
            refresh_error=str(exc),
        )

    record = await get_client_google_token(client.id)
    return OAuthStatusResponse(
        connected=True,
        calendar_id=record.calendar_id,
        expires_at=record.token_expiry,
        connected_email=record.connected_email,
    )


@router.delete("/disconnect", response_model=OAuthDisconnectResponse)
async def oauth_disconnect(request: Request, actor: CalendarActor, client_id: ClientIdQuery = None):
    client = await resolve_client_scope(actor, client_id)
    await delete_client_google_token(client.id)
    # Without a connection the Google mode cannot book: turn online booking off.
    current = await get_client(client.id)
    if current is not None and current.calendar_mode == MODE_GOOGLE:
        await set_client_calendar_mode(client.id, MODE_NONE)
    await log_admin_action(
        "calendar.disconnect",
        resource_type="calendar",
        request=request,
        client_id=client.id,
    )
    return OAuthDisconnectResponse()
