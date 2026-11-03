import logging
from datetime import timedelta
from uuid import UUID

from app.core.config import get_settings
from app.core.roles import UserType
from app.core.security import (
    TokenError,
    burn_password_check,
    create_access_token,
    decode_access_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from app.db.models.user import (
    UserRecord,
    get_user_by_email,
    get_user_by_id,
    record_successful_login,
)

logger = logging.getLogger(__name__)


def is_user_allowed(user: UserRecord) -> bool:
    """Inactive users and users of suspended clients cannot log in or use a token."""
    if not user.is_active:
        return False
    if user.user_type_code == UserType.super_admin.value:
        return True
    return user.client_status == "active"


async def authenticate(email: str, password: str) -> UserRecord | None:
    user = await get_user_by_email(email)
    if user is None:
        burn_password_check(password)
        return None
    if not verify_password(password, user.password_hash):
        return None
    if not is_user_allowed(user):
        logger.info("Login refused for disabled user or client: user_id=%s", user.id)
        return None

    new_hash = hash_password(password) if password_needs_rehash(user.password_hash) else None
    await record_successful_login(user.id, new_password_hash=new_hash)
    return user


def issue_token(user: UserRecord) -> str:
    """Signed JWT for the cookie. Raises TokenConfigError if JWT_SECRET is unusable."""
    settings = get_settings()
    return create_access_token(
        user.id, secret=settings.jwt_secret, ttl=timedelta(hours=settings.jwt_ttl_hours)
    )


async def resolve_token(token: str) -> UserRecord | None:
    """The user a valid token belongs to, or None.

    Signature and expiry are checked first; then the user is loaded, so deactivation,
    client suspension and tokens_valid_after (password resets) apply immediately.
    """
    try:
        claims = decode_access_token(token, secret=get_settings().jwt_secret)
        user_id = UUID(claims["sub"])
    except (TokenError, ValueError):
        return None
    user = await get_user_by_id(user_id)
    if user is None or not is_user_allowed(user):
        return None
    if user.tokens_valid_after is not None and claims["iat"] < int(
        user.tokens_valid_after.timestamp()
    ):
        return None
    return user
