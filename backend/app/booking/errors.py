"""Booking errors the widget acts on. The body carries a stable code next to the message.

The widget branches on `code`, never on the message text. The message stays readable, so an
older widget that only shows `detail` still works. The booking route turns these into a JSON
response itself, so every app that mounts the router behaves the same.
"""

from fastapi.responses import JSONResponse

SLOT_TAKEN = "SLOT_TAKEN"
SESSION_ENDED = "SESSION_ENDED"
CONSENT_UNAVAILABLE = "CONSENT_UNAVAILABLE"
CURRENT_PATIENT_REQUIRED = "CURRENT_PATIENT_REQUIRED"


class BookingHTTPError(Exception):
    def __init__(self, status_code: int, detail: str, code: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.code = code


def booking_error_response(exc: BookingHTTPError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": exc.code},
    )
