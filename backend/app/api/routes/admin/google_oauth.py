from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse

from app.api.deps.auth import require_access
from app.booking.schemas import (
    OAuthDisconnectResponse,
    OAuthStartResponse,
    OAuthStatusResponse,
)
from app.core.config import get_settings
from app.core.permissions import Area
from app.db.models.google_token import delete_google_tokens, get_google_token
from app.integrations.google.oauth import (
    GoogleOAuthError,
    exchange_code_and_store,
    get_valid_credentials,
    is_google_calendar_connected,
    start_oauth_flow,
)
from app.services.admin_audit import log_admin_action

router = APIRouter(prefix="/admin/google/oauth", tags=["admin-google-oauth"])


def _admin_calendar_redirect_url(*, connected: bool = False, error: str | None = None) -> str:
    settings = get_settings()
    base = f"{settings.admin_frontend_url.rstrip('/')}/admin/calendar"
    if connected:
        return f"{base}?connected=1"
    if error:
        return f"{base}?error={quote(error, safe='')}"
    return base


@router.get("/start", response_model=None, dependencies=[Depends(require_access(Area.calendar))])
async def oauth_start(
    redirect: bool = Query(default=False, description="Redirect browser to Google consent"),
):
    try:
        auth_url, _state = await start_oauth_flow()
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
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    if error:
        return RedirectResponse(
            url=_admin_calendar_redirect_url(error=f"Google OAuth error: {error}"),
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
    if not code:
        return RedirectResponse(
            url=_admin_calendar_redirect_url(error="Missing authorization code"),
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
    if not state:
        return RedirectResponse(
            url=_admin_calendar_redirect_url(error="Missing OAuth state parameter"),
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )

    try:
        await exchange_code_and_store(code, state)
    except GoogleOAuthError as exc:
        return RedirectResponse(
            url=_admin_calendar_redirect_url(error=str(exc)),
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )

    return RedirectResponse(
        url=_admin_calendar_redirect_url(connected=True),
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


@router.get(
    "/status",
    response_model=OAuthStatusResponse,
    dependencies=[Depends(require_access(Area.calendar))],
)
async def oauth_status():
    if not await is_google_calendar_connected():
        return OAuthStatusResponse(connected=False)

    try:
        await get_valid_credentials()
    except GoogleOAuthError as exc:
        return OAuthStatusResponse(connected=False, refresh_error=str(exc))

    record = await get_google_token()
    if record is None:
        return OAuthStatusResponse(connected=False)

    return OAuthStatusResponse(
        connected=True,
        calendar_id=record.calendar_id,
        expires_at=record.token_expiry,
        connected_email=record.connected_email,
    )


@router.delete(
    "/disconnect",
    response_model=OAuthDisconnectResponse,
    dependencies=[Depends(require_access(Area.calendar))],
)
async def oauth_disconnect(request: Request):
    await delete_google_tokens()
    await log_admin_action(
        "calendar.disconnect",
        resource_type="calendar",
        request=request,
    )
    return OAuthDisconnectResponse()
