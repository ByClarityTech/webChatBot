"""Single-use links for account setup and password reset.

Tokens are random, stored only as SHA-256 hashes (db/models/invite_token.py), and spent on
use. Setting a password checks the password before the link is spent, so a weak password
does not burn the link.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.config import get_settings
from app.core.security import hash_password, password_problem, token_cutoff_now
from app.db.models.invite_token import (
    INVITE,
    PASSWORD_RESET,
    find_unspent_token_user,
    replace_invite_token,
    spend_invite_token,
)
from app.db.models.user import UserRecord, get_user_by_email, get_user_by_id, update_user
from app.services.auth import is_user_allowed
from app.services.user_management import (
    ForbiddenError,
    InvalidInputError,
    can_manage_user,
    get_visible_user,
)

INVALID_LINK = "This link is invalid or has expired."
MAX_TOKEN_LENGTH = 256


class InvalidLinkError(Exception):
    pass


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _ttl(purpose: str) -> timedelta:
    settings = get_settings()
    if purpose == INVITE:
        return timedelta(hours=settings.invite_ttl_hours)
    return timedelta(hours=settings.password_reset_ttl_hours)


async def issue(user_id: UUID, purpose: str) -> str:
    """A new raw token for the email. Earlier unused tokens of the same purpose stop working."""
    token = secrets.token_urlsafe(32)  # 32 random bytes, URL-safe
    await replace_invite_token(
        user_id=user_id,
        token_hash=_hash(token),
        purpose=purpose,
        expires_at=datetime.now(UTC) + _ttl(purpose),
    )
    return token


async def resend_invite(actor: UserRecord, user_id: UUID) -> tuple[UserRecord, str]:
    target = await get_visible_user(actor, user_id)
    if not can_manage_user(actor, target):
        raise ForbiddenError("You cannot change this user")
    if not target.is_active:
        raise InvalidInputError("Activate the user before sending an invite")
    return target, await issue(target.id, INVITE)


async def request_password_reset(email: str) -> tuple[UserRecord, str] | None:
    """None when no active account matches. The caller gives the same reply either way."""
    user = await get_user_by_email(email)
    if user is None or not is_user_allowed(user):
        return None
    return user, await issue(user.id, PASSWORD_RESET)


async def set_password_with_token(token: str, *, new_password: str, purpose: str) -> None:
    if not token or len(token) > MAX_TOKEN_LENGTH:
        raise InvalidLinkError(INVALID_LINK)
    token_hash = _hash(token)

    user_id = await find_unspent_token_user(token_hash, purpose)
    if user_id is None:
        raise InvalidLinkError(INVALID_LINK)
    user = await get_user_by_id(user_id)
    if user is None or not is_user_allowed(user):
        raise InvalidLinkError(INVALID_LINK)

    problem = password_problem(new_password, email=user.email)
    if problem:
        raise InvalidInputError(problem)

    # Spend the link last, and atomically: a second request with the same token gets None here.
    if await spend_invite_token(token_hash, purpose) is None:
        raise InvalidLinkError(INVALID_LINK)
    # The user chose this password, so no forced change at next sign-in. Older tokens die.
    await update_user(
        user.id,
        password_hash=hash_password(new_password),
        tokens_valid_after=token_cutoff_now(),
        must_change_password=False,
    )
