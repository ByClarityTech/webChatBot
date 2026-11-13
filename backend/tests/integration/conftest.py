"""Real-PostgreSQL tests. Skipped unless TEST_DATABASE_URL is set.

TEST_DATABASE_URL must point at a server where the user may CREATE DATABASE, for example
postgresql://postgres:postgres@localhost:5432/postgres. Each run creates a throwaway database
(webchatbot_it_<random>), runs the migrations, and drops it afterwards; the database named in
the URL itself is never written to.
"""

import os
import secrets

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.core.config import get_settings
from app.db.init import run_migrations

# Test modules here set `pytestmark = pytest.mark.integration` (a conftest-level pytestmark
# does not apply to tests); the marker also turns off the root audit stub.
TRUNCATE_TABLES = (
    "pending_knowledge_updates",
    "crawled_pages",
    "crawl_runs",
    "chat_threads",
    "google_tokens",
    "oauth_pending_states",
    "faqs",
    "knowledge_sources",
    "admin_audit_log",
    "users",
    "clients",
    "bookings",
    "business_hours_settings",
)


def pytest_collection_modifyitems(config, items):
    if os.environ.get("TEST_DATABASE_URL"):
        return
    skip = pytest.mark.skip(reason="set TEST_DATABASE_URL to run integration tests")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


def _as_url(conninfo: str) -> str:
    p = conninfo_to_dict(conninfo)
    auth = p.get("user", "")
    if p.get("password"):
        auth += f":{p['password']}"
    return f"postgresql://{auth}@{p.get('host', 'localhost')}:{p.get('port', 5432)}/{p['dbname']}"


@pytest.fixture(scope="session")
def database_url():
    server = os.environ.get("TEST_DATABASE_URL")
    if not server:
        pytest.skip("set TEST_DATABASE_URL to run integration tests")
    name = f"webchatbot_it_{secrets.token_hex(4)}"
    with psycopg.connect(server, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = _as_url(make_conninfo(server, dbname=name))

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    try:
        run_migrations()
        yield url
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()
        with psycopg.connect(server, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def db(database_url):
    """A clean database for each test; yields an autocommit connection for setup and checks."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(f"TRUNCATE {', '.join(TRUNCATE_TABLES)} RESTART IDENTITY CASCADE")
        yield conn
