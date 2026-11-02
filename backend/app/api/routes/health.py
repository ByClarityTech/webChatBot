import httpx
import psycopg
from fastapi import APIRouter, Response, status

from app.core.config import get_settings
from app.schemas.health import DependencyStatus, HealthResponse

router = APIRouter(tags=["health"])


async def _check_qdrant(client: httpx.AsyncClient, url: str) -> DependencyStatus:
    try:
        response = await client.get(f"{url.rstrip('/')}/healthz", timeout=3.0)
        if response.status_code == 200:
            return DependencyStatus(status="up")
        return DependencyStatus(status="down", detail=f"HTTP {response.status_code}")
    except Exception as exc:
        return DependencyStatus(status="down", detail=str(exc))


async def _check_postgres(database_url: str) -> DependencyStatus:
    try:
        async with await psycopg.AsyncConnection.connect(database_url) as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                await cur.fetchone()
        return DependencyStatus(status="up")
    except Exception as exc:
        return DependencyStatus(status="down", detail=str(exc))


async def _check_worker(client: httpx.AsyncClient, url: str) -> DependencyStatus:
    try:
        response = await client.get(f"{url.rstrip('/')}/health", timeout=5.0)
        if response.status_code == 200:
            return DependencyStatus(status="up")
        return DependencyStatus(status="down", detail=f"HTTP {response.status_code}")
    except Exception as exc:
        return DependencyStatus(status="down", detail=str(exc))


@router.get("/health", response_model=HealthResponse)
async def health(response: Response) -> HealthResponse:
    settings = get_settings()

    async with httpx.AsyncClient() as client:
        qdrant = await _check_qdrant(client, settings.qdrant_url)
        worker = await _check_worker(client, settings.worker_url)

    postgres = await _check_postgres(settings.database_url)
    api = DependencyStatus(status="up")

    dependencies = [api, qdrant, postgres, worker]
    if all(dep.status == "up" for dep in dependencies):
        overall = "healthy"
        response.status_code = status.HTTP_200_OK
    elif any(dep.status == "up" for dep in dependencies[1:]):
        overall = "degraded"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        overall = "unhealthy"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status=overall,
        api=api,
        qdrant=qdrant,
        postgres=postgres,
        worker=worker,
    )
