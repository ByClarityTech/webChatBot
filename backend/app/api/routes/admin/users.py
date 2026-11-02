from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.deps.auth import require_access
from app.core.permissions import Area
from app.db.models.user import UserRecord
from app.schemas.users import (
    UserCreateRequest,
    UserListResponse,
    UserResponse,
    UserUpdateRequest,
)
from app.services import user_management
from app.services.admin_audit import log_admin_action
from app.services.user_management import (
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    UserManagementError,
)

router = APIRouter(prefix="/admin/users", tags=["admin-users"])

UsersActor = Annotated[UserRecord, Depends(require_access(Area.users))]

_ERROR_STATUS = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ForbiddenError: status.HTTP_403_FORBIDDEN,
    ConflictError: status.HTTP_409_CONFLICT,
    InvalidInputError: status.HTTP_400_BAD_REQUEST,
}


def to_http_error(exc: UserManagementError) -> HTTPException:
    return HTTPException(status_code=_ERROR_STATUS[type(exc)], detail=str(exc))


def to_user_response(user: UserRecord) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        user_type=user.user_type_code,
        client_id=user.client_id,
        client_name=user.client_name,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


@router.get("", response_model=UserListResponse)
async def get_users(
    actor: UsersActor,
    client_id: Annotated[UUID | None, Query()] = None,
) -> UserListResponse:
    try:
        users = await user_management.get_visible_users(actor, client_id=client_id)
    except UserManagementError as exc:
        raise to_http_error(exc) from exc
    return UserListResponse(users=[to_user_response(user) for user in users])


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreateRequest, request: Request, actor: UsersActor) -> UserResponse:
    try:
        user = await user_management.create_user(
            actor,
            email=str(body.email),
            full_name=body.full_name,
            password=body.password,
            user_type=body.user_type,
            client_id=body.client_id,
        )
    except UserManagementError as exc:
        raise to_http_error(exc) from exc
    await log_admin_action(
        "user.create",
        request=request,
        resource_type="user",
        resource_id=str(user.id),
        client_id=user.client_id,
    )
    return to_user_response(user)


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(user_id: UUID, actor: UsersActor) -> UserResponse:
    try:
        user = await user_management.get_visible_user(actor, user_id)
    except UserManagementError as exc:
        raise to_http_error(exc) from exc
    return to_user_response(user)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    body: UserUpdateRequest,
    request: Request,
    actor: UsersActor,
) -> UserResponse:
    try:
        user = await user_management.change_user(
            actor,
            user_id,
            full_name=body.full_name,
            is_active=body.is_active,
            password=body.password,
        )
    except UserManagementError as exc:
        raise to_http_error(exc) from exc

    actions = []
    if body.password is not None:
        actions.append("user.password_reset")
    if body.is_active is not None:
        actions.append("user.activate" if body.is_active else "user.deactivate")
    if body.full_name is not None:
        actions.append("user.update")
    for action in actions:
        await log_admin_action(
            action,
            request=request,
            resource_type="user",
            resource_id=str(user_id),
            client_id=user.client_id,
        )
    return to_user_response(user)
