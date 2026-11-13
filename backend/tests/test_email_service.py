"""Account email: SES client, console fallback, templates, invite and reset tokens.

boto3 is mocked throughout. Nothing here reaches AWS or the database.
"""

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from auth_helpers import make_client_record, make_user, signed_in
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import auth
from app.api.routes.admin import users as users_routes
from app.core.config import get_settings
from app.core.roles import UserType
from app.core.security import verify_password
from app.db.models.invite_token import INVITE, PASSWORD_RESET
from app.services import invites
from app.services import user_management as um
from app.services.email import (
    deliver,
    get_email_sender,
    log_email_configuration,
)
from app.services.email import ses as ses_module
from app.services.email.base import EmailDeliveryError, EmailMessage, mask_email
from app.services.email.console import ConsoleEmailSender
from app.services.email.messages import (
    invite_message,
    password_reset_message,
    welcome_message,
)
from app.services.email.ses import MAX_ATTEMPTS, SesEmailSender

SA, ADMIN, SUPPORT = UserType.super_admin, UserType.admin, UserType.support
STRONG = "Strong-password-123"
TOKEN = "abc_DEF-123xyz"

SES_KEYS = {
    "AWS_ACCESS_KEY_ID": "AKIAEXAMPLEKEYID",
    "AWS_SECRET_ACCESS_KEY": "super-secret-value-do-not-log",
}


@pytest.fixture
def env(monkeypatch):
    """Set env vars for one test, then rebuild settings so the next read sees the change."""

    def set_env(**values):
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()

    yield set_env
    get_settings.cache_clear()


# ---------------------------------------------------------------- settings and sender choice


def test_defaults_match_the_agreed_values():
    settings = get_settings()
    assert settings.ses_region == "ap-south-1"
    assert settings.emails_from_email == "tech@claritytechlabs.com"
    assert settings.emails_from_name == "Website Assistant"
    assert settings.invite_ttl_hours == 24
    assert settings.password_reset_ttl_hours == 1


def test_no_keys_falls_back_to_console_sender():
    assert isinstance(get_email_sender(), ConsoleEmailSender)


def test_one_key_without_the_other_falls_back_to_console(env):
    env(AWS_ACCESS_KEY_ID="AKIAEXAMPLEKEYID")
    assert isinstance(get_email_sender(), ConsoleEmailSender)


def test_keys_select_ses_and_initialise_boto3_client(env):
    env(**SES_KEYS, EMAILS_FROM_NAME="Test Name")
    ses_module._ses_client.cache_clear()
    with patch("app.services.email.ses.boto3.client") as boto_client:
        sender = get_email_sender()

    assert isinstance(sender, SesEmailSender)
    boto_client.assert_called_once_with(
        "ses",
        region_name="ap-south-1",
        aws_access_key_id="AKIAEXAMPLEKEYID",
        aws_secret_access_key="super-secret-value-do-not-log",
    )
    ses_module._ses_client.cache_clear()


def test_secret_key_is_masked_in_settings_repr(env):
    env(**SES_KEYS)
    assert "super-secret-value-do-not-log" not in repr(get_settings())


def test_startup_log_never_fails_without_keys(caplog, env):
    env(APP_ENV="production")
    with caplog.at_level(logging.ERROR):
        log_email_configuration()  # must not raise
    assert "AWS keys are not set" in caplog.text


# ---------------------------------------------------------------- console sender


def test_console_logs_the_link_in_development(caplog):
    sender = ConsoleEmailSender(app_env="development")
    with caplog.at_level(logging.INFO):
        sender.send_email(
            "jane@clinic.com", "Subject", "<p>x</p>", f"Link: https://h/?token={TOKEN}"
        )
    assert f"token={TOKEN}" in caplog.text
    assert "jane@clinic.com" not in caplog.text, "address is masked"


def test_console_withholds_the_body_outside_development(caplog):
    # A send that did not happen must fail, so a notification is never marked as sent.
    sender = ConsoleEmailSender(app_env="production")
    with caplog.at_level(logging.INFO), pytest.raises(EmailDeliveryError) as raised:
        sender.send_email("jane@clinic.com", "Subject", "<p>x</p>", f"Link: token={TOKEN}")
    assert raised.value.code == "SES_NOT_CONFIGURED"
    assert raised.value.retryable is False
    assert TOKEN not in caplog.text
    assert "not sent" in caplog.text


# ---------------------------------------------------------------- SES sender


def _ses(client):
    return SesEmailSender(
        access_key_id="AKIAEXAMPLEKEYID",
        secret_access_key="unused",
        region="ap-south-1",
        from_email="tech@claritytechlabs.com",
        from_name="Clarity Dental AI",
        configuration_set="cfg-set",
        client=client,
    )


def test_send_email_uses_the_per_message_sender_name_when_given():
    client = MagicMock()
    client.send_email.return_value = {"MessageId": "msg-2"}

    _ses(client).send_email("jane@clinic.com", "Hi", "<p>h</p>", "t", from_name="Smile Dental, Chennai")

    assert client.send_email.call_args.kwargs["Source"] == (
        '"Smile Dental, Chennai" <tech@claritytechlabs.com>'
    )


def test_send_email_builds_the_ses_request_and_returns_message_id():
    client = MagicMock()
    client.send_email.return_value = {"MessageId": "msg-1"}

    message_id = _ses(client).send_email("jane@clinic.com", "Hi", "<p>html</p>", "plain text")

    assert message_id == "msg-1"
    kwargs = client.send_email.call_args.kwargs
    assert kwargs["Source"] == "Clarity Dental AI <tech@claritytechlabs.com>"
    assert kwargs["Destination"] == {"ToAddresses": ["jane@clinic.com"]}
    assert kwargs["Message"]["Body"]["Html"]["Data"] == "<p>html</p>"
    assert kwargs["Message"]["Body"]["Text"]["Data"] == "plain text"
    assert kwargs["ConfigurationSetName"] == "cfg-set"


def test_message_rejected_is_not_retried_and_leaks_nothing(caplog):
    error = ClientError(
        {
            "Error": {
                "Code": "MessageRejected",
                "Message": "Email address is not verified: jane@clinic.com",
            }
        },
        "SendEmail",
    )
    client = MagicMock()
    client.send_email.side_effect = error

    with caplog.at_level(logging.INFO), pytest.raises(EmailDeliveryError) as raised:
        _ses(client).send_email("jane@clinic.com", "Hi", f"<p>{TOKEN}</p>", f"token {TOKEN}")

    assert client.send_email.call_count == 1
    assert str(raised.value) == "SES_REJECTED"
    assert raised.value.retryable is False
    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    assert "jane@clinic.com" not in caplog.text
    assert TOKEN not in caplog.text


def test_throttling_is_retried_then_succeeds():
    client = MagicMock()
    client.send_email.side_effect = [
        ClientError({"Error": {"Code": "Throttling", "Message": "slow"}}, "SendEmail"),
        {"MessageId": "msg-2"},
    ]
    with patch("app.services.email.ses.time.sleep") as sleep:
        assert _ses(client).send_email("a@b.com", "s", "h", "t") == "msg-2"
    assert client.send_email.call_count == 2
    sleep.assert_called_once()


def test_connection_errors_give_up_after_the_last_attempt():
    client = MagicMock()
    client.send_email.side_effect = BotoCoreError()
    with patch("app.services.email.ses.time.sleep"), pytest.raises(EmailDeliveryError):
        _ses(client).send_email("a@b.com", "s", "h", "t")
    assert client.send_email.call_count == MAX_ATTEMPTS


def test_deliver_never_raises_on_provider_failure(caplog):
    sender = MagicMock()
    sender.send_email.side_effect = EmailDeliveryError("SES error MessageRejected")
    message = EmailMessage("a@b.com", "Subject", "<p>h</p>", "t")
    with (
        patch("app.services.email.get_email_sender", return_value=sender),
        caplog.at_level(logging.ERROR),
    ):
        deliver(message)  # must not raise
    assert "Email delivery failed" in caplog.text


def test_deliver_never_raises_on_unexpected_errors(caplog):
    sender = MagicMock()
    sender.send_email.side_effect = RuntimeError("boom with a@b.com inside")
    with (
        patch("app.services.email.get_email_sender", return_value=sender),
        caplog.at_level(logging.ERROR),
    ):
        deliver(EmailMessage("a@b.com", "Subject", "<p>h</p>", "t"))
    assert "error=RuntimeError" in caplog.text
    assert "a@b.com" not in caplog.text


def test_mask_email_keeps_only_the_first_letter():
    assert mask_email("jane.doe@clinic.com") == "j***@clinic.com"


# ---------------------------------------------------------------- templates


def _user(**overrides):
    user = make_user(ADMIN)
    user.client_name = overrides.pop("client_name", "Example Clinic")
    for key, value in overrides.items():
        setattr(user, key, value)
    return user


def test_invite_has_setup_link_expiry_and_login_url():
    msg = invite_message(_user(), TOKEN)
    assert msg.subject == "Set up your Example Clinic admin account"
    assert msg.to_email == "admin@example.com"
    link = f"http://localhost:3000/auth/setup-password?token={TOKEN}"
    assert link in msg.html_content and link in msg.text_content
    assert "24 hours" in msg.text_content
    assert "http://localhost:3000/admin/login" in msg.text_content


def test_reset_has_its_own_link_and_one_hour_expiry():
    msg = password_reset_message(_user(), TOKEN)
    assert msg.subject == "Reset your password"
    assert f"http://localhost:3000/auth/reset-password?token={TOKEN}" in msg.text_content
    assert "1 hour(s)" in msg.text_content


def test_welcome_carries_no_token():
    msg = welcome_message(_user())
    assert "token=" not in msg.html_content and "token=" not in msg.text_content
    assert "http://localhost:3000/admin/login" in msg.html_content


def test_user_supplied_values_are_escaped_in_html_only():
    hostile = _user(full_name='<script>alert("x")</script> & Co')
    msg = invite_message(hostile, TOKEN)
    assert "<script>" not in msg.html_content
    assert "&lt;script&gt;" in msg.html_content
    assert '<script>alert("x")</script>' in msg.text_content


def test_missing_practice_name_falls_back_to_a_generic_phrase():
    msg = welcome_message(_user(client_name=None))
    assert "your organization" in msg.text_content


def test_no_template_placeholder_is_left_unfilled():
    for msg in (
        invite_message(_user(), TOKEN),
        password_reset_message(_user(), TOKEN),
        welcome_message(_user()),
    ):
        assert "$" not in msg.html_content and "$" not in msg.text_content


# ---------------------------------------------------------------- invite and reset tokens


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def test_issue_stores_only_a_hash_with_a_24_hour_expiry():
    uid = uuid4()
    with patch("app.services.invites.replace_invite_token", new_callable=AsyncMock) as store:
        token = await invites.issue(uid, INVITE)

    kwargs = store.await_args.kwargs
    assert kwargs["token_hash"] == _hash(token) and kwargs["token_hash"] != token
    assert len(token) >= 43, "32 random bytes, URL-safe base64"
    assert kwargs["purpose"] == INVITE and kwargs["user_id"] == uid
    remaining = kwargs["expires_at"] - datetime.now(UTC)
    assert timedelta(hours=23, minutes=59) < remaining <= timedelta(hours=24)


async def test_password_reset_token_expires_after_one_hour():
    with patch("app.services.invites.replace_invite_token", new_callable=AsyncMock) as store:
        await invites.issue(uuid4(), PASSWORD_RESET)
    remaining = store.await_args.kwargs["expires_at"] - datetime.now(UTC)
    assert timedelta(minutes=59) < remaining <= timedelta(hours=1)


async def test_each_issue_gives_a_different_token():
    with patch("app.services.invites.replace_invite_token", new_callable=AsyncMock):
        first = await invites.issue(uuid4(), INVITE)
        second = await invites.issue(uuid4(), INVITE)
    assert first != second


async def test_weak_password_is_refused_before_the_link_is_spent():
    user = make_user(SUPPORT)
    with (
        patch(
            "app.services.invites.find_unspent_token_user",
            new_callable=AsyncMock,
            return_value=user.id,
        ),
        patch("app.services.invites.get_user_by_id", new_callable=AsyncMock, return_value=user),
        patch("app.services.invites.spend_invite_token", new_callable=AsyncMock) as spend,
        pytest.raises(um.InvalidInputError, match="at least 12"),
    ):
        await invites.set_password_with_token(TOKEN, new_password="short", purpose=INVITE)
    spend.assert_not_awaited()


async def test_setting_a_password_spends_the_link_and_clears_the_forced_change():
    user = make_user(SUPPORT)
    with (
        patch(
            "app.services.invites.find_unspent_token_user",
            new_callable=AsyncMock,
            return_value=user.id,
        ),
        patch("app.services.invites.get_user_by_id", new_callable=AsyncMock, return_value=user),
        patch(
            "app.services.invites.spend_invite_token",
            new_callable=AsyncMock,
            return_value=user.id,
        ) as spend,
        patch("app.services.invites.update_user", new_callable=AsyncMock) as update,
    ):
        await invites.set_password_with_token(TOKEN, new_password=STRONG, purpose=INVITE)

    spend.assert_awaited_once_with(_hash(TOKEN), INVITE)
    kwargs = update.await_args.kwargs
    assert verify_password(STRONG, kwargs["password_hash"])
    assert kwargs["must_change_password"] is False
    assert kwargs["tokens_valid_after"] is not None, "older sessions end"


@pytest.mark.parametrize("token", ["", "x" * 300])
async def test_empty_or_oversized_token_never_touches_the_database(token):
    with patch("app.services.invites.find_unspent_token_user", new_callable=AsyncMock) as find:
        with pytest.raises(invites.InvalidLinkError):
            await invites.set_password_with_token(token, new_password=STRONG, purpose=INVITE)
    find.assert_not_awaited()


async def test_unknown_or_spent_token_is_invalid():
    with (
        patch(
            "app.services.invites.find_unspent_token_user",
            new_callable=AsyncMock,
            return_value=None,
        ),
        pytest.raises(invites.InvalidLinkError, match="invalid or has expired"),
    ):
        await invites.set_password_with_token(TOKEN, new_password=STRONG, purpose=PASSWORD_RESET)


async def test_a_lost_race_on_the_same_link_is_refused():
    user = make_user(SUPPORT)
    with (
        patch(
            "app.services.invites.find_unspent_token_user",
            new_callable=AsyncMock,
            return_value=user.id,
        ),
        patch("app.services.invites.get_user_by_id", new_callable=AsyncMock, return_value=user),
        patch("app.services.invites.spend_invite_token", new_callable=AsyncMock, return_value=None),
        patch("app.services.invites.update_user", new_callable=AsyncMock) as update,
        pytest.raises(invites.InvalidLinkError),
    ):
        await invites.set_password_with_token(TOKEN, new_password=STRONG, purpose=INVITE)
    update.assert_not_awaited()


async def test_link_for_a_deactivated_user_is_refused():
    user = make_user(SUPPORT)
    user.is_active = False
    with (
        patch(
            "app.services.invites.find_unspent_token_user",
            new_callable=AsyncMock,
            return_value=user.id,
        ),
        patch("app.services.invites.get_user_by_id", new_callable=AsyncMock, return_value=user),
        pytest.raises(invites.InvalidLinkError),
    ):
        await invites.set_password_with_token(TOKEN, new_password=STRONG, purpose=INVITE)


async def test_reset_request_for_unknown_email_sends_nothing():
    with (
        patch("app.services.invites.get_user_by_email", new_callable=AsyncMock, return_value=None),
        patch("app.services.invites.replace_invite_token", new_callable=AsyncMock) as store,
    ):
        assert await invites.request_password_reset("nobody@example.com") is None
    store.assert_not_awaited()


async def test_reset_request_for_suspended_client_sends_nothing():
    user = make_user(ADMIN)
    user.client_status = "suspended"
    with (
        patch("app.services.invites.get_user_by_email", new_callable=AsyncMock, return_value=user),
        patch("app.services.invites.replace_invite_token", new_callable=AsyncMock) as store,
    ):
        assert await invites.request_password_reset(user.email) is None
    store.assert_not_awaited()


async def test_resend_refuses_an_inactive_user():
    target = make_user(SUPPORT)
    target.is_active = False
    with (
        patch("app.services.invites.get_visible_user", new_callable=AsyncMock, return_value=target),
        pytest.raises(um.InvalidInputError, match="Activate the user"),
    ):
        await invites.resend_invite(make_user(SA), target.id)


# ---------------------------------------------------------------- create_user without a password


async def test_create_without_password_stores_an_unusable_hash():
    actor = make_user(SA)
    client = make_client_record(client_id=actor.client_id)
    created: list[dict] = []

    async def fake_insert(**kwargs):
        created.append(kwargs)
        return make_user(SUPPORT)

    with (
        patch(
            "app.services.user_management.get_client", new_callable=AsyncMock, return_value=client
        ),
        patch("app.services.user_management.insert_user", fake_insert),
    ):
        await um.create_user(
            actor,
            email="new@acme.com",
            full_name="New",
            password=None,
            user_type="support",
            client_id=client.id,
        )

    row = created[0]
    assert row["must_change_password"] is False
    assert not verify_password("anything", row["password_hash"])
    assert not verify_password(STRONG, row["password_hash"])


# ---------------------------------------------------------------- routes: admin users


@pytest.fixture
def users_app():
    test_app = FastAPI()
    test_app.include_router(users_routes.router, prefix="/api/v1")
    return test_app


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_create_without_password_schedules_an_invite(users_app):
    new_user = make_user(SUPPORT)
    with (
        signed_in(users_app, ADMIN),
        patch(
            "app.services.user_management.create_user",
            new_callable=AsyncMock,
            return_value=new_user,
        ) as create,
        patch("app.services.invites.issue", new_callable=AsyncMock, return_value="tok-1") as issue,
        patch("app.api.routes.admin.users.deliver") as send,
    ):
        async with _client(users_app) as client:
            response = await client.post(
                "/api/v1/admin/users",
                json={"email": "s@acme.com", "full_name": "S", "user_type": "support"},
            )

    assert response.status_code == 201
    assert create.await_args.kwargs["password"] is None
    issue.assert_awaited_once_with(new_user.id, INVITE)
    message = send.call_args.args[0]
    assert message.subject.startswith("Set up your ")
    assert "token=tok-1" in message.text_content


async def test_create_with_password_sends_welcome_without_a_token(users_app):
    with (
        signed_in(users_app, ADMIN),
        patch(
            "app.services.user_management.create_user",
            new_callable=AsyncMock,
            return_value=make_user(SUPPORT),
        ),
        patch("app.services.invites.issue", new_callable=AsyncMock) as issue,
        patch("app.api.routes.admin.users.deliver") as send,
    ):
        async with _client(users_app) as client:
            response = await client.post(
                "/api/v1/admin/users",
                json={
                    "email": "s@acme.com",
                    "full_name": "S",
                    "password": STRONG,
                    "user_type": "support",
                },
            )

    assert response.status_code == 201
    issue.assert_not_awaited()
    message = send.call_args.args[0]
    assert message.subject.startswith("Your ") and "token=" not in message.text_content


async def test_resend_invite_sends_a_new_link_and_is_audited(users_app, audit_rows):
    target = make_user(SUPPORT)
    with (
        signed_in(users_app, ADMIN),
        patch(
            "app.services.invites.resend_invite",
            new_callable=AsyncMock,
            return_value=(target, "tok-2"),
        ),
        patch("app.api.routes.admin.users.deliver") as send,
    ):
        async with _client(users_app) as client:
            response = await client.post(f"/api/v1/admin/users/{target.id}/resend-invite")

    assert response.status_code == 200
    assert "token=tok-2" in send.call_args.args[0].text_content
    assert [r["action"] for r in audit_rows] == ["user.invite_resent"]


async def test_support_cannot_resend_invites(users_app):
    with signed_in(users_app, SUPPORT):
        async with _client(users_app) as client:
            response = await client.post(f"/api/v1/admin/users/{uuid4()}/resend-invite")
    assert response.status_code == 403


# ---------------------------------------------------------------- routes: auth


@pytest.fixture
def auth_app():
    test_app = FastAPI()
    test_app.include_router(auth.router, prefix="/api/v1")
    return test_app


async def test_forgot_password_gives_the_same_reply_for_unknown_email(auth_app):
    with (
        patch("app.services.invites.get_user_by_email", new_callable=AsyncMock, return_value=None),
        patch("app.api.routes.auth.deliver") as send,
    ):
        async with _client(auth_app) as client:
            response = await client.post(
                "/api/v1/auth/forgot-password", json={"email": "nobody@example.com"}
            )

    assert response.status_code == 200
    assert response.json()["detail"] == auth.RESET_REQUESTED
    send.assert_not_called()


async def test_forgot_password_emails_a_reset_link_for_a_known_account(auth_app):
    known = make_user(ADMIN)
    with (
        patch("app.services.invites.get_user_by_email", new_callable=AsyncMock, return_value=known),
        patch("app.services.invites.replace_invite_token", new_callable=AsyncMock),
        patch("app.api.routes.auth.deliver") as send,
    ):
        async with _client(auth_app) as client:
            response = await client.post(
                "/api/v1/auth/forgot-password", json={"email": known.email}
            )

    assert response.json()["detail"] == auth.RESET_REQUESTED
    message = send.call_args.args[0]
    assert "/auth/reset-password?token=" in message.text_content


async def test_setup_with_a_bad_link_is_400(auth_app):
    with patch(
        "app.services.invites.find_unspent_token_user", new_callable=AsyncMock, return_value=None
    ):
        async with _client(auth_app) as client:
            response = await client.post(
                "/api/v1/auth/password/setup", json={"token": TOKEN, "new_password": STRONG}
            )
    assert response.status_code == 400
    assert response.json()["detail"] == invites.INVALID_LINK


async def test_setup_with_a_weak_password_is_400_and_keeps_the_link(auth_app):
    user = make_user(SUPPORT)
    with (
        patch(
            "app.services.invites.find_unspent_token_user",
            new_callable=AsyncMock,
            return_value=user.id,
        ),
        patch("app.services.invites.get_user_by_id", new_callable=AsyncMock, return_value=user),
        patch("app.services.invites.spend_invite_token", new_callable=AsyncMock) as spend,
    ):
        async with _client(auth_app) as client:
            response = await client.post(
                "/api/v1/auth/password/setup", json={"token": TOKEN, "new_password": "short"}
            )
    assert response.status_code == 400
    spend.assert_not_awaited()


async def test_setup_success_returns_a_message_and_sets_no_cookie(auth_app):
    user = make_user(SUPPORT)
    with (
        patch(
            "app.services.invites.find_unspent_token_user",
            new_callable=AsyncMock,
            return_value=user.id,
        ) as find,
        patch("app.services.invites.get_user_by_id", new_callable=AsyncMock, return_value=user),
        patch(
            "app.services.invites.spend_invite_token",
            new_callable=AsyncMock,
            return_value=user.id,
        ),
        patch("app.services.invites.update_user", new_callable=AsyncMock),
    ):
        async with _client(auth_app) as client:
            response = await client.post(
                "/api/v1/auth/password/setup", json={"token": TOKEN, "new_password": STRONG}
            )

    assert response.status_code == 200
    assert response.json()["detail"] == auth.PASSWORD_SET
    assert "set-cookie" not in response.headers
    assert find.await_args.args[1] == INVITE


async def test_reset_endpoint_only_accepts_reset_links(auth_app):
    with patch(
        "app.services.invites.find_unspent_token_user", new_callable=AsyncMock, return_value=None
    ) as find:
        async with _client(auth_app) as client:
            response = await client.post(
                "/api/v1/auth/reset-password", json={"token": TOKEN, "new_password": STRONG}
            )
    assert response.status_code == 400
    assert find.await_args.args[1] == PASSWORD_RESET
