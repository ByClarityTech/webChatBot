from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.booking.config import get_business_hours_config
from app.db.models.booking import count_bookings
from app.integrations.google.oauth import is_google_calendar_connected
from app.schemas.admin_dashboard import AdminDashboardSummaryResponse
from app.services import document_worker


async def get_dashboard_summary() -> AdminDashboardSummaryResponse:
    config = await get_business_hours_config()
    tz = ZoneInfo(config.booking_timezone)
    now_local = datetime.now(tz)
    start_of_day = datetime.combine(now_local.date(), time.min, tzinfo=tz)
    end_of_day = datetime.combine(now_local.date(), time.max, tzinfo=tz)

    document_count, bookings_today, calendar_connected = await _gather_summary_counts(
        start_of_day=start_of_day,
        end_of_day=end_of_day,
    )
    return AdminDashboardSummaryResponse(
        document_count=document_count,
        bookings_today=bookings_today,
        calendar_connected=calendar_connected,
    )


async def _gather_summary_counts(
    *,
    start_of_day: datetime,
    end_of_day: datetime,
) -> tuple[int, int, bool]:
    documents = await document_worker.count_admin_documents()
    bookings_today = await count_bookings(slot_from=start_of_day, slot_to=end_of_day)
    calendar_connected = await is_google_calendar_connected()
    return documents, bookings_today, calendar_connected
