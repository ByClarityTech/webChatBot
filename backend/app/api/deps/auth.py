from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.core.config import get_settings
from app.core.permissions import Area, can_access
from app.db.models.user import UserRecord
from app.services.auth import resolve_session

NOT_AUTHENTICATED = "Not signed in or session expired"
FORBIDDEN = "You do not have access to this area"


async def get_current_user(request: Request) -> UserRecord:
    token = request.cookies.get(get_settings().session_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=NOT_AUTHENTICATED)
    user = await resolve_session(token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=NOT_AUTHENTICATED)
    # Read by services.admin_audit so audit rows record who acted.
    request.state.current_user = user
    return user


def require_access(area: Area) -> Callable[..., Awaitable[UserRecord]]:
    """Dependency: signed in (401 otherwise) and allowed into `area` (403 otherwise)."""

    async def dependency(
        request: Request,
        user: Annotated[UserRecord, Depends(get_current_user)],
    ) -> UserRecord:
        if not can_access(user.user_type_code, area):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=FORBIDDEN)
        request.state.current_user = user
        return user

    return dependency
