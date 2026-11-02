"""Keep tests away from real config, databases and services.

- Settings ignore the repo .env files, so tests never see real API keys or credentials.
- Database, Qdrant and worker URLs point at a closed local port: an unmocked call fails
  fast instead of touching a real service.
- Audit writes are captured in memory (the `audit_rows` fixture) instead of hitting a DB.
- The login lockout starts empty for every test.

Integration tests in tests/integration/ opt back in to a real database via TEST_DATABASE_URL.
"""

import os
import sys

UNREACHABLE = "127.0.0.1:1"
TEST_ENV = {
    "APP_ENV": "development",
    "DATABASE_URL": f"postgresql://test:test@{UNREACHABLE}/test",
    "QDRANT_URL": f"http://{UNREACHABLE}",
    "WORKER_URL": f"http://{UNREACHABLE}",
    "OPENAI_API_KEY": "",
    "GROQ_API_KEY": "",
    "GOOGLE_API_KEY": "",
    "LANGSMITH_TRACING": "false",
    "LANGSMITH_API_KEY": "",
    "GOOGLE_CLIENT_ID": "",
    "GOOGLE_CLIENT_SECRET": "",
    "GOOGLE_TOKEN_ENCRYPTION_KEY": "",
    "ALLOWED_ORIGINS": "http://localhost:3000,http://localhost:8000",
}
os.environ.update(TEST_ENV)

import asyncio  # noqa: E402

import pytest  # noqa: E402
from pytest_asyncio import plugin as pytest_asyncio_plugin  # noqa: E402

from app.core import config  # noqa: E402

# Settings are built from env vars only; the repo-root and backend .env files are skipped.
config.Settings.model_config["env_file"] = None
config.get_settings.cache_clear()

from app.core.rate_limit import (  # noqa: E402
    reset_booking_rate_limiter,
    reset_login_failure_limiter,
)

# psycopg's async mode cannot run on Windows' default Proactor loop, so use a selector loop.
# pytest-asyncio >= 1.4 takes a loop factory hook; older versions use the policy fixture.
if hasattr(pytest_asyncio_plugin, "PytestAsyncioSpecs"):

    @pytest.hookimpl(optionalhook=True)
    def pytest_asyncio_loop_factories(config, item):
        if sys.platform == "win32":
            return {"selector": asyncio.SelectorEventLoop}
        return None

else:

    @pytest.fixture(scope="session")
    def event_loop_policy():
        if sys.platform == "win32":
            return asyncio.WindowsSelectorEventLoopPolicy()
        return asyncio.DefaultEventLoopPolicy()


@pytest.fixture(autouse=True)
def _fresh_limiters():
    reset_login_failure_limiter()
    reset_booking_rate_limiter()
    yield
    reset_login_failure_limiter()
    reset_booking_rate_limiter()


@pytest.fixture(autouse=True)
def audit_rows(monkeypatch, request):
    """Audit inserts recorded here instead of the database (not for integration tests)."""
    rows: list[dict] = []
    if request.node.get_closest_marker("integration"):
        yield rows
        return

    async def record(**row):
        rows.append(row)

    monkeypatch.setattr("app.services.admin_audit.insert_admin_audit_log", record)
    yield rows
