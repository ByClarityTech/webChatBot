from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.deps.auth import get_current_user
from app.core.config import get_settings
from app.core.rate_limit import get_login_failure_limiter, login_failure_key
from app.core.security import TokenConfigError, checked_jwt_secret
from app.db.models.user import UserRecord
from app.schemas.auth import ClientSummary, CurrentUserResponse, LoginRequest, LogoutResponse
from app.schemas.users import ChangePasswordRequest
from app.services.admin_audit import client_ip_from_request, log_admin_action
from app.services.auth import authenticate, issue_token
from app.services.user_management import InvalidInputError, change_own_password

router = APIRouter(prefix="/auth", tags=["auth"])

INVALID_CREDENTIALS = "Invalid email or password"


def to_current_user_response(user: UserRecord) -> CurrentUserResponse:
    client = None
    if user.client_id is not None:
        client = ClientSummary(
            id=user.client_id, name=user.client_name or "", slug=user.client_slug or ""
        )
    return CurrentUserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        user_type=user.user_type_code,
        client=client,
    )


def _cookie_options() -> dict:
    settings = get_settings()
    return {
        "key": settings.session_cookie_name,
        "httponly": True,
        "secure": settings.session_cookie_secure_effective,
        "samesite": "lax",
        "path": "/",
    }


def _set_token_cookie(response: Response, user: UserRecord) -> None:
    response.set_cookie(
        value=issue_token(user),
        max_age=get_settings().jwt_ttl_hours * 3600,
        **_cookie_options(),
    )


@router.post("/login", response_model=CurrentUserResponse)
async def login(body: LoginRequest, request: Request, response: Response) -> CurrentUserResponse:
    # Fail before checking the password if tokens can't be issued at all.
    try:
        checked_jwt_secret(get_settings().jwt_secret)
    except TokenConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    client_ip = client_ip_from_request(request)
    limiter = get_login_failure_limiter()
    key = login_failure_key(body.email, client_ip)

    retry_after = limiter.retry_after(key)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    user = await authenticate(body.email, body.password)
    if user is None:
        limiter.record_failure(key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_CREDENTIALS)

    limiter.clear(key)
    _set_token_cookie(response, user)
    return to_current_user_response(user)


@router.post("/logout", response_model=LogoutResponse)
async def logout(response: Response) -> LogoutResponse:
    # Tokens are not stored, so logout removes the cookie from this browser. A copied token
    # stays valid until it expires (JWT_TTL_HOURS) unless the user's tokens are cut off.
    response.delete_cookie(**_cookie_options())
    return LogoutResponse()


@router.get("/me", response_model=CurrentUserResponse)
async def me(user: Annotated[UserRecord, Depends(get_current_user)]) -> CurrentUserResponse:
    return to_current_user_response(user)


@router.post("/password", response_model=LogoutResponse)
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    response: Response,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> LogoutResponse:
    """Change your own password. All older tokens stop working; this browser gets a new one."""
    client_ip = client_ip_from_request(request)
    limiter = get_login_failure_limiter()
    # A wrong current password counts as a failed login, so this can't be used to guess it.
    key = login_failure_key(user.email, client_ip)
    retry_after = limiter.retry_after(key)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    try:
        await change_own_password(
            user,
            current_password=body.current_password,
            new_password=body.new_password,
        )
    except InvalidInputError as exc:
        if str(exc) == "Current password is incorrect":
            limiter.record_failure(key)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    limiter.clear(key)
    _set_token_cookie(response, user)
    await log_admin_action(
        "user.password_change", request=request, resource_type="user", resource_id=str(user.id)
    )
    return LogoutResponse()
