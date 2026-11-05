"""Where a client's open booking slots come from (PRD FR-10).

- google: the client's Google Calendar gives busy times; bookings become Google events.
- built_in: business hours minus the client's own bookings; no Google account needed.
- none: no online booking (the widget and the bot do not offer it).
"""

from app.core.config import get_settings
from app.db.models.client import ClientRecord
from app.integrations.google.oauth import is_google_calendar_connected

MODE_GOOGLE = "google"
MODE_BUILT_IN = "built_in"
MODE_NONE = "none"


async def booking_available(client: ClientRecord) -> bool:
    """Whether visitors of this client can book online right now."""
    if not get_settings().booking_enabled:
        return False
    if client.calendar_mode == MODE_BUILT_IN:
        return True
    if client.calendar_mode == MODE_GOOGLE:
        return await is_google_calendar_connected(client.id)
    return False
