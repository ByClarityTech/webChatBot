"""Admin analytics: the report and the zero-PHI CSV export (FR-26, FR-28).

Admin, Super Admin and Support may read the report and export it. The export has aggregate
numbers and outcome labels only; see app/services/analytics/csv_export.py.
"""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.api.deps.auth import require_access
from app.core.permissions import Area
from app.db.models.user import UserRecord
from app.schemas.analytics import AnalyticsResponse
from app.services.admin_audit import log_admin_action
from app.services.analytics.filters import DEFAULT_PRESET, Period, resolve_period
from app.services.analytics.reports import (
    build_analytics,
    export_conversations_csv,
    export_summary_csv,
    timezone_for,
    today_in,
)
from app.services.client_scope import resolve_optional_client_scope

router = APIRouter(prefix="/admin/analytics", tags=["admin-analytics"])

AnalyticsActor = Annotated[UserRecord, Depends(require_access(Area.analytics))]
# The CSV download is admin only (PRD D-02); Support gets the dashboard but not the file.
ExportActor = Annotated[UserRecord, Depends(require_access(Area.analytics_export))]
ClientIdQuery = Annotated[UUID | None, Query()]
PresetQuery = Annotated[str | None, Query(pattern=r"^(7d|30d|90d)$")]


async def _period(
    actor: UserRecord,
    client_id: UUID | None,
    preset: str | None,
    start: date | None,
    end: date | None,
) -> tuple[UUID | None, Period]:
    """The client (None for all clients, Super Admin only) and the chosen period."""
    client = await resolve_optional_client_scope(actor, client_id)
    scope_id = client.id if client is not None else None
    timezone = await timezone_for(scope_id)
    chosen_preset = preset if (preset or start or end) else None
    if chosen_preset is None and start is None and end is None:
        chosen_preset = DEFAULT_PRESET
    try:
        period = resolve_period(
            preset=chosen_preset, start=start, end=end, timezone=timezone, today=today_in(timezone)
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return scope_id, period


@router.get("", response_model=AnalyticsResponse)
async def get_analytics(
    actor: AnalyticsActor,
    client_id: ClientIdQuery = None,
    preset: PresetQuery = None,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> AnalyticsResponse:
    scope_id, period = await _period(actor, client_id, preset, start, end)
    return await build_analytics(scope_id, period)


@router.get("/export")
async def export_analytics(
    request: Request,
    actor: ExportActor,
    kind: Annotated[Literal["summary", "conversations"], Query()] = "summary",
    client_id: ClientIdQuery = None,
    preset: PresetQuery = None,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> Response:
    scope_id, period = await _period(actor, client_id, preset, start, end)
    try:
        if kind == "summary":
            body = await export_summary_csv(scope_id, period)
        else:
            body = await export_conversations_csv(scope_id, period)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await log_admin_action(
        "analytics.export",
        request=request,
        resource_type="analytics",
        resource_id=kind,
        client_id=scope_id,
    )
    filename = f"{kind}-{period.start.isoformat()}-{period.end.isoformat()}.csv"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
