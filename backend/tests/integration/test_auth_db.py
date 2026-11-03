"""End-to-end auth against a real database: migrations, login, roles, user management, audit,
and the create_super_admin script."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import auth
from app.api.routes.admin import audit_log, clients, settings, users
from app.core.config import get_settings
from app.core.rate_limit import reset_login_failure_limiter
from app.core.security import decode_access_token, hash_password
from app.db.init import alembic_config

pytestmark = pytest.mark.integration

PW = "Root-password-123"
ADMIN_PW = "Admin-own-pass-123"
SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "create_super_admin.py"
HOURS = {
    "business_days": [1, 2, 3, 4, 5],
    "start_time": "09:00",
    "end_time": "17:00",
    "slot_duration_minutes": 30,
    "slot_buffer_minutes": 0,
    "timezone": "UTC",
}


@pytest.fixture
def app():
    test_app = FastAPI()
    for module in (auth, audit_log, clients, settings, users):
        test_app.include_router(module.router, prefix="/api/v1")
    return test_app


def browser(app):
    """A separate cookie jar, like one browser."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def login(client, email, password=PW):
    return (
        await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    ).status_code


def add_super_admin(db, email="root@example.com"):
    return db.execute(
        "INSERT INTO users (user_type_id, email, password_hash, full_name) "
        "VALUES (1, %s, %s, 'Root') RETURNING id",
        (email, hash_password(PW)),
    ).fetchone()[0]


# ---------------------------------------------------------------- schema


def test_migrations_seed_user_types_and_enforce_constraints(db):
    assert db.execute("SELECT id, code FROM user_types ORDER BY id").fetchall() == [
        (1, "super_admin"),
        (2, "admin"),
        (3, "support"),
    ]
    client_id = db.execute(
        "INSERT INTO clients (name, slug) VALUES ('A', 'a') RETURNING id"
    ).fetchone()[0]
    bad_rows = [
        ("super admin with a client", (client_id, 1, "x1@example.com")),
        ("admin without a client", (None, 2, "x2@example.com")),
    ]
    for label, params in bad_rows:
        with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
            db.execute(
                "INSERT INTO users (client_id, user_type_id, email, password_hash, full_name) "
                "VALUES (%s, %s, %s, 'h', 'n')",
                params,
            )
    db.execute(
        "INSERT INTO users (client_id, user_type_id, email, password_hash, full_name) "
        "VALUES (%s, 2, 'Same@Example.com', 'h', 'n')",
        (client_id,),
    )
    with pytest.raises(psycopg.errors.UniqueViolation), db.transaction():
        db.execute(
            "INSERT INTO users (client_id, user_type_id, email, password_hash, full_name) "
            "VALUES (%s, 3, 'same@EXAMPLE.com', 'h', 'n')",
            (client_id,),
        )
    with pytest.raises(psycopg.errors.RestrictViolation), db.transaction():
        db.execute("DELETE FROM clients WHERE id = %s", (client_id,))


def test_migrations_downgrade_and_upgrade_cleanly(database_url, db):
    from alembic import command

    config = alembic_config()
    command.downgrade(config, "0001_baseline")
    tables = {
        r[0] for r in db.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    }
    assert not tables & {"users", "clients", "user_types", "user_sessions"}
    command.upgrade(config, "head")
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(config).get_current_head()
    assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == head
    tables = {
        r[0] for r in db.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    }
    assert "user_sessions" not in tables, "JWT login keeps no session table"


# ---------------------------------------------------------------- JWT login


def with_cookie(app, token):
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"webchatbot_session": token},
    )


async def test_login_sets_a_jwt_and_logout_clears_the_cookie(app, db):
    user_id = add_super_admin(db)
    async with browser(app) as b:
        assert await login(b, "ROOT@example.com") == 200
        assert (await b.get("/api/v1/auth/me")).json()["user_type"] == "super_admin"
        token = b.cookies.get("webchatbot_session")
        claims = decode_access_token(token, secret=get_settings().jwt_secret)
        assert claims["sub"] == str(user_id) and claims["exp"] - claims["iat"] == 24 * 3600
        assert db.execute("SELECT last_login_at FROM users").fetchone()[0] is not None
        assert (await b.post("/api/v1/auth/logout")).status_code == 200
        assert (await b.get("/api/v1/auth/me")).status_code == 401, "this browser is signed out"
    # Agreed trade-off: no session table, so a copied token works until it expires.
    async with with_cookie(app, token) as copied:
        assert (await copied.get("/api/v1/auth/me")).status_code == 200


async def test_admin_password_reset_ends_every_existing_token(app, db):
    add_super_admin(db)
    client_id = db.execute(
        "INSERT INTO clients (name, slug) VALUES ('A', 'a') RETURNING id"
    ).fetchone()[0]
    target_id = db.execute(
        "INSERT INTO users (client_id, user_type_id, email, password_hash, full_name) "
        "VALUES (%s, 3, 'help@a.com', %s, 'H') RETURNING id",
        (client_id, hash_password(PW)),
    ).fetchone()[0]
    async with browser(app) as support, browser(app) as sa:
        await login(support, "help@a.com")
        token = support.cookies.get("webchatbot_session")
        # Let a second pass: a token issued in the cutoff's own second is still accepted.
        await asyncio.sleep(1.1)
        await login(sa, "root@example.com")
        r = await sa.patch(
            f"/api/v1/admin/users/{target_id}", json={"password": "Reset-pass-12345"}
        )
        assert r.status_code == 200
        assert (await support.get("/api/v1/auth/me")).status_code == 401
    async with with_cookie(app, token) as copied:
        assert (await copied.get("/api/v1/auth/me")).status_code == 401, "copies die too"
    assert (
        db.execute("SELECT tokens_valid_after FROM users WHERE id = %s", (target_id,)).fetchone()[0]
        is not None
    )


async def test_suspension_and_deactivation_end_access_at_once(app, db):
    client_id = db.execute(
        "INSERT INTO clients (name, slug) VALUES ('A', 'a') RETURNING id"
    ).fetchone()[0]
    db.execute(
        "INSERT INTO users (client_id, user_type_id, email, password_hash, full_name) "
        "VALUES (%s, 2, 'admin@a.com', %s, 'A')",
        (client_id, hash_password(PW)),
    )
    async with browser(app) as b:
        assert await login(b, "admin@a.com") == 200
        db.execute("UPDATE clients SET status = 'suspended'")
        assert (await b.get("/api/v1/auth/me")).status_code == 401
        db.execute("UPDATE clients SET status = 'active'")
        assert (await b.get("/api/v1/auth/me")).status_code == 200
        db.execute("UPDATE users SET is_active = FALSE")
        assert (await b.get("/api/v1/auth/me")).status_code == 401
        db.execute("UPDATE users SET is_active = TRUE")
        db.execute("UPDATE clients SET status = 'suspended'")
        assert await login(b, "admin@a.com") == 401


# ---------------------------------------------------------------- user management + audit


async def test_user_management_rules_and_audit_trail(app, db):
    add_super_admin(db)
    async with browser(app) as sa:
        await login(sa, "root@example.com")
        acme = (
            await sa.post("/api/v1/admin/clients", json={"name": "Acme", "slug": "acme"})
        ).json()
        beta = (
            await sa.post("/api/v1/admin/clients", json={"name": "Beta", "slug": "beta"})
        ).json()
        r = await sa.post(
            "/api/v1/admin/users",
            json={
                "email": "admin@acme.com",
                "full_name": "A",
                "password": PW,
                "user_type": "admin",
                "client_id": acme["id"],
            },
        )
        assert r.status_code == 201
        beta_support = (
            await sa.post(
                "/api/v1/admin/users",
                json={
                    "email": "s@beta.com",
                    "full_name": "S",
                    "password": PW,
                    "user_type": "support",
                    "client_id": beta["id"],
                },
            )
        ).json()

    async with browser(app) as admin, browser(app) as support:
        await login(admin, "admin@acme.com")
        # The Super Admin chose this password, so the Admin must replace it first.
        assert (await admin.get("/api/v1/admin/users")).status_code == 403
        r = await admin.post(
            "/api/v1/auth/password", json={"current_password": PW, "new_password": ADMIN_PW}
        )
        assert r.status_code == 200
        r = await admin.post(
            "/api/v1/admin/users",
            json={
                "email": "help@acme.com",
                "full_name": "H",
                "password": PW,
                "user_type": "support",
            },
        )
        assert r.status_code == 201 and r.json()["client_id"] == acme["id"]
        help_id = r.json()["id"]
        emails = sorted(
            u["email"] for u in (await admin.get("/api/v1/admin/users")).json()["users"]
        )
        assert emails == ["admin@acme.com", "help@acme.com"]
        assert (await admin.get(f"/api/v1/admin/users/{beta_support['id']}")).status_code == 404
        assert (await admin.get("/api/v1/admin/clients")).status_code == 403

        assert await login(support, "help@acme.com") == 200
        assert (await support.get("/api/v1/admin/users")).status_code == 403
        assert (
            await admin.patch(f"/api/v1/admin/users/{help_id}", json={"is_active": False})
        ).status_code == 200
        assert (await support.get("/api/v1/auth/me")).status_code == 401

        await admin.put("/api/v1/admin/settings/business-hours", json=HOURS)

    rows = db.execute(
        "SELECT a.action, u.email, c.slug FROM admin_audit_log a "
        "LEFT JOIN users u ON u.id = a.user_id LEFT JOIN clients c ON c.id = a.client_id "
        "ORDER BY a.id"
    ).fetchall()
    assert rows == [
        ("client.create", "root@example.com", "acme"),
        ("client.create", "root@example.com", "beta"),
        ("user.create", "root@example.com", "acme"),
        ("user.create", "root@example.com", "beta"),
        ("user.password_change", "admin@acme.com", "acme"),
        ("user.create", "admin@acme.com", "acme"),
        ("user.deactivate", "admin@acme.com", "acme"),
        ("settings.business_hours_update", "admin@acme.com", "acme"),
    ]

    async with browser(app) as admin:
        await login(admin, "admin@acme.com", ADMIN_PW)
        entries = (await admin.get("/api/v1/admin/audit-log")).json()["entries"]
    assert {e["client_id"] for e in entries} == {acme["id"]}
    assert len(entries) == 6


async def test_own_password_change_signs_out_other_sessions(app, db):
    add_super_admin(db)
    reset_login_failure_limiter()
    async with browser(app) as a, browser(app) as b:
        await login(a, "root@example.com")
        await login(b, "root@example.com")
        # Let a second pass: a token issued in the cutoff's own second is still accepted.
        await asyncio.sleep(1.1)
        r = await a.post(
            "/api/v1/auth/password",
            json={"current_password": PW, "new_password": "Brand-new-pass-1"},
        )
        assert r.status_code == 200
        assert (await a.get("/api/v1/auth/me")).status_code == 200
        assert (await b.get("/api/v1/auth/me")).status_code == 401
        assert await login(b, "root@example.com") == 401
        assert await login(b, "root@example.com", "Brand-new-pass-1") == 200


# ---------------------------------------------------------------- create_super_admin script


def _run_script(database_url, *args, password=PW):
    env = {**os.environ, "DATABASE_URL": database_url, "SUPER_ADMIN_PASSWORD": password}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=120
    )


def test_create_super_admin_script(database_url, db):
    created = _run_script(database_url, "--email", "Boss@Example.com", "--name", "Boss")
    assert created.returncode == 0, created.stderr
    assert db.execute(
        "SELECT email, user_type_id, client_id, must_change_password FROM users"
    ).fetchall() == [("boss@example.com", 1, None, False)], "they chose their own password"

    again = _run_script(database_url, "--email", "boss@example.com", "--name", "Boss")
    assert again.returncode == 1 and "already exists" in again.stdout + again.stderr

    weak = _run_script(database_url, "--email", "x@example.com", "--name", "X", password="short")
    assert weak.returncode == 1 and "at least 12" in weak.stdout + weak.stderr

    db.execute("UPDATE users SET is_active = FALSE")
    reset = _run_script(
        database_url, "--email", "boss@example.com", "--reset-password", password="Second-pass-678"
    )
    assert reset.returncode == 0, reset.stderr
    assert db.execute("SELECT is_active FROM users").fetchone()[0] is True
    actions = [r[0] for r in db.execute("SELECT action FROM admin_audit_log ORDER BY id")]
    assert actions == ["super_admin.create", "super_admin.password_reset"]


async def test_admin_set_password_must_be_changed_before_anything_else(app, db):
    add_super_admin(db)
    async with browser(app) as sa:
        await login(sa, "root@example.com")
        acme = (
            await sa.post("/api/v1/admin/clients", json={"name": "Acme", "slug": "acme"})
        ).json()
        created = (
            await sa.post(
                "/api/v1/admin/users",
                json={
                    "email": "help@acme.com",
                    "full_name": "H",
                    "password": PW,
                    "user_type": "support",
                    "client_id": acme["id"],
                },
            )
        ).json()
        assert created["must_change_password"] is True

    async with browser(app) as support:
        assert await login(support, "help@acme.com") == 200
        assert (await support.get("/api/v1/auth/me")).json()["must_change_password"] is True
        blocked = await support.get("/api/v1/admin/settings/business-hours")
        assert blocked.status_code == 403 and blocked.json()["detail"] == "Password change required"

        r = await support.post(
            "/api/v1/auth/password",
            json={"current_password": PW, "new_password": "My-own-pass-123"},
        )
        assert r.status_code == 200
        assert (await support.get("/api/v1/auth/me")).json()["must_change_password"] is False
        assert (await support.get("/api/v1/admin/settings/business-hours")).status_code == 200

    async with browser(app) as sa:
        await login(sa, "root@example.com")
        r = await sa.patch(
            f"/api/v1/admin/users/{created['id']}", json={"password": "Admin-reset-123"}
        )
        assert r.json()["must_change_password"] is True, "an admin reset asks again"
