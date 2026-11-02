from fastapi import APIRouter, Depends

from app.api.deps.auth import require_access
from app.core.permissions import Area
from app.schemas.admin_dashboard import AdminDashboardSummaryResponse
from app.services.dashboard import get_dashboard_summary

router = APIRouter(
    prefix="/admin/dashboard",
    tags=["admin-dashboard"],
    dependencies=[Depends(require_access(Area.dashboard))],
)


@router.get("/summary", response_model=AdminDashboardSummaryResponse)
async def dashboard_summary() -> AdminDashboardSummaryResponse:
    return await get_dashboard_summary()
