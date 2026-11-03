"""Who may see and change which users and clients (services.user_management, admin routes)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import psycopg
import pytest
from auth_helpers import make_user, signed_in
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin import clients as clients_routes
from app.api.routes.admin import users as users_routes
from app.core.roles import UserType
from app.core.security import hash_password, token_cutoff_now, verify_password
from app.db.models.client import ClientRecord
from app.services import user_management as um

SA, ADMIN, SUPPORT = UserType.super_admin, UserType.admin, UserType.support
STRONG = "Strong-password-123"


def _client_record(*, status: str = "active", client_id=None) -> ClientRecord:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return ClientRecord(
        id=client_id or uuid4(),
        name="Acme",
        slug="acme",
        status=status,
        created_at=now,
        updated_at=now,
    )


def _same_client(user, other):
    other.client_id = user.client_id
    return other


# ---------------------------------------------------------------- view / manage rules


def test_view_rules():
    sa, admin = make_user(SA), make_user(ADMIN)
    own_support = _same_client(admin, make_user(SUPPORT))
    other_support = make_user(SUPPORT)

    assert um.can_view_user(sa, other_support) and um.can_view_user(sa, make_user(SA))
    assert um.can_view_user(admin, own_support)
    assert not um.can_view_user(admin, other_support)
    assert not um.can_view_user(admin, sa)
    assert not um.can_view_user(make_user(SUPPORT), own_support)


def test_manage_rules():
    sa, admin = make_user(SA), make_user(ADMIN)
    own_support = _same_client(admin, make_user(SUPPORT))
    own_admin = _same_client(admin, make_user(ADMIN))

    assert um.can_manage_user(sa, make_user(ADMIN)) and um.can_manage_user(sa, make_user(SUPPORT))
    assert not um.can_manage_user(sa, make_user(SA)), "no one edits Super Admins via the API"
    assert um.can_manage_user(admin, own_support)
    assert not um.can_manage_user(admin, own_admin), "Admins don't manage Admins"
    assert not um.can_manage_user(admin, admin), "not even themselves"
    assert not um.can_manage_user(admin, make_user(SUPPORT)), "other client's Support"


# ---------------------------------------------------------------- create_user


@pytest.fixture
def created():
    """insert_user mocked; returns the kwargs it was called with."""
    calls: list[dict] = []

    async def fake_insert(**kwargs):
        calls.append(kwargs)
        return make_user(SUPPORT)

    with patch("app.services.user_management.insert_user", fake_insert):
        yield calls


async def test_super_admin_creates_admin_in_chosen_client(created):
    client = _client_record()
    with patch(
        "app.services.user_management.get_client", new_callable=AsyncMock, return_value=client
    ):
        await um.create_user(
            make_user(SA),
            email=" New.Admin@Acme.COM ",
            full_name=" New Admin ",
            password=STRONG,
            user_type="admin",
            client_id=client.id,
        )

    row = created[0]
    assert row["client_id"] == client.id and row["user_type_id"] == 2
    assert row["email"] == "new.admin@acme.com" and row["full_name"] == "New Admin"
    assert row["password_hash"].startswith("$argon2id$") and verify_password(
        STRONG, row["password_hash"]
    )


async def test_admin_creates_support_in_own_client_only(created):
    admin = make_user(ADMIN)
    client = _client_record(client_id=admin.client_id)
    with patch(
        "app.services.user_management.get_client", new_callable=AsyncMock, return_value=client
    ):
        await um.create_user(
            admin,
            email="s@acme.com",
            full_name="S",
            password=STRONG,
            user_type="support",
            client_id=None,
        )
    assert created[0]["client_id"] == admin.client_id and created[0]["user_type_id"] == 3


@pytest.mark.parametrize(
    ("actor_type", "user_type", "client_id", "error"),
    [
        (ADMIN, "admin", None, um.ForbiddenError),
        (ADMIN, "support", "other", um.ForbiddenError),
        (SA, "super_admin", "any", um.ForbiddenError),
        (SA, "admin", None, um.InvalidInputError),
        (SUPPORT, "support", None, um.ForbiddenError),
    ],
)
async def test_create_user_refusals(created, actor_type, user_type, client_id, error):
    actor = make_user(actor_type)
    client_id = {"other": uuid4(), "any": uuid4(), None: None}[client_id]
    with pytest.raises(error):
        await um.create_user(
            actor,
            email="x@acme.com",
            full_name="X",
            password=STRONG,
            user_type=user_type,
            client_id=client_id,
        )
    assert created == []


@pytest.mark.parametrize(
    ("client", "password", "full_name", "error"),
    [
        (None, STRONG, "X", um.NotFoundError),
        ("suspended", STRONG, "X", um.InvalidInputError),
        ("active", "short", "X", um.InvalidInputError),
        ("active", STRONG, "   ", um.InvalidInputError),
    ],
)
async def test_create_user_input_problems(created, client, password, full_name, error):
    record = None if client is None else _client_record(status=client)
    with (
        patch(
            "app.services.user_management.get_client", new_callable=AsyncMock, return_value=record
        ),
        pytest.raises(error),
    ):
        await um.create_user(
            make_user(SA),
            email="x@acme.com",
            full_name=full_name,
            password=password,
            user_type="admin",
            client_id=uuid4(),
        )
    assert created == []


async def test_duplicate_email_is_a_conflict():
    with (
        patch(
            "app.services.user_management.get_client",
            new_callable=AsyncMock,
            return_value=_client_record(),
        ),
        patch(
            "app.services.user_management.insert_user",
            new_callable=AsyncMock,
            side_effect=psycopg.errors.UniqueViolation("duplicate"),
        ),
        pytest.raises(um.ConflictError),
    ):
        await um.create_user(
            make_user(SA),
            email="x@acme.com",
            full_name="X",
            password=STRONG,
            user_type="admin",
            client_id=uuid4(),
        )


# ---------------------------------------------------------------- change_user / own password


async def _change(actor, target, **changes):
    with (
        patch(
            "app.services.user_management.get_user_by_id",
            new_callable=AsyncMock,
            return_value=target,
        ),
        patch(
            "app.services.user_management.update_user", new_callable=AsyncMock, return_value=target
        ) as update,
    ):
        await um.change_user(
            actor,
            target.id,
            full_name=changes.get("full_name"),
            is_active=changes.get("is_active"),
            password=changes.get("password"),
        )
    return update


async def test_rename_does_not_sign_the_user_out():
    admin = make_user(ADMIN)
    update = await _change(admin, _same_client(admin, make_user(SUPPORT)), full_name=" New ")
    assert update.await_args.kwargs["full_name"] == "New"
    assert update.await_args.kwargs["tokens_valid_after"] is None


@pytest.mark.parametrize("change", [{"is_active": False}, {"password": STRONG}])
async def test_deactivation_or_password_reset_ends_existing_tokens(change):
    before = token_cutoff_now()
    update = await _change(make_user(SA), make_user(SUPPORT), **change)
    cutoff = update.await_args.kwargs["tokens_valid_after"]
    assert cutoff is not None and cutoff >= before and cutoff.microsecond == 0


async def test_reactivation_does_not_move_the_cutoff():
    update = await _change(make_user(SA), make_user(SUPPORT), is_active=True)
    assert update.await_args.kwargs["tokens_valid_after"] is None


async def test_change_user_hides_other_clients_and_forbids_admins():
    admin = make_user(ADMIN)
    with pytest.raises(um.NotFoundError):
        await _change(admin, make_user(SUPPORT), full_name="x")
    with pytest.raises(um.ForbiddenError):
        await _change(admin, _same_client(admin, make_user(ADMIN)), full_name="x")
    with pytest.raises(um.ForbiddenError):
        await _change(make_user(SA), make_user(SA), full_name="x")


async def test_change_own_password_sets_hash_and_ends_older_tokens():
    user = make_user(ADMIN)
    user.password_hash = hash_password("Old-password-123")
    with patch("app.services.user_management.update_user", new_callable=AsyncMock) as update:
        await um.change_own_password(user, current_password="Old-password-123", new_password=STRONG)

    assert verify_password(STRONG, update.await_args.kwargs["password_hash"])
    assert update.await_args.kwargs["tokens_valid_after"] is not None


@pytest.mark.parametrize(
    ("current", "new", "message"),
    [
        ("wrong", STRONG, "Current password is incorrect"),
        ("Old-password-123", "Old-password-123", "must be different"),
        ("Old-password-123", "short", "at least 12"),
    ],
)
async def test_change_own_password_refusals(current, new, message):
    user = make_user(ADMIN)
    user.password_hash = hash_password("Old-password-123")
    with (
        patch("app.services.user_management.update_user", new_callable=AsyncMock) as update,
        pytest.raises(um.InvalidInputError, match=message),
    ):
        await um.change_own_password(user, current_password=current, new_password=new)
    update.assert_not_awaited()


async def test_admin_cannot_list_another_clients_users():
    admin = make_user(ADMIN)
    with pytest.raises(um.ForbiddenError):
        await um.get_visible_users(admin, client_id=uuid4())
    with patch(
        "app.services.user_management.list_users", new_callable=AsyncMock, return_value=[]
    ) as listing:
        await um.get_visible_users(admin)
    listing.assert_awaited_once_with(client_id=admin.client_id)


# ---------------------------------------------------------------- routes


@pytest.fixture
def admin_routes_app():
    test_app = FastAPI()
    test_app.include_router(users_routes.router, prefix="/api/v1")
    test_app.include_router(clients_routes.router, prefix="/api/v1")
    return test_app


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (um.NotFoundError("User not found"), 404),
        (um.ForbiddenError("nope"), 403),
        (um.ConflictError("dup"), 409),
        (um.InvalidInputError("bad"), 400),
    ],
)
async def test_service_errors_map_to_http_status(admin_routes_app, error, status):
    with (
        signed_in(admin_routes_app, SA),
        patch(
            "app.services.user_management.get_visible_user",
            new_callable=AsyncMock,
            side_effect=error,
        ),
    ):
        async with _client(admin_routes_app) as client:
            response = await client.get(f"/api/v1/admin/users/{uuid4()}")

    assert response.status_code == status
    assert response.json()["detail"] == str(error)


async def test_create_user_route_audits_against_the_new_users_client(admin_routes_app, audit_rows):
    new_user = make_user(SUPPORT)
    with (
        signed_in(admin_routes_app, ADMIN) as actor,
        patch(
            "app.services.user_management.create_user",
            new_callable=AsyncMock,
            return_value=new_user,
        ),
    ):
        async with _client(admin_routes_app) as client:
            response = await client.post(
                "/api/v1/admin/users",
                json={
                    "email": "s@example.com",
                    "full_name": "S",
                    "password": STRONG,
                    "user_type": "support",
                },
            )

    assert response.status_code == 201
    assert response.json()["email"] == new_user.email
    assert [(r["action"], r["user_id"], r["client_id"]) for r in audit_rows] == [
        ("user.create", actor.id, new_user.client_id)
    ]


async def test_create_user_body_rejects_super_admin_type(admin_routes_app):
    with signed_in(admin_routes_app, SA):
        async with _client(admin_routes_app) as client:
            response = await client.post(
                "/api/v1/admin/users",
                json={
                    "email": "x@example.com",
                    "full_name": "X",
                    "password": STRONG,
                    "user_type": "super_admin",
                },
            )
    assert response.status_code == 422


async def test_update_user_route_audits_each_change(admin_routes_app, audit_rows):
    target = make_user(SUPPORT)
    with (
        signed_in(admin_routes_app, SA),
        patch(
            "app.services.user_management.change_user", new_callable=AsyncMock, return_value=target
        ),
    ):
        async with _client(admin_routes_app) as client:
            response = await client.patch(
                f"/api/v1/admin/users/{target.id}",
                json={"is_active": False, "password": STRONG, "full_name": "Renamed"},
            )

    assert response.status_code == 200
    assert [r["action"] for r in audit_rows] == [
        "user.password_reset",
        "user.deactivate",
        "user.update",
    ]
    assert all(r["client_id"] == target.client_id for r in audit_rows)


@pytest.mark.parametrize("slug", ["Acme", "acme_dental", "-acme", "a", "acme--x"])
async def test_client_slug_validation(admin_routes_app, slug):
    with signed_in(admin_routes_app, SA):
        async with _client(admin_routes_app) as client:
            response = await client.post(
                "/api/v1/admin/clients", json={"name": "Acme", "slug": slug}
            )
    assert response.status_code == 422


async def test_suspend_client_route_audits_against_that_client(admin_routes_app, audit_rows):
    client_record = _client_record(status="suspended")
    with (
        signed_in(admin_routes_app, SA),
        patch(
            "app.services.user_management.change_client",
            new_callable=AsyncMock,
            return_value=client_record,
        ),
    ):
        async with _client(admin_routes_app) as client:
            response = await client.patch(
                f"/api/v1/admin/clients/{client_record.id}", json={"status": "suspended"}
            )

    assert response.status_code == 200
    assert [(r["action"], r["client_id"]) for r in audit_rows] == [
        ("client.suspend", client_record.id)
    ]
