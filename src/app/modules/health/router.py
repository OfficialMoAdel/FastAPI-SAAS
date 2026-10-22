"""Health endpoints — the canonical HTTP health surface (A-M7).

The check implementations live in ``src/app/core/health.py`` (the canonical
check library; also delegated to by api/monitoring.py's richer ops
endpoints). This module owns only the HTTP surface: /health and /ready.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import settings
from src.app.core.db.database import async_get_db
from src.app.core.health import (
    collect_readiness_results,
    resolve_readiness_status,
)
from src.app.core.schemas import HealthCheck, ReadyCheck
from src.app.core.utils.cache import async_get_redis

router = APIRouter(tags=["health"])

STATUS_HEALTHY = "healthy"
STATUS_UNHEALTHY = "unhealthy"

ReadinessStatus = Literal["healthy", "degraded", "unhealthy"]

LOGGER = logging.getLogger(__name__)


@router.get("/health", response_model=HealthCheck)
async def health() -> HealthCheck:
    return HealthCheck(
        status=cast(ReadinessStatus, STATUS_HEALTHY),
        environment=settings.env.ENVIRONMENT.value,
        version=settings.app.APP_VERSION,
        timestamp=datetime.now(UTC).isoformat(timespec="seconds"),
    )


@router.get("/ready", response_model=ReadyCheck)
async def ready(
    response: Response,
    redis: Annotated[Redis, Depends(async_get_redis)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> ReadyCheck:
    database_result, redis_result, queue_result, events_ready = await collect_readiness_results(
        db=db, redis=redis
    )
    overall_status = resolve_readiness_status(
        database=database_result,
        redis=redis_result,
        queue=queue_result,
        events_ready=events_ready,
    )
    response.status_code = (
        status.HTTP_200_OK if overall_status == STATUS_HEALTHY else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return ReadyCheck(
        status=cast(ReadinessStatus, overall_status),
        environment=settings.env.ENVIRONMENT.value,
        version=settings.app.APP_VERSION,
        app=STATUS_HEALTHY,
        database=database_result.status,
        redis=redis_result.status,
        queue=queue_result.status,
        events=STATUS_HEALTHY if events_ready else STATUS_UNHEALTHY,
        timestamp=datetime.now(UTC).isoformat(timespec="seconds"),
    )
