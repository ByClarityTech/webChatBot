from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations.google.oauth import GoogleOAuthError, get_valid_credentials


@pytest.mark.asyncio
async def test_get_valid_credentials_refreshes_expired_token():
    expired = datetime.now(timezone.utc) - timedelta(minutes=5)
    record = MagicMock()
    record.token_expiry = expired
    record.access_token = "old-access"
    record.refresh_token = "encrypted-refresh"
    record.calendar_id = "primary"
    record.connected_email = "admin@example.com"

    mock_credentials = MagicMock()
    mock_credentials.expired = True
    mock_credentials.refresh_token = "refresh-plain"
    mock_credentials.token = "new-access"
    mock_credentials.expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    mock_settings = MagicMock()
    mock_settings.google_client_id = "client-id"
    mock_settings.google_client_secret = "client-secret"

    with (
        patch("app.integrations.google.oauth.get_google_token", new_callable=AsyncMock, return_value=record),
        patch("app.integrations.google.oauth.get_settings", return_value=mock_settings),
        patch("app.integrations.google.oauth.decrypt_refresh_token", return_value="refresh-plain"),
        patch("app.integrations.google.oauth._credentials_from_record", return_value=mock_credentials),
        patch("app.integrations.google.oauth.update_google_token_access", new_callable=AsyncMock) as mock_update,
    ):
        result = await get_valid_credentials()

    mock_credentials.refresh.assert_called_once()
    mock_update.assert_awaited_once()
    assert result.token == "new-access"


@pytest.mark.asyncio
async def test_get_valid_credentials_not_connected():
    with patch(
        "app.integrations.google.oauth.get_google_token",
        new_callable=AsyncMock,
        return_value=None,
    ):
        with pytest.raises(GoogleOAuthError, match="not connected"):
            await get_valid_credentials()


@pytest.mark.asyncio
async def test_get_valid_credentials_refresh_failure_raises():
    expired = datetime.now(timezone.utc) - timedelta(minutes=5)
    record = MagicMock()
    record.token_expiry = expired
    record.access_token = "old-access"
    record.refresh_token = "encrypted-refresh"

    mock_credentials = MagicMock()
    mock_credentials.expired = True
    mock_credentials.refresh_token = "refresh-plain"
    mock_credentials.refresh.side_effect = Exception("invalid_grant")

    with (
        patch("app.integrations.google.oauth.get_google_token", new_callable=AsyncMock, return_value=record),
        patch("app.integrations.google.oauth._credentials_from_record", return_value=mock_credentials),
    ):
        with pytest.raises(GoogleOAuthError, match="refresh failed"):
            await get_valid_credentials()
