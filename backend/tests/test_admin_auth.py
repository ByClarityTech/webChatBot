
import pytest
from auth_helpers import signed_in, signed_out
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin import (
    audit_log,
    bookings,
    calendar,
    clients,
    dashboard,
    documents,
    google_oauth,
    settings,
    users,
)
from app.core.csrf import CSRFOriginMiddleware
from app.core.permissions import AREA_ACCESS, Area
from app.core.roles import UserType

SA, ADMIN, SUPPORT = UserType.super_admin, UserType.admin, UserType.support


@pytest.fixture
def admin_app():
    test_app = FastAPI()
    test_app.include_router(google_oauth.router, prefix="/api/v1")
    return test_app


@pytest.fixture
def full_admin_app():
    test_app = FastAPI()
    for module in (
        audit_log,
        bookings,
        calendar,
        clients,
        dashboard,
        documents,
        google_oauth,
        settings,
        users,
    ):
        test_app.include_router(module.router, prefix="/api/v1")
    return test_app


# ---------------------------------------------------------------- permission map


def test_area_access_matches_role_decisions():
    """Decision 7, plus dashboard for every role."""
    expected = {
        Area.dashboard: {SA, ADMIN, SUPPORT},
        Area.documents: {SA, ADMIN},
        # D-05 settings are admin only: Support has no access to them.
        Area.calendar: {SA, ADMIN},
        Area.business_hours: {SA, ADMIN},
        Area.bookings_view: {SA, ADMIN, SUPPORT},
        Area.bookings_cancel: {SA, ADMIN, SUPPORT},
        Area.audit_log: {SA, ADMIN},
        Area.users: {SA, ADMIN},
        Area.clients: {SA},
        # PRD D-02: the CSV download is admin only.
        Area.analytics_export: {SA, ADMIN},
    }
    assert set(AREA_ACCESS) == set(Area)
    for area, roles in expected.items():
        assert AREA_ACCESS[area] == {role.value for role in roles}, area


ADMIN_ENDPOINTS = [
    ("GET", "/api/v1/admin/dashboard/summary"),
    ("GET", "/api/v1/admin/documents"),
    ("DELETE", "/api/v1/admin/documents/00000000-0000-0000-0000-000000000000"),
    ("POST", "/api/v1/admin/documents/00000000-0000-0000-0000-000000000000/reindex"),
    ("GET", "/api/v1/admin/google/oauth/start"),
    ("GET", "/api/v1/admin/google/oauth/status"),
    ("DELETE", "/api/v1/admin/google/oauth/disconnect"),
    ("GET", "/api/v1/admin/settings/business-hours"),
    ("GET", "/api/v1/admin/bookings"),
    ("GET", "/api/v1/admin/bookings/00000000-0000-0000-0000-000000000000"),
    ("PATCH", "/api/v1/admin/bookings/00000000-0000-0000-0000-000000000000"),
    ("GET", "/api/v1/admin/audit-log"),
    ("GET", "/api/v1/admin/users"),
    ("POST", "/api/v1/admin/users"),
    ("GET", "/api/v1/admin/users/00000000-0000-0000-0000-000000000000"),
    ("PATCH", "/api/v1/admin/users/00000000-0000-0000-0000-000000000000"),
    ("GET", "/api/v1/admin/clients"),
    ("POST", "/api/v1/admin/clients"),
    ("GET", "/api/v1/admin/clients/00000000-0000-0000-0000-000000000000"),
    ("PATCH", "/api/v1/admin/clients/00000000-0000-0000-0000-000000000000"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), ADMIN_ENDPOINTS)
async def test_every_admin_endpoint_requires_sign_in(full_admin_app, method, path):
    with signed_out(full_admin_app):
        transport = ASGITransport(app=full_admin_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.request(method, path)

    assert response.status_code == 401


SUPPORT_FORBIDDEN = [
    ("GET", "/api/v1/admin/documents"),
    ("DELETE", "/api/v1/admin/documents/00000000-0000-0000-0000-000000000000"),
    ("POST", "/api/v1/admin/documents/00000000-0000-0000-0000-000000000000/reindex"),
    ("GET", "/api/v1/admin/audit-log"),
    ("GET", "/api/v1/admin/users"),
    ("POST", "/api/v1/admin/users"),
    ("GET", "/api/v1/admin/clients"),
    ("POST", "/api/v1/admin/clients"),
    # D-05 settings: admin only.
    ("GET", "/api/v1/admin/settings/business-hours"),
    ("PUT", "/api/v1/admin/settings/business-hours"),
    ("GET", "/api/v1/admin/calendar"),
    ("PUT", "/api/v1/admin/calendar/mode"),
    ("GET", "/api/v1/admin/google/oauth/start"),
    ("GET", "/api/v1/admin/google/oauth/status"),
    ("DELETE", "/api/v1/admin/google/oauth/disconnect"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), SUPPORT_FORBIDDEN)
async def test_support_user_gets_403_outside_their_areas(full_admin_app, method, path):
    with signed_in(full_admin_app, SUPPORT):
        transport = ASGITransport(app=full_admin_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.request(method, path)

    assert response.status_code == 403
    assert response.json()["detail"] == "You do not have access to this area"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/admin/clients"),
        ("POST", "/api/v1/admin/clients"),
        ("PATCH", "/api/v1/admin/clients/00000000-0000-0000-0000-000000000000"),
    ],
)
async def test_admin_gets_403_on_clients(full_admin_app, method, path):
    with signed_in(full_admin_app, ADMIN):
        transport = ASGITransport(app=full_admin_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.request(method, path)

    assert response.status_code == 403


# ---------------------------------------------------------------- Google OAuth


@pytest.mark.asyncio
async def test_oauth_status_without_session_returns_401(admin_app):
    with signed_out(admin_app):
        transport = ASGITransport(app=admin_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/google/oauth/status")

    assert response.status_code == 401
    assert "not signed in" in response.json()["detail"].lower()


# Signed-in Google OAuth behaviour: tests/test_oauth.py.


# ---------------------------------------------------------------- CSRF


@pytest.fixture
def csrf_app():
    test_app = FastAPI()
    router = APIRouter()

    @router.post("/api/v1/admin/ping")
    async def admin_ping():
        return {"ok": True}

    @router.get("/api/v1/admin/ping")
    async def admin_ping_get():
        return {"ok": True}

    @router.post("/api/v1/chat-like")
    async def public_ping():
        return {"ok": True}

    test_app.include_router(router)
    test_app.add_middleware(
        CSRFOriginMiddleware,
        allowed_origins=["http://localhost:3000"],
        protected_prefixes=("/api/v1/admin", "/api/v1/auth"),
    )
    return test_app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "headers", "expected"),
    [
        ("POST", "/api/v1/admin/ping", {"origin": "https://evil.example"}, 403),
        ("POST", "/api/v1/admin/ping", {"origin": "null"}, 403),
        ("POST", "/api/v1/admin/ping", {"referer": "https://evil.example/page"}, 403),
        ("POST", "/api/v1/admin/ping", {"origin": "http://localhost:3000"}, 200),
        ("POST", "/api/v1/admin/ping", {"referer": "http://localhost:3000/admin"}, 200),
        ("POST", "/api/v1/admin/ping", {}, 200),
        ("GET", "/api/v1/admin/ping", {"origin": "https://evil.example"}, 200),
        ("POST", "/api/v1/chat-like", {"origin": "https://evil.example"}, 200),
    ],
)
async def test_csrf_origin_check(csrf_app, method, path, headers, expected):
    transport = ASGITransport(app=csrf_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(method, path, headers=headers)

    assert response.status_code == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), ADMIN_ENDPOINTS)
async def test_admin_routes_are_blocked_until_an_admin_set_password_is_changed(
    full_admin_app, method, path
):
    with signed_in(full_admin_app, SA) as user:
        user.must_change_password = True
        transport = ASGITransport(app=full_admin_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.request(method, path)

    assert response.status_code == 403
    assert response.json()["detail"] == "Password change required"
