"""Notification emails to practice staff (FR-34).

Zero PHI. The builders take only the recipient, the practice name, and a source label. A
visitor's name, phone, email, message text, and appointment time have no parameter to go
into, so they cannot reach a subject or a body. Values are escaped for HTML.
"""

from html import escape
from string import Template

from app.core.config import get_settings
from app.db.models.callback_request import SOURCE_IN_CHAT, SOURCE_NO_STAFF, SOURCE_TIMEOUT
from app.db.models.notification_outbox import BOOKING_SUBJECT, CALLBACK_SUBJECT
from app.services.email.base import EmailMessage
from app.services.email.messages import TEMPLATE_DIR

SOURCE_LABELS = {
    SOURCE_IN_CHAT: "Asked in chat",
    SOURCE_NO_STAFF: "No staff online",
    SOURCE_TIMEOUT: "Not answered in 3 minutes",
}
UNKNOWN_SOURCE_LABEL = "Callback requested"


def source_label(source: str | None) -> str:
    return SOURCE_LABELS.get(source or "", UNKNOWN_SOURCE_LABEL)


def _admin_url(path: str) -> str:
    return f"{get_settings().admin_frontend_url.rstrip('/')}{path}"


def _render(name: str, values: dict[str, str], subject: str) -> tuple[str, str]:
    html_values = {key: escape(value, quote=True) for key, value in values.items()}
    html_content = Template((TEMPLATE_DIR / f"{name}.html").read_text(encoding="utf-8")).substitute(
        html_values, subject=escape(subject)
    )
    text_content = Template((TEMPLATE_DIR / f"{name}.txt").read_text(encoding="utf-8")).substitute(
        values
    )
    return html_content, text_content


def booking_notification(to_email: str, practice_name: str) -> EmailMessage:
    values = {"practice_name": practice_name, "bookings_url": _admin_url("/admin/bookings")}
    html_content, text_content = _render("booking_notification", values, BOOKING_SUBJECT)
    return EmailMessage(
        to_email=to_email,
        subject=BOOKING_SUBJECT,
        html_content=html_content,
        text_content=text_content,
    )


def callback_notification(to_email: str, practice_name: str, source: str | None) -> EmailMessage:
    values = {
        "practice_name": practice_name,
        "source_label": source_label(source),
        "inbox_url": _admin_url("/admin/inbox"),
    }
    html_content, text_content = _render("callback_notification", values, CALLBACK_SUBJECT)
    return EmailMessage(
        to_email=to_email,
        subject=CALLBACK_SUBJECT,
        html_content=html_content,
        text_content=text_content,
    )
