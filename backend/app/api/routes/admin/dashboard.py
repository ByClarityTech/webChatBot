from fastapi import APIRouter, Depends

from app.api.deps.admin_auth import require_admin_auth
from app.schemas.admin_dashboard import AdminDashboardSummaryResponse
from app.services.dashboard import get_dashboard_summary

router = APIRouter(
    prefix="/admin/dashboard",
    tags=["admin-dashboard"],
    dependencies=[Depends(require_admin_auth)],
)


@router.get("/summary", response_model=AdminDashboardSummaryResponse)
async def dashboard_summary() -> AdminDashboardSummaryResponse:
    return await get_dashboard_summary()
