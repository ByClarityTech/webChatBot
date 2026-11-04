"""Google Calendar per client: credentials and health, and the connect flow's binding to the
client and user that started it. Google and the database are replaced by fakes."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from auth_helpers import make_client_record, make_user, signed_in
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin import google_oauth
from app.core.roles import UserType
from app.db.models.google_token import GoogleTokenRecord
from app.db.models.oauth_pending import OAuthPendingFlow
from app.integrations.google import oauth
from app.integrations.google.oauth import GoogleOAuthError, NeedsReconnectError

CLIENT_ID = uuid4()


def token_record(*, status="ok", expired=False, last_error=None) -> GoogleTokenRecord:
    now = datetime.now(UTC)
    return GoogleTokenRecord(
        id=1,
        access_token="old-access",
        refresh_token="encrypted-refresh",
        token_expiry=now - timedelta(minutes=5) if expired else now + timedelta(hours=1),
        calendar_id="primary",
        connected_email="owner@example.com",
        created_at=now,
        updated_at=now,
        client_id=CLIENT_ID,
        status=status,
        last_error=last_error,
    )


# ---------------------------------------------------------------- credentials and health


@pytest.fixture
def store(monkeypatch):
    calls = {"marked": [], "updated": []}

    async def mark(client_id, error):
        calls["marked"].append((client_id, error))

    async def update(client_id, **fields):
        calls["updated"].append((client_id, fields))

    monkeypatch.setattr(oauth, "mark_client_google_token_needs_reconnect", mark)
    monkeypatch.setattr(oauth, "update_client_google_token_access", update)
    monkeypatch.setattr(oauth, "decrypt_refresh_token", lambda _value: "refresh-plain")
    return calls


async def test_an_expired_token_is_refreshed_and_saved(store):
    credentials = MagicMock(expired=True, refresh_token="refresh-plain", token="new-access")
    credentials.expiry = datetime.now(UTC) + timedelta(hours=1)
    with (
        patch.object(
            oauth, "get_client_google_token", AsyncMock(return_value=token_record(expired=True))
        ),
        patch.object(oauth, "_credentials_from_record", return_value=credentials),
    ):
        result, calendar_id = await oauth.get_client_calendar(CLIENT_ID)

    credentials.refresh.assert_called_once()
    assert (result, calendar_id) == (credentials, "primary")
    assert store["updated"][0][0] == CLIENT_ID
    assert store["marked"] == []


async def test_a_refused_refresh_marks_the_connection_needs_reconnect(store):
    credentials = MagicMock(expired=True, refresh_token="refresh-plain")
    credentials.refresh.side_effect = Exception("invalid_grant: Token has been revoked")
    with (
        patch.object(
            oauth, "get_client_google_token", AsyncMock(return_value=token_record(expired=True))
        ),
        patch.object(oauth, "_credentials_from_record", return_value=credentials),
        pytest.raises(NeedsReconnectError),
    ):
        await oauth.get_client_calendar(CLIENT_ID)

    ((client_id, error),) = store["marked"]
    assert client_id == CLIENT_ID and "invalid_grant" in error


async def test_a_connection_marked_needs_reconnect_is_not_retried(store):
    record = token_record(status="needs_reconnect", last_error="Refresh failed: invalid_grant")
    with (
        patch.object(oauth, "get_client_google_token", AsyncMock(return_value=record)),
        patch.object(oauth, "_credentials_from_record") as build,
        pytest.raises(NeedsReconnectError, match="invalid_grant"),
    ):
        await oauth.get_client_calendar(CLIENT_ID)
    build.assert_not_called()


async def test_no_connection_is_an_error_and_not_connected(store):
    with patch.object(oauth, "get_client_google_token", AsyncMock(return_value=None)):
        with pytest.raises(GoogleOAuthError, match="not connected"):
            await oauth.get_client_calendar(CLIENT_ID)
        assert await oauth.is_google_calendar_connected(CLIENT_ID) is False


async def test_only_a_healthy_connection_counts_as_connected():
    for status, expected in (("ok", True), ("needs_reconnect", False)):
        record = token_record(status=status)
        with patch.object(oauth, "get_client_google_token", AsyncMock(return_value=record)):
            assert await oauth.is_google_calendar_connected(CLIENT_ID) is expected


def test_ensure_scope_in_auth_url_adds_missing_scope():
    bare = "https://accounts.google.com/o/oauth2/auth?response_type=code&client_id=test"
    assert parse_qs(urlsplit(oauth._ensure_scope_in_auth_url(bare)).query)["scope"] == [
        oauth.GOOGLE_CALENDAR_SCOPE
    ]


# ---------------------------------------------------------------- routes


@pytest.fixture
def app(monkeypatch):
    async def get_client(client_id):
        return make_client_record(client_id=client_id)

    monkeypatch.setattr("app.services.client_scope.get_client", get_client)
    settings = MagicMock(admin_frontend_url="http://localhost:3000")
    monkeypatch.setattr(google_oauth, "get_settings", lambda: settings)
    test_app = FastAPI()
    test_app.include_router(google_oauth.router, prefix="/api/v1")
    return test_app


def http(app):
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    )


def redirect_params(response) -> dict:
    assert response.status_code == 307
    location = urlsplit(response.headers["location"])
    assert location.path == "/admin/calendar"
    return {key: values[0] for key, values in parse_qs(location.query).items()}


@pytest.mark.parametrize("user_type", [UserType.super_admin, UserType.admin, UserType.support])
async def test_status_works_for_every_calendar_role(app, user_type):
    params = {"client_id": str(CLIENT_ID)} if user_type == UserType.super_admin else {}
    with (
        signed_in(app, user_type),
        patch.object(google_oauth, "get_client_google_token", AsyncMock(return_value=None)),
    ):
        async with http(app) as client:
            response = await client.get("/api/v1/admin/google/oauth/status", params=params)
    assert response.status_code == 200
    assert response.json()["connected"] is False


async def test_status_shows_reconnect_needed(app):
    record = token_record(status="needs_reconnect", last_error="Refresh failed: invalid_grant")
    with (
        signed_in(app, UserType.admin),
        patch.object(google_oauth, "get_client_google_token", AsyncMock(return_value=record)),
    ):
        async with http(app) as client:
            body = (await client.get("/api/v1/admin/google/oauth/status")).json()
    assert (body["connected"], body["needs_reconnect"]) == (False, True)
    assert "invalid_grant" in body["refresh_error"]


async def test_start_records_the_client_and_the_user(app):
    start = AsyncMock(return_value=("https://accounts.google.com/auth", "state"))
    with (
        signed_in(app, UserType.admin) as admin,
        patch.object(google_oauth, "start_oauth_flow", start),
    ):
        async with http(app) as client:
            response = await client.get("/api/v1/admin/google/oauth/start")
    assert response.json()["auth_url"] == "https://accounts.google.com/auth"
    assert start.await_args.kwargs == {"client_id": admin.client_id, "user_id": admin.id}


async def callback(app, *, user, flow, exchange=None):
    exchange = exchange or AsyncMock(return_value=token_record())
    with (
        patch.object(google_oauth, "take_pending_flow", AsyncMock(return_value=flow)),
        patch.object(google_oauth, "_signed_in_user", AsyncMock(return_value=user)),
        patch.object(google_oauth, "exchange_code_and_store", exchange),
    ):
        async with http(app) as client:
            response = await client.get(
                "/api/v1/admin/google/oauth/callback", params={"code": "abc", "state": "xyz"}
            )
    return response, exchange


async def test_the_user_who_started_can_finish_and_it_is_audited(app, audit_rows):
    admin = make_user(UserType.admin)
    flow = OAuthPendingFlow(code_verifier="v", client_id=admin.client_id, user_id=admin.id)

    response, exchange = await callback(app, user=admin, flow=flow)

    assert redirect_params(response) == {"connected": "1"}
    exchange.assert_awaited_once_with("abc", flow)
    assert (audit_rows[0]["action"], audit_rows[0]["client_id"], audit_rows[0]["user_id"]) == (
        "calendar.connect",
        admin.client_id,
        admin.id,
    )


async def test_a_super_admin_returns_to_the_clients_page(app):
    root = make_user(UserType.super_admin)
    flow = OAuthPendingFlow(code_verifier="v", client_id=CLIENT_ID, user_id=root.id)
    response, _ = await callback(app, user=root, flow=flow)
    assert redirect_params(response) == {"client_id": str(CLIENT_ID), "connected": "1"}


@pytest.mark.parametrize(
    "case", ["signed_out", "other_user", "other_client", "must_change_password"]
)
async def test_nobody_else_can_finish_a_flow(app, case, audit_rows):
    admin = make_user(UserType.admin)
    flow = OAuthPendingFlow(code_verifier="v", client_id=admin.client_id, user_id=admin.id)
    user = {
        "signed_out": None,
        "other_user": make_user(UserType.admin),
        # Same user id, but now an admin of another client.
        "other_client": make_user(UserType.admin),
        "must_change_password": make_user(UserType.admin),
    }[case]
    if case == "other_client":
        user.id = admin.id
    if case == "must_change_password":
        user.id, user.client_id, user.must_change_password = admin.id, admin.client_id, True

    response, exchange = await callback(app, user=user, flow=flow)

    assert "error" in redirect_params(response)
    exchange.assert_not_awaited()
    assert audit_rows == []


async def test_an_unknown_or_used_state_is_refused(app):
    with patch.object(
        google_oauth,
        "take_pending_flow",
        AsyncMock(
            side_effect=GoogleOAuthError("This connect link has expired or was already used.")
        ),
    ):
        async with http(app) as client:
            response = await client.get(
                "/api/v1/admin/google/oauth/callback", params={"code": "abc", "state": "forged"}
            )
    assert "expired" in redirect_params(response)["error"]


async def test_callback_without_code_redirects_with_an_error(app):
    async with http(app) as client:
        response = await client.get("/api/v1/admin/google/oauth/callback")
    assert redirect_params(response)["error"] == "Missing authorization code or state"


async def test_disconnect_removes_only_this_clients_connection(app, audit_rows):
    delete = AsyncMock(return_value=True)
    with (
        signed_in(app, UserType.admin) as admin,
        patch.object(google_oauth, "delete_client_google_token", delete),
    ):
        async with http(app) as client:
            response = await client.delete("/api/v1/admin/google/oauth/disconnect")
    assert response.status_code == 200
    delete.assert_awaited_once_with(admin.client_id)
    assert (audit_rows[0]["action"], audit_rows[0]["client_id"]) == (
        "calendar.disconnect",
        admin.client_id,
    )
