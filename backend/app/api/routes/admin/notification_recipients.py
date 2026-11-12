"""Who receives the practice's notification emails (FR-34).

Access: Area.practice_contact (Admin and Super Admin). Admins work on their own client; a Super
Admin names the client. Every change is audited by recipient ID, not by address.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.api.deps.auth import require_access
from app.core.permissions import Area
from app.db.models.notification_recipient import (
    MAX_RECIPIENTS,
    DuplicateRecipient,
    NotificationRecipient,
    RecipientLimitReached,
    add_recipient,
    delete_recipient,
    list_recipients,
    set_recipient_enabled,
)
from app.db.models.user import UserRecord
from app.schemas.notifications import (
    NotificationRecipientCreate,
    NotificationRecipientList,
    NotificationRecipientResponse,
    NotificationRecipientUpdate,
)
from app.services.admin_audit import log_admin_action
from app.services.chat_sessions import utc_now
from app.services.client_scope import resolve_client_scope

router = APIRouter(prefix="/admin/settings/notification-recipients", tags=["admin-settings"])

Actor = Annotated[UserRecord, Depends(require_access(Area.practice_contact))]
ClientIdQuery = Annotated[UUID | None, Query()]


def _to_response(item: NotificationRecipient) -> NotificationRecipientResponse:
    return NotificationRecipientResponse(
        id=item.id, email=item.email, enabled=item.enabled, created_at=item.created_at
    )


@router.get("", response_model=NotificationRecipientList)
async def get_notification_recipients(actor: Actor, client_id: ClientIdQuery = None):
    client = await resolve_client_scope(actor, client_id)
    items = await list_recipients(client.id)
    return NotificationRecipientList(
        recipients=[_to_response(item) for item in items], limit=MAX_RECIPIENTS
    )


@router.post(
    "",
    response_model=NotificationRecipientResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_notification_recipient(
    body: NotificationRecipientCreate,
    request: Request,
    actor: Actor,
    client_id: ClientIdQuery = None,
):
    client = await resolve_client_scope(actor, client_id)
    try:
        item = await add_recipient(
            client.id, str(body.email).strip(), actor_id=actor.id, now=utc_now()
        )
    except RecipientLimitReached as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A practice can list up to {MAX_RECIPIENTS} notification recipients.",
        ) from exc
    except DuplicateRecipient as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That address is already on the notification list.",
        ) from exc
    await log_admin_action(
        "settings.notification_recipient_add",
        resource_type="settings",
        resource_id=str(item.id),
        request=request,
        client_id=client.id,
    )
    return _to_response(item)


@router.patch("/{recipient_id}", response_model=NotificationRecipientResponse)
async def update_notification_recipient(
    recipient_id: UUID,
    body: NotificationRecipientUpdate,
    request: Request,
    actor: Actor,
    client_id: ClientIdQuery = None,
):
    client = await resolve_client_scope(actor, client_id)
    item = await set_recipient_enabled(client.id, recipient_id, body.enabled)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipient not found")
    await log_admin_action(
        "settings.notification_recipient_update",
        resource_type="settings",
        resource_id=str(item.id),
        request=request,
        client_id=client.id,
    )
    return _to_response(item)


@router.delete("/{recipient_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_notification_recipient(
    recipient_id: UUID,
    request: Request,
    actor: Actor,
    client_id: ClientIdQuery = None,
):
    client = await resolve_client_scope(actor, client_id)
    if not await delete_recipient(client.id, recipient_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipient not found")
    await log_admin_action(
        "settings.notification_recipient_delete",
        resource_type="settings",
        resource_id=str(recipient_id),
        request=request,
        client_id=client.id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
