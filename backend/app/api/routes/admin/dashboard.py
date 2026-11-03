from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps.auth import require_access
from app.core.permissions import Area
from app.db.models.user import UserRecord
from app.schemas.admin_dashboard import AdminDashboardSummaryResponse
from app.services.client_scope import resolve_optional_client_scope
from app.services.dashboard import get_dashboard_summary

router = APIRouter(prefix="/admin/dashboard", tags=["admin-dashboard"])


@router.get("/summary", response_model=AdminDashboardSummaryResponse)
async def dashboard_summary(
    actor: Annotated[UserRecord, Depends(require_access(Area.dashboard))],
    client_id: Annotated[UUID | None, Query()] = None,
) -> AdminDashboardSummaryResponse:
    # Admin and Support: their own client. Super Admin: one client, or all of them.
    client = await resolve_optional_client_scope(actor, client_id)
    return await get_dashboard_summary(client.id if client is not None else None)
