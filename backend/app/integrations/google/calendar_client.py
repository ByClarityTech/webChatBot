import logging
from datetime import datetime

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.integrations.google.oauth import GoogleOAuthError, get_valid_credentials
from app.integrations.google.schemas import BusyPeriod, FreeBusyResult

logger = logging.getLogger(__name__)


class GoogleCalendarError(Exception):
    pass


def _google_api_error_message(exc: HttpError) -> str:
    status = exc.resp.status if exc.resp is not None else 0
    if status in (401, 403):
        return (
            "Google Calendar access failed. "
            "Reconnect via GET /api/v1/admin/google/oauth/start."
        )
    if status >= 500:
        return "Google Calendar is temporarily unavailable. Please try again later."
    reason = getattr(exc, "reason", None) or "request failed"
    return f"Google Calendar API error: {reason}"


def _parse_google_datetime(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


async def query_freebusy(
    *,
    calendar_id: str,
    time_min: datetime,
    time_max: datetime,
    timezone: str,
) -> FreeBusyResult:
    try:
        credentials = await get_valid_credentials()
    except GoogleOAuthError as exc:
        raise GoogleCalendarError(str(exc)) from exc

    try:
        service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        body = {
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "timeZone": timezone,
            "items": [{"id": calendar_id}],
        }
        response = service.freebusy().query(body=body).execute()
    except HttpError as exc:
        logger.exception("Google Calendar freebusy query failed")
        raise GoogleCalendarError(_google_api_error_message(exc)) from exc
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
    calendar_id: str,
    summary: str,
    description: str,
    start: datetime,
    end: datetime,
    attendee_email: str,
    timezone: str,
) -> str:
    try:
        credentials = await get_valid_credentials()
    except GoogleOAuthError as exc:
        raise GoogleCalendarError(str(exc)) from exc

    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start.isoformat(), "timeZone": timezone},
        "end": {"dateTime": end.isoformat(), "timeZone": timezone},
        "attendees": [{"email": attendee_email}],
    }

    try:
        service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        event = (
            service.events()
            .insert(calendarId=calendar_id, body=body, sendUpdates="all")
            .execute()
        )
    except HttpError as exc:
        logger.exception("Google Calendar event creation failed")
        raise GoogleCalendarError(_google_api_error_message(exc)) from exc
    except Exception as exc:
        logger.exception("Google Calendar event creation failed")
        raise GoogleCalendarError("Google Calendar API request failed") from exc

    return event.get("id", "")


async def delete_calendar_event(*, calendar_id: str, event_id: str) -> None:
    try:
        credentials = await get_valid_credentials()
    except GoogleOAuthError as exc:
        raise GoogleCalendarError(str(exc)) from exc

    try:
        service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    except HttpError as exc:
        logger.exception("Google Calendar event deletion failed")
        raise GoogleCalendarError(_google_api_error_message(exc)) from exc


async def list_events(
    *,
    calendar_id: str,
    time_min: datetime,
    time_max: datetime,
    timezone: str,
    max_results: int = 50,
) -> list[dict]:
    try:
        credentials = await get_valid_credentials()
    except GoogleOAuthError as exc:
        raise GoogleCalendarError(str(exc)) from exc

    try:
        service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        response = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min.isoformat(),
                timeMax=time_max.isoformat(),
                timeZone=timezone,
                singleEvents=True,
                orderBy="startTime",
                maxResults=max_results,
            )
            .execute()
        )
    except HttpError as exc:
        logger.exception("Google Calendar events list failed")
        raise GoogleCalendarError(_google_api_error_message(exc)) from exc
    except Exception as exc:
        logger.exception("Google Calendar events list failed")
        raise GoogleCalendarError("Google Calendar API request failed") from exc

    return response.get("items", [])
