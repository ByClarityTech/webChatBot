from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.core.config import get_settings
from app.core.permissions import Area, can_access
from app.core.security import TokenConfigError
from app.db.models.user import UserRecord
from app.services.auth import resolve_token

NOT_AUTHENTICATED = "Not signed in or session expired"
FORBIDDEN = "You do not have access to this area"
PASSWORD_CHANGE_REQUIRED = "Password change required"
LOGIN_NOT_CONFIGURED = "Login is not configured on this server (JWT_SECRET missing or too short)"


async def get_current_user(request: Request) -> UserRecord:
    token = request.cookies.get(get_settings().session_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=NOT_AUTHENTICATED)
    try:
        user = await resolve_token(token)
    except TokenConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=LOGIN_NOT_CONFIGURED
        ) from exc
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=NOT_AUTHENTICATED)
    # Read by services.admin_audit so audit rows record who acted.
    request.state.current_user = user
    return user


def require_access(area: Area) -> Callable[..., Awaitable[UserRecord]]:
    """Dependency: signed in (401 otherwise) and allowed into `area` (403 otherwise).

    A user whose password was set by an admin gets 403 everywhere here until they
    change it; /auth/me and /auth/password use get_current_user and stay open.
    """

    async def dependency(
        request: Request,
        user: Annotated[UserRecord, Depends(get_current_user)],
    ) -> UserRecord:
        if user.must_change_password:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=PASSWORD_CHANGE_REQUIRED
            )
        if not can_access(user.user_type_code, area):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=FORBIDDEN)
        request.state.current_user = user
        return user

    return dependency


async def optional_admin_user(request: Request) -> UserRecord | None:
    """The signed-in admin, or None when the request carries no valid session.

    For public routes that behave differently for staff (test sessions). Never raises for a
    missing or bad session: the visitor simply gets the public behaviour.
    """
    token = request.cookies.get(get_settings().session_cookie_name)
    if not token:
        return None
    try:
        user = await resolve_token(token)
    except TokenConfigError:
        return None
    if user is None or user.must_change_password:
        return None
    return user
