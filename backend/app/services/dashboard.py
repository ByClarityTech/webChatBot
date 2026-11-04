from datetime import datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

from app.booking.config import default_business_hours, get_business_hours_config
from app.db.models.booking import count_bookings
from app.db.models.google_token import count_healthy_google_connections
from app.integrations.google.oauth import is_google_calendar_connected
from app.schemas.admin_dashboard import AdminDashboardSummaryResponse
from app.services import document_worker


async def get_dashboard_summary(client_id: UUID | None = None) -> AdminDashboardSummaryResponse:
    """Counts for one client, in its timezone. With client_id None (Super Admin, all
    clients): totals across clients for "today" in the .env timezone, and the calendar counts
    as connected when at least one client's calendar is."""
    if client_id is not None:
        config = await get_business_hours_config(client_id)
    else:
        config = default_business_hours()
    tz = ZoneInfo(config.booking_timezone)
    now_local = datetime.now(tz)
    start_of_day = datetime.combine(now_local.date(), time.min, tzinfo=tz)
    end_of_day = datetime.combine(now_local.date(), time.max, tzinfo=tz)

    documents = await document_worker.count_ready_documents(client_id)
    bookings_today = await count_bookings(
        client_id=client_id, slot_from=start_of_day, slot_to=end_of_day, status="confirmed"
    )
    if client_id is not None:
        calendar_connected = await is_google_calendar_connected(client_id)
    else:
        calendar_connected = await count_healthy_google_connections() > 0

    return AdminDashboardSummaryResponse(
        document_count=documents,
        bookings_today=bookings_today,
        calendar_connected=calendar_connected,
    )
