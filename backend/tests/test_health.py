from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.health import router
from app.schemas.health import DependencyStatus


@pytest.fixture
def app():
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api/v1")
    return test_app


@pytest.mark.asyncio
async def test_health_all_up(app):
    with (
        patch("app.api.routes.health._check_qdrant", new_callable=AsyncMock) as mock_qdrant,
        patch("app.api.routes.health._check_postgres", new_callable=AsyncMock) as mock_postgres,
        patch("app.api.routes.health._check_worker", new_callable=AsyncMock) as mock_worker,
    ):
        mock_qdrant.return_value = DependencyStatus(status="up")
        mock_postgres.return_value = DependencyStatus(status="up")
        mock_worker.return_value = DependencyStatus(status="up")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "healthy"


@pytest.mark.asyncio
async def test_health_degraded_when_dependency_down(app):
    with (
        patch("app.api.routes.health._check_qdrant", new_callable=AsyncMock) as mock_qdrant,
        patch("app.api.routes.health._check_postgres", new_callable=AsyncMock) as mock_postgres,
        patch("app.api.routes.health._check_worker", new_callable=AsyncMock) as mock_worker,
    ):
        mock_qdrant.return_value = DependencyStatus(status="down", detail="connection refused")
        mock_postgres.return_value = DependencyStatus(status="up")
        mock_worker.return_value = DependencyStatus(status="up")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
