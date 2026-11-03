import secrets
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# argon2-cffi defaults to Argon2id with its recommended cost parameters.
_hasher = PasswordHasher()


PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128


def password_problem(password: str, *, email: str) -> str | None:
    """Reason a new password is not acceptable, or None if it is."""
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Password must be at least {PASSWORD_MIN_LENGTH} characters"
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"Password must be at most {PASSWORD_MAX_LENGTH} characters"
    if password.strip().lower() == email.strip().lower():
        return "Password must not be the same as the email address"
    return None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@lru_cache
def _dummy_hash() -> str:
    return _hasher.hash(secrets.token_urlsafe(16))


def burn_password_check(password: str) -> None:
    """Spend the time of a real check when the email is unknown, so timing doesn't reveal it."""
    verify_password(password, _dummy_hash())


# ---------------------------------------------------------------- JWT access tokens

JWT_ALGORITHM = "HS256"
JWT_ISSUER = "webchatbot-api"
JWT_AUDIENCE = "webchatbot-admin"
JWT_MIN_SECRET_LENGTH = 32


class TokenError(Exception):
    """The token is missing, malformed, expired, or signed with another key."""


class TokenConfigError(Exception):
    """JWT_SECRET is not set or too short to be safe."""


def checked_jwt_secret(secret: str) -> str:
    if len(secret or "") < JWT_MIN_SECRET_LENGTH:
        raise TokenConfigError(
            f"JWT_SECRET must be set to at least {JWT_MIN_SECRET_LENGTH} random characters"
        )
    return secret


def create_access_token(
    user_id: UUID, *, secret: str, ttl: timedelta, now: datetime | None = None
) -> str:
    issued = (now or datetime.now(UTC)).replace(microsecond=0)
    claims = {
        "sub": str(user_id),
        "iat": int(issued.timestamp()),
        "exp": int((issued + ttl).timestamp()),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
    }
    return jwt.encode(claims, checked_jwt_secret(secret), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str, *, secret: str) -> dict:
    """Verified claims. Only HS256 is accepted, so a token can't pick a weaker algorithm."""
    try:
        return jwt.decode(
            token,
            checked_jwt_secret(secret),
            algorithms=[JWT_ALGORITHM],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            options={"require": ["sub", "iat", "exp", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc


def token_cutoff_now() -> datetime:
    """Value for users.tokens_valid_after: now, to the second (JWT iat has no fractions).

    Tokens issued in an earlier second are refused; a token issued from now on is accepted.
    Taken from the app's clock, the same clock that stamps iat.
    """
    return datetime.now(UTC).replace(microsecond=0)
