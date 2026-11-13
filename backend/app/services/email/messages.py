"""Builds the three account emails. Values are escaped for HTML and used raw in plain text."""

from html import escape
from pathlib import Path
from string import Template
from urllib.parse import quote

from app.core.config import get_settings
from app.db.models.user import UserRecord
from app.services.email.base import EmailMessage

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _template(name: str) -> Template:
    return Template((TEMPLATE_DIR / name).read_text(encoding="utf-8"))


def _build(user: UserRecord, *, name: str, subject: str, values: dict[str, str]) -> EmailMessage:
    html_values = {key: escape(value, quote=True) for key, value in values.items()}
    html_content = _template(f"{name}.html").substitute(html_values, subject=escape(subject))
    text_content = _template(f"{name}.txt").substitute(values)
    return EmailMessage(
        to_email=user.email,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
        # The tenant's own name is the sender. Users without a client fall back to the default.
        from_name=user.client_name or None,
    )


def _practice(user: UserRecord) -> str:
    return user.client_name or "your organization"


def _common(user: UserRecord) -> dict[str, str]:
    settings = get_settings()
    return {
        "full_name": user.full_name,
        "practice_name": _practice(user),
        "login_url": f"{settings.admin_frontend_url.rstrip('/')}/admin/login",
    }


def _link(path: str, token: str) -> str:
    base = get_settings().admin_frontend_url.rstrip("/")
    return f"{base}{path}?token={quote(token, safe='')}"


def _account_subject(user: UserRecord, action: str) -> str:
    # No client name: keep the subject neutral, never "your your organization".
    if user.client_name:
        return f"{action} your {user.client_name} admin account"
    return f"{action} your admin account"


def invite_message(user: UserRecord, token: str) -> EmailMessage:
    settings = get_settings()
    values = _common(user) | {
        "setup_url": _link("/auth/setup-password", token),
        "expires_hours": str(settings.invite_ttl_hours),
    }
    return _build(
        user,
        name="invite",
        subject=_account_subject(user, "Set up"),
        values=values,
    )


def welcome_message(user: UserRecord) -> EmailMessage:
    if user.client_name:
        subject = f"Your {user.client_name} admin account is ready"
    else:
        subject = "Your admin account is ready"
    return _build(
        user,
        name="welcome",
        subject=subject,
        values=_common(user),
    )


def password_reset_message(user: UserRecord, token: str) -> EmailMessage:
    settings = get_settings()
    values = _common(user) | {
        "reset_url": _link("/auth/reset-password", token),
        "expires_hours": str(settings.password_reset_ttl_hours),
    }
    return _build(
        user,
        name="password_reset",
        subject="Reset your password",
        values=values,
    )
