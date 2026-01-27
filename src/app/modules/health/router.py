import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import settings
from ...core.db.database import async_get_db
from ...core.health import check_database_health, check_redis_health
from ...core.schemas import HealthCheck, ReadyCheck
from ...core.utils.cache import async_get_redis

router = APIRouter(tags=["health"])

STATUS_HEALTHY = "healthy"
STATUS_UNHEALTHY = "unhealthy"

LOGGER = logging.getLogger(__name__)


@router.get("/health", response_model=HealthCheck)
async def health() -> JSONResponse:
    http_status = status.HTTP_200_OK
    response = {
        "status": STATUS_HEALTHY,
        "environment": settings.ENVIRONMENT.value,
        "version": settings.APP_VERSION,
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
    }

    return JSONResponse(status_code=http_status, content=response)


@router.get("/ready", response_model=ReadyCheck)
async def ready(
    redis: Annotated[Redis, Depends(async_get_redis)], db: Annotated[AsyncSession, Depends(async_get_db)]
) -> JSONResponse:
    from ...core.events import event_bus
    from ...core.health import check_redis_queue_health

    database_result = await check_database_health(db=db)
    LOGGER.debug(f"Database health check status: {database_result}")
    redis_result = await check_redis_health(redis=redis)
    LOGGER.debug(f"Redis health check status: {redis_result}")
    queue_result = await check_redis_queue_health()
    LOGGER.debug(f"Redis queue health check status: {queue_result}")
    events_status = event_bus.is_ready
    LOGGER.debug(f"Event bus readiness status: {events_status}")

    is_database_healthy = database_result.status == "healthy"
    is_redis_healthy = redis_result.status == "healthy"
    is_queue_healthy = queue_result.status == "healthy"

    overall_status = (
        STATUS_HEALTHY
        if (is_database_healthy and is_redis_healthy and is_queue_healthy and events_status)
        else STATUS_UNHEALTHY
    )
    http_status = status.HTTP_200_OK if overall_status == STATUS_HEALTHY else status.HTTP_503_SERVICE_UNAVAILABLE

    response = {
        "status": overall_status,
        "environment": settings.ENVIRONMENT.value,
        "version": settings.APP_VERSION,
        "app": STATUS_HEALTHY,
        "database": database_result.status,
        "redis": redis_result.status,
        "queue": queue_result.status,
        "events": STATUS_HEALTHY if events_status else STATUS_UNHEALTHY,
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
    }

    return JSONResponse(status_code=http_status, content=response)
