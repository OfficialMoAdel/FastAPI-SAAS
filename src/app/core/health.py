"""Health-check library — the canonical check implementations (A-M7).

Consumers: ``modules/health/router.py`` (the canonical HTTP surface for
/health and /ready) and ``api/monitoring.py`` (richer ops endpoints that
delegate their DB probe here). Keeping the probes in core/ and only the
HTTP surface in modules/ preserves the core→modules layering contract.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

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
        result: dict[str, Any] = {"status": self.status}
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

        async def _check() -> None:
            await db.execute(text("SELECT 1"))

        await asyncio.wait_for(_check(), timeout=5.0)
        latency_ms = (time.perf_counter() - start) * 1000
        return HealthResult(status="healthy", latency_ms=latency_ms)
    except TimeoutError:
        LOGGER.exception("Database health check timed out")
        return HealthResult(status="unhealthy", error="Database check timed out. Timeout after 5.0s")
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
        await asyncio.wait_for(redis.ping(), timeout=5.0)
        latency_ms = (time.perf_counter() - start) * 1000
        return HealthResult(status="healthy", latency_ms=latency_ms)
    except TimeoutError:
        LOGGER.exception("Redis health check timed out")
        return HealthResult(status="unhealthy", error="Redis check timed out. Timeout after 5.0s")
    except Exception as e:
        LOGGER.exception(f"Redis health check failed with error: {e}")
        return HealthResult(status="unhealthy", error=f"{type(e).__name__}: {e}")


async def check_redis_queue_health() -> HealthResult:
    """Check Redis queue (ARQ) health for background job processing.

    Returns
    -------
        HealthResult with status, latency, and any error details
    """
    from src.app.core.utils.queue import pool

    if pool is None:
        LOGGER.warning("Redis queue pool not initialized")
        return HealthResult(status="degraded", error="Pool not initialized")

    start = time.perf_counter()
    try:
        # ArqRedis has built-in ping method
        await asyncio.wait_for(pool.ping(), timeout=5.0)
        latency_ms = (time.perf_counter() - start) * 1000
        return HealthResult(status="healthy", latency_ms=latency_ms)
    except TimeoutError:
        LOGGER.exception("Redis queue health check timed out")
        return HealthResult(status="unhealthy", error="Redis queue check timed out. Timeout after 5.0s")
    except Exception as e:
        LOGGER.exception(f"Redis queue health check failed with error: {e}")
        return HealthResult(status="unhealthy", error=f"{type(e).__name__}: {e}")
