from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps.admin_auth import require_admin_auth
from app.db.models.admin_audit_log import list_admin_audit_logs
from app.schemas.admin_dashboard import AdminAuditLogEntry, AdminAuditLogListResponse

router = APIRouter(
    prefix="/admin/audit-log",
    tags=["admin-audit-log"],
    dependencies=[Depends(require_admin_auth)],
)


@router.get("", response_model=AdminAuditLogListResponse)
async def get_audit_log(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AdminAuditLogListResponse:
    records = await list_admin_audit_logs(limit=limit)
    return AdminAuditLogListResponse(
        entries=[
            AdminAuditLogEntry(
                id=record.id,
                action=record.action,
                resource_type=record.resource_type,
                resource_id=record.resource_id,
                client_ip=record.client_ip,
                created_at=record.created_at,
            )
            for record in records
        ],
        limit=limit,
    )
