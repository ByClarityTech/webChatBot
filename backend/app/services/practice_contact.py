"""The practice phone shown on the Call and Text buttons: a US number, stored as E.164."""

from app.services.contact_privacy import InvalidPhoneError, normalize_us_phone


def clean_practice_phone(raw: str | None) -> str | None:
    """None or blank clears the number. Anything else must be a valid US number."""
    if raw is None or not raw.strip():
        return None
    try:
        return normalize_us_phone(raw)
    except InvalidPhoneError as exc:
        raise ValueError(str(exc)) from exc
