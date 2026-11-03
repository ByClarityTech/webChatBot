"""Which client an admin request is about.

Admins and Support users always work in their own client. Super Admins have no client, so
they name one with ?client_id= on routes that hold client data (documents now; calendar,
business hours and bookings later).
"""

from uuid import UUID

from fastapi import HTTPException, status

from app.core.roles import UserType
from app.db.models.client import ClientRecord, get_client
from app.db.models.user import UserRecord


async def resolve_client_scope(actor: UserRecord, client_id: UUID | None) -> ClientRecord:
    """The client to act on. Super Admins must pass client_id; others may only pass their own."""
    client = await resolve_optional_client_scope(actor, client_id)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Choose a client (client_id is required for Super Admins)",
        )
    return client


async def resolve_optional_client_scope(
    actor: UserRecord, client_id: UUID | None
) -> ClientRecord | None:
    """Like resolve_client_scope, but a Super Admin without client_id gets None (all clients)."""
    if actor.user_type_code == UserType.super_admin.value:
        if client_id is None:
            return None
        target_id = client_id
    else:
        if client_id is not None and client_id != actor.client_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only work with your own client",
            )
        target_id = actor.client_id

    client = await get_client(target_id) if target_id is not None else None
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    return client
