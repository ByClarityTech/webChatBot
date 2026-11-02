from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from app.api.deps.auth import require_access
from app.api.routes.admin.users import to_http_error
from app.core.permissions import Area
from app.db.models.client import ClientRecord
from app.schemas.users import (
    ClientCreateRequest,
    ClientListResponse,
    ClientResponse,
    ClientUpdateRequest,
)
from app.services import user_management
from app.services.admin_audit import log_admin_action
from app.services.user_management import UserManagementError

router = APIRouter(
    prefix="/admin/clients",
    tags=["admin-clients"],
    dependencies=[Depends(require_access(Area.clients))],
)


def to_client_response(client: ClientRecord) -> ClientResponse:
    return ClientResponse(
        id=client.id,
        name=client.name,
        slug=client.slug,
        status=client.status,
        user_count=client.user_count,
        created_at=client.created_at,
        updated_at=client.updated_at,
    )


@router.get("", response_model=ClientListResponse)
async def get_clients() -> ClientListResponse:
    clients = await user_management.get_clients()
    return ClientListResponse(clients=[to_client_response(client) for client in clients])


@router.post("", response_model=ClientResponse, status_code=status.HTTP_201_CREATED)
async def create_client(body: ClientCreateRequest, request: Request) -> ClientResponse:
    try:
        client = await user_management.create_client(name=body.name, slug=body.slug)
    except UserManagementError as exc:
        raise to_http_error(exc) from exc
    await log_admin_action(
        "client.create",
        request=request,
        resource_type="client",
        resource_id=str(client.id),
        client_id=client.id,
    )
    return to_client_response(client)


@router.get("/{client_id}", response_model=ClientResponse)
async def get_client(client_id: UUID) -> ClientResponse:
    try:
        client = await user_management.get_client_or_404(client_id)
    except UserManagementError as exc:
        raise to_http_error(exc) from exc
    return to_client_response(client)


@router.patch("/{client_id}", response_model=ClientResponse)
async def update_client(
    client_id: UUID,
    body: ClientUpdateRequest,
    request: Request,
) -> ClientResponse:
    try:
        client = await user_management.change_client(client_id, name=body.name, status=body.status)
    except UserManagementError as exc:
        raise to_http_error(exc) from exc
    action = {"active": "client.activate", "suspended": "client.suspend"}.get(
        body.status or "", "client.update"
    )
    await log_admin_action(
        action,
        request=request,
        resource_type="client",
        resource_id=str(client_id),
        client_id=client_id,
    )
    return to_client_response(client)
