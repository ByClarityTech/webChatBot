from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps.auth import require_access
from app.core.permissions import Area
from app.core.roles import UserType
from app.db.models.admin_audit_log import list_admin_audit_logs
from app.db.models.user import UserRecord
from app.schemas.admin_dashboard import AdminAuditLogEntry, AdminAuditLogListResponse

router = APIRouter(prefix="/admin/audit-log", tags=["admin-audit-log"])


@router.get("", response_model=AdminAuditLogListResponse)
async def get_audit_log(
    actor: Annotated[UserRecord, Depends(require_access(Area.audit_log))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    client_id: Annotated[UUID | None, Query()] = None,
) -> AdminAuditLogListResponse:
    # Super Admin: everything, or one client. Admin: their own client only.
    if actor.user_type_code != UserType.super_admin.value:
        if client_id is not None and client_id != actor.client_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only see the audit log of your own client",
            )
        client_id = actor.client_id

    records = await list_admin_audit_logs(limit=limit, client_id=client_id)
    return AdminAuditLogListResponse(
        entries=[
            AdminAuditLogEntry(
                id=record.id,
                action=record.action,
                resource_type=record.resource_type,
                resource_id=record.resource_id,
                client_ip=record.client_ip,
                created_at=record.created_at,
                user_id=record.user_id,
                user_email=record.user_email,
                client_id=record.client_id,
            )
            for record in records
        ],
        limit=limit,
    )
