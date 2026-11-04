import logging
from datetime import datetime
from uuid import UUID

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.integrations.google.oauth import (
    GoogleOAuthError,
    get_client_calendar,
    mark_needs_reconnect,
)
from app.integrations.google.schemas import BusyPeriod, FreeBusyResult

logger = logging.getLogger(__name__)

# Every call works on one client's own Google connection and calendar.


class GoogleCalendarError(Exception):
    pass


def _google_api_error_message(exc: HttpError) -> str:
    status = exc.resp.status if exc.resp is not None else 0
    if status in (401, 403):
        return "Google Calendar access failed. Reconnect the calendar on the Calendar page."
    if status >= 500:
        return "Google Calendar is temporarily unavailable. Please try again later."
    reason = getattr(exc, "reason", None) or "request failed"
    return f"Google Calendar API error: {reason}"


async def _handle_http_error(client_id: UUID, exc: HttpError, action: str) -> GoogleCalendarError:
    logger.exception("Google Calendar %s failed for client %s", action, client_id)
    # 401: Google no longer accepts these credentials.
    if exc.resp is not None and exc.resp.status == 401:
        await mark_needs_reconnect(client_id, f"Google answered 401 during {action}")
    return GoogleCalendarError(_google_api_error_message(exc))


async def _calendar(client_id: UUID):
    try:
        credentials, calendar_id = await get_client_calendar(client_id)
    except GoogleOAuthError as exc:
        raise GoogleCalendarError(str(exc)) from exc
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    return service, calendar_id


def _parse_google_datetime(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


async def query_freebusy(
    *,
    client_id: UUID,
    time_min: datetime,
    time_max: datetime,
    timezone: str,
) -> FreeBusyResult:
    service, calendar_id = await _calendar(client_id)
    try:
        body = {
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "timeZone": timezone,
            "items": [{"id": calendar_id}],
        }
        response = service.freebusy().query(body=body).execute()
    except HttpError as exc:
        raise await _handle_http_error(client_id, exc, "freebusy query") from exc
    except Exception as exc:
        logger.exception("Google Calendar freebusy query failed")
        raise GoogleCalendarError("Google Calendar API request failed") from exc

    calendar_data = response.get("calendars", {}).get(calendar_id, {})
    busy_periods = [
        BusyPeriod(
            start=_parse_google_datetime(period["start"]),
            end=_parse_google_datetime(period["end"]),
        )
        for period in calendar_data.get("busy", [])
    ]
    return FreeBusyResult(calendar_id=calendar_id, busy=busy_periods)


async def create_calendar_event(
    *,
    client_id: UUID,
    summary: str,
    description: str,
    start: datetime,
    end: datetime,
    attendee_email: str,
    timezone: str,
) -> str:
    service, calendar_id = await _calendar(client_id)
    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start.isoformat(), "timeZone": timezone},
        "end": {"dateTime": end.isoformat(), "timeZone": timezone},
        "attendees": [{"email": attendee_email}],
    }

    try:
        event = (
            service.events()
            .insert(calendarId=calendar_id, body=body, sendUpdates="all")
            .execute()
        )
    except HttpError as exc:
        raise await _handle_http_error(client_id, exc, "event creation") from exc
    except Exception as exc:
        logger.exception("Google Calendar event creation failed")
        raise GoogleCalendarError("Google Calendar API request failed") from exc

    return event.get("id", "")


async def delete_calendar_event(*, client_id: UUID, event_id: str) -> None:
    service, calendar_id = await _calendar(client_id)
    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    except HttpError as exc:
        raise await _handle_http_error(client_id, exc, "event deletion") from exc
