import logging
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.core.roles import UserType
from app.core.security import (
    burn_password_check,
    hash_password,
    hash_session_token,
    new_session_token,
    password_needs_rehash,
    verify_password,
)
from app.db.models.user import UserRecord, get_user_by_email, record_successful_login
from app.db.models.user_session import (
    get_active_session,
    insert_user_session,
    revoke_user_session,
    touch_user_session,
)

logger = logging.getLogger(__name__)

# Avoid a DB write on every request; last_seen_at is only refreshed this often.
_TOUCH_INTERVAL = timedelta(minutes=1)


def is_user_allowed(user: UserRecord) -> bool:
    """Inactive users and users of suspended clients cannot log in or keep a session."""
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


async def start_session(user: UserRecord, *, client_ip: str | None, user_agent: str | None) -> str:
    """Create a session row and return the raw token for the cookie (only its hash is stored)."""
    settings = get_settings()
    token = new_session_token()
    await insert_user_session(
        user_id=user.id,
        token_hash=hash_session_token(token),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours),
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return token


async def resolve_session(token: str) -> UserRecord | None:
    session = await get_active_session(hash_session_token(token))
    if session is None or not is_user_allowed(session.user):
        return None
    if datetime.now(timezone.utc) - session.last_seen_at > _TOUCH_INTERVAL:
        await touch_user_session(session.session_id)
    return session.user


async def end_session(token: str) -> None:
    await revoke_user_session(hash_session_token(token))
