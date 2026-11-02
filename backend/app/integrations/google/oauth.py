import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from cryptography.fernet import Fernet, InvalidToken
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.core.config import get_settings
from app.db.models.oauth_pending import (
    cleanup_expired_oauth_pending_states,
    consume_oauth_pending_state,
    save_oauth_pending_state,
)
from app.db.models.google_token import (
    GoogleTokenRecord,
    get_google_token,
    update_google_token_access,
    upsert_google_token,
)

logger = logging.getLogger(__name__)

GOOGLE_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
TOKEN_URI = "https://oauth2.googleapis.com/token"


class GoogleOAuthError(Exception):
    pass


class TokenEncryptionError(GoogleOAuthError):
    pass


def _get_fernet() -> Fernet:
    settings = get_settings()
    if not settings.google_token_encryption_key:
        raise TokenEncryptionError(
            "GOOGLE_TOKEN_ENCRYPTION_KEY is not configured. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    try:
        return Fernet(settings.google_token_encryption_key.encode())
    except (TypeError, ValueError) as exc:
        raise TokenEncryptionError("GOOGLE_TOKEN_ENCRYPTION_KEY is invalid") from exc


def encrypt_refresh_token(refresh_token: str) -> str:
    return _get_fernet().encrypt(refresh_token.encode()).decode()


def decrypt_refresh_token(encrypted_token: str) -> str:
    try:
        return _get_fernet().decrypt(encrypted_token.encode()).decode()
    except InvalidToken as exc:
        raise TokenEncryptionError("Failed to decrypt stored refresh token") from exc


def _client_config() -> dict:
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise GoogleOAuthError("Google OAuth client credentials are not configured")
    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": TOKEN_URI,
            "redirect_uris": [settings.google_oauth_redirect_uri],
        }
    }


def create_oauth_flow() -> Flow:
    settings = get_settings()
    return Flow.from_client_config(
        _client_config(),
        scopes=[GOOGLE_CALENDAR_SCOPE],
        redirect_uri=settings.google_oauth_redirect_uri,
    )


def build_authorization_url() -> tuple[str, str]:
    """Sync helper — prefer start_oauth_flow() for the full PKCE-persisted flow."""
    flow = create_oauth_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )
    return _ensure_scope_in_auth_url(auth_url), state


async def start_oauth_flow() -> tuple[str, str]:
    """Build Google consent URL and persist PKCE verifier for the callback."""
    flow = create_oauth_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )
    auth_url = _ensure_scope_in_auth_url(auth_url)

    code_verifier = flow.code_verifier
    if not code_verifier:
        raise GoogleOAuthError("PKCE code verifier was not generated for OAuth flow")

    await cleanup_expired_oauth_pending_states()
    await save_oauth_pending_state(state, code_verifier)
    return auth_url, state


def _ensure_scope_in_auth_url(auth_url: str) -> str:
    """Some oauthlib builds omit scope; Google requires it on the consent URL."""
    parsed = urlparse(auth_url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if not params.get("scope"):
        params["scope"] = GOOGLE_CALENDAR_SCOPE
        auth_url = urlunparse(parsed._replace(query=urlencode(params, quote_via=quote)))
    return auth_url


def _credentials_from_record(record: GoogleTokenRecord) -> Credentials:
    settings = get_settings()
    refresh_token = decrypt_refresh_token(record.refresh_token)
    return Credentials(
        token=record.access_token,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=[GOOGLE_CALENDAR_SCOPE],
        expiry=record.token_expiry.replace(tzinfo=None) if record.token_expiry.tzinfo else record.token_expiry,
    )


def _expiry_from_credentials(credentials: Credentials) -> datetime:
    if credentials.expiry is None:
        return datetime.now(timezone.utc) + timedelta(hours=1)
    expiry = credentials.expiry
    if expiry.tzinfo is None:
        return expiry.replace(tzinfo=timezone.utc)
    return expiry.astimezone(timezone.utc)


async def exchange_code_and_store(code: str, state: str) -> GoogleTokenRecord:
    settings = get_settings()

    code_verifier = await consume_oauth_pending_state(state)
    if not code_verifier:
        raise GoogleOAuthError(
            "Invalid or expired OAuth state. Start again from /api/v1/admin/google/oauth/start"
        )

    flow = create_oauth_flow()
    flow.code_verifier = code_verifier

    try:
        flow.fetch_token(code=code)
    except Exception as exc:
        raise GoogleOAuthError(f"OAuth token exchange failed: {exc}") from exc

    credentials = flow.credentials

    if not credentials.refresh_token:
        raise GoogleOAuthError(
            "Google did not return a refresh token. "
            "Revoke app access in Google Account settings and reconnect with prompt=consent."
        )

    refresh_encrypted = encrypt_refresh_token(credentials.refresh_token)
    token_expiry = _expiry_from_credentials(credentials)

    connected_email: str | None = None
    try:
        from googleapiclient.discovery import build

        service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        calendar = service.calendarList().get(calendarId="primary").execute()
        connected_email = calendar.get("id")
    except Exception:
        logger.warning("Could not fetch calendar metadata after OAuth", exc_info=True)

    await upsert_google_token(
        access_token=credentials.token or "",
        refresh_token_encrypted=refresh_encrypted,
        token_expiry=token_expiry,
        calendar_id=settings.google_calendar_id,
        connected_email=connected_email,
    )

    record = await get_google_token()
    if record is None:
        raise GoogleOAuthError("Failed to persist Google OAuth tokens")
    return record


async def get_valid_credentials() -> Credentials:
    record = await get_google_token()
    if record is None:
        raise GoogleOAuthError("Google Calendar is not connected")

    credentials = _credentials_from_record(record)
    if credentials.expired:
        if not credentials.refresh_token:
            raise GoogleOAuthError(
                "Google Calendar access expired and no refresh token is stored. Please reconnect."
            )
        try:
            credentials.refresh(Request())
        except Exception as exc:
            logger.warning("Google OAuth token refresh failed", exc_info=True)
            raise GoogleOAuthError(
                "Google Calendar refresh failed. Please reconnect your calendar."
            ) from exc
        await update_google_token_access(
            access_token=credentials.token or "",
            token_expiry=_expiry_from_credentials(credentials),
        )
    return credentials


async def is_google_calendar_connected() -> bool:
    return await get_google_token() is not None
