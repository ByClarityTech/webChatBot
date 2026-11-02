from typing import Literal

from pydantic import BaseModel


class DependencyStatus(BaseModel):
    status: Literal["up", "down"]
    detail: str | None = None


class HealthResponse(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    api: DependencyStatus
    qdrant: DependencyStatus
    postgres: DependencyStatus
    worker: DependencyStatus
