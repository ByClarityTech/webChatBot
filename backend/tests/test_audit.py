"""Audit attribution (who acted, which client) and audit log scoping."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from auth_helpers import make_user, signed_in
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin import audit_log
from app.core.roles import UserType
from app.services.admin_audit import log_admin_action


def _request(user=None, host="10.0.0.5"):
    state = SimpleNamespace(**({"current_user": user} if user else {}))
    return SimpleNamespace(state=state, client=SimpleNamespace(host=host))


async def test_defaults_to_the_actor_and_their_client(audit_rows):
    user = make_user(UserType.admin)
    await log_admin_action("document.upload", request=_request(user), resource_type="document")
    assert audit_rows == [
        {
            "action": "document.upload",
            "resource_type": "document",
            "resource_id": None,
            "client_ip": "10.0.0.5",
            "user_id": user.id,
            "client_id": user.client_id,
        }
    ]


async def test_explicit_client_wins_including_none(audit_rows):
    user = make_user(UserType.admin)
    target_client = uuid4()
    await log_admin_action("user.create", request=_request(user), client_id=target_client)
    await log_admin_action("platform.thing", request=_request(user), client_id=None)
    assert [r["client_id"] for r in audit_rows] == [target_client, None]


async def test_super_admin_actions_default_to_no_client(audit_rows):
    await log_admin_action("settings.update", request=_request(make_user(UserType.super_admin)))
    assert audit_rows[0]["client_id"] is None


async def test_no_request_means_unknown_actor(audit_rows):
    await log_admin_action("job.run")
    assert audit_rows[0]["user_id"] is None and audit_rows[0]["client_ip"] == "unknown"


async def test_audit_failure_never_breaks_the_request(monkeypatch):
    monkeypatch.setattr(
        "app.services.admin_audit.insert_admin_audit_log",
        AsyncMock(side_effect=RuntimeError("db down")),
    )
    await log_admin_action("anything")  # must not raise


@pytest.fixture
def audit_app():
    test_app = FastAPI()
    test_app.include_router(audit_log.router, prefix="/api/v1")
    return test_app


async def _get(app, query=""):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(f"/api/v1/admin/audit-log{query}")


async def test_super_admin_sees_all_or_filters_by_client(audit_app):
    listing = AsyncMock(return_value=[])
    wanted = uuid4()
    with (
        signed_in(audit_app, UserType.super_admin),
        patch("app.api.routes.admin.audit_log.list_admin_audit_logs", listing),
    ):
        assert (await _get(audit_app)).status_code == 200
        assert (await _get(audit_app, f"?client_id={wanted}&limit=10")).status_code == 200
    assert listing.await_args_list[0].kwargs == {"limit": 50, "client_id": None}
    assert listing.await_args_list[1].kwargs == {"limit": 10, "client_id": wanted}


async def test_admin_is_always_scoped_to_own_client(audit_app):
    listing = AsyncMock(return_value=[])
    with (
        signed_in(audit_app, UserType.admin) as admin,
        patch("app.api.routes.admin.audit_log.list_admin_audit_logs", listing),
    ):
        assert (await _get(audit_app)).status_code == 200
        assert (await _get(audit_app, f"?client_id={admin.client_id}")).status_code == 200
        other = await _get(audit_app, f"?client_id={uuid4()}")
    assert [call.kwargs["client_id"] for call in listing.await_args_list] == [admin.client_id] * 2
    assert other.status_code == 403
