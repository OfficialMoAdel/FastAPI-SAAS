import logging
import time
from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

LOGGER = logging.getLogger(__name__)


@dataclass
class HealthResult:
    """Health check result with detailed metrics.

    Attributes
    ----------
        status: "healthy", "degraded", or "unhealthy"
        latency_ms: Response time in milliseconds (None if failed)
        error: Error message if any
    """

    status: str
    latency_ms: float | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON response."""
        result = {"status": self.status}
        if self.latency_ms is not None:
            result["latency_ms"] = round(self.latency_ms, 2)
        if self.error is not None:
            result["error"] = self.error
        return result


async def check_database_health(db: AsyncSession) -> HealthResult:
    """Check database connectivity with latency measurement.

    Returns
    -------
        HealthResult with status, latency, and any error details
    """
    start = time.perf_counter()
    try:
        await db.execute(text("SELECT 1"))
        latency_ms = (time.perf_counter() - start) * 1000
        return HealthResult(status="healthy", latency_ms=latency_ms)
    except OperationalError as e:
        LOGGER.exception(f"Database operational error: {e}")
        return HealthResult(status="unhealthy", error=f"OperationalError: {e}")
    except Exception as e:
        LOGGER.exception(f"Database health check failed with error: {e}")
        return HealthResult(status="unhealthy", error=f"Unexpected: {type(e).__name__}: {e}")


async def check_redis_health(redis: Redis) -> HealthResult:
    """Check Redis connectivity with latency measurement.

    Returns
    -------
        HealthResult with status, latency, and any error details
    """
    start = time.perf_counter()
    try:
        await redis.ping()
        latency_ms = (time.perf_counter() - start) * 1000
        return HealthResult(status="healthy", latency_ms=latency_ms)
    except Exception as e:
        LOGGER.exception(f"Redis health check failed with error: {e}")
        return HealthResult(status="unhealthy", error=f"{type(e).__name__}: {e}")


async def check_redis_queue_health() -> HealthResult:
    """Check Redis queue (ARQ) health for background job processing.

    Returns
    -------
        HealthResult with status, latency, and any error details
    """
    from .utils.queue import pool

    if pool is None:
        LOGGER.warning("Redis queue pool not initialized")
        return HealthResult(status="degraded", error="Pool not initialized")

    start = time.perf_counter()
    try:
        # ArqRedis has built-in ping method
        await pool.ping()
        latency_ms = (time.perf_counter() - start) * 1000
        return HealthResult(status="healthy", latency_ms=latency_ms)
    except Exception as e:
        LOGGER.exception(f"Redis queue health check failed with error: {e}")
        return HealthResult(status="unhealthy", error=f"{type(e).__name__}: {e}")
