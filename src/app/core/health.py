"""Health-check library — the canonical check implementations (A-M7).

Consumers: ``modules/health/router.py`` (the canonical HTTP surface for
/health and /ready) and ``api/monitoring.py`` (compatibility readiness alias
plus richer ops endpoints that delegate their probes here). Keeping the probes
and readiness policy in core/ preserves the core→modules layering contract.
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


def resolve_readiness_status(
    database: HealthResult,
    redis: HealthResult,
    queue: HealthResult,
    events_ready: bool,
) -> str:
    """Resolve one readiness answer from all required runtime dependencies.

    ``healthy`` is the only ready state. A non-database dependency failure is
    ``degraded`` in the body but must still return HTTP 503 at the route.
    """
    if database.status == "healthy" and redis.status == "healthy" and queue.status == "healthy" and events_ready:
        return "healthy"
    if database.status == "unhealthy":
        return "unhealthy"
    return "degraded"


def _elapsed_ms(start: float) -> float:
    """Milliseconds since ``start``, for BOTH probe outcomes.

    A failed probe used to report no latency at all, so the published
    ``latency_ms`` became ``0`` on every failure and an alert keyed on "slow
    then failed" could not fire. The elapsed time is measured either way; only
    the STATUS differs.
    """
    return (time.perf_counter() - start) * 1000


async def collect_readiness_results(
    *,
    db: AsyncSession | None = None,
    redis: Redis | None = None,
) -> tuple[HealthResult, HealthResult, HealthResult, bool]:
    """Collect the readiness results for the canonical dependency set.

    This is the ONE place the four readiness inputs are gathered, so the
    canonical HTTP surface (``modules/health/router.py``) and the
    compatibility alias (``api/monitoring.py``) cannot disagree about
    what the system currently looks like.

    ``db`` and ``redis`` may be supplied by the caller — the request-scoped
    path injects both. When they are omitted, this opens its own session
    and resolves the shared Redis client, which is what a probe outside a
    request (the compatibility alias, an operator script) needs.

    The three probes run CONCURRENTLY. They are independent, each carries its
    own five-second timeout, and the worst case used to be the SUM of them
    (15s on a ``/ready`` that a load balancer is waiting on) even when only one
    dependency was slow. Return order stays
    ``(database, redis, queue, events_ready)`` — the two route consumers unpack
    positionally — and the identity of the answer does not depend on which
    probe finished first.

    ``return_exceptions=True`` is what keeps the contract "one slow dependency
    must not blank the whole page": without it the first probe to escape its
    own handler would unwind the gather and the sibling results — already
    computed — would be discarded. Every escaped exception is turned into that
    dependency's own failure result below, so ``/ready`` still answers per
    dependency instead of 500-ing.
    """
    from src.app.core.events import event_bus

    results = await asyncio.gather(
        _database_probe(db),
        _redis_probe(redis),
        check_redis_queue_health(),
        return_exceptions=True,
    )
    database_result = _settled(results[0])
    redis_result = _settled(results[1])
    queue_result = _settled(results[2])
    return database_result, redis_result, queue_result, event_bus.is_ready


def _settled(result: HealthResult | BaseException) -> HealthResult:
    """A probe's settled result, with an escaped exception read as unhealthy.

    The probes catch their own errors; this covers a failure OUTSIDE their
    ``try`` — opening the probe's session, resolving the Redis client. Mapping
    it here (rather than letting it propagate) is what makes the per-dependency
    answer total: ``/ready`` reports WHICH dependency broke even when the break
    happened before the check could run.
    """
    if isinstance(result, HealthResult):
        return result
    return HealthResult(status="unhealthy", error=f"Probe crashed: {type(result).__name__}: {result}")


async def _database_probe(db: AsyncSession | None) -> HealthResult:
    """``check_database_health`` over the caller's session, or over a new one."""
    if db is None:
        from src.app.core.db.database import local_session

        async with local_session() as session:
            return await check_database_health(db=session)
    return await check_database_health(db=db)


async def _redis_probe(redis: Redis | None) -> HealthResult:
    """``check_redis_health`` over the caller's client, or over the shared one."""
    if redis is None:
        from src.app.core.redis_provider import get_redis

        redis_client = get_redis()
        if redis_client is None:
            return HealthResult(status="degraded", error="Redis not configured")
        return await check_redis_health(redis=redis_client)
    return await check_redis_health(redis=redis)


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
        return HealthResult(status="healthy", latency_ms=_elapsed_ms(start))
    except TimeoutError:
        LOGGER.exception("Database health check timed out")
        return HealthResult(
            status="unhealthy",
            latency_ms=_elapsed_ms(start),
            error="Database check timed out. Timeout after 5.0s",
        )
    except OperationalError as e:
        LOGGER.exception(f"Database operational error: {e}")
        return HealthResult(status="unhealthy", latency_ms=_elapsed_ms(start), error=f"OperationalError: {e}")
    except Exception as e:
        LOGGER.exception(f"Database health check failed with error: {e}")
        return HealthResult(
            status="unhealthy",
            latency_ms=_elapsed_ms(start),
            error=f"Unexpected: {type(e).__name__}: {e}",
        )


async def check_redis_health(redis: Redis) -> HealthResult:
    """Check Redis connectivity with latency measurement.

    Returns
    -------
        HealthResult with status, latency, and any error details
    """
    start = time.perf_counter()
    try:
        await asyncio.wait_for(redis.ping(), timeout=5.0)
        return HealthResult(status="healthy", latency_ms=_elapsed_ms(start))
    except TimeoutError:
        LOGGER.exception("Redis health check timed out")
        return HealthResult(
            status="unhealthy",
            latency_ms=_elapsed_ms(start),
            error="Redis check timed out. Timeout after 5.0s",
        )
    except Exception as e:
        LOGGER.exception(f"Redis health check failed with error: {e}")
        return HealthResult(status="unhealthy", latency_ms=_elapsed_ms(start), error=f"{type(e).__name__}: {e}")


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
        return HealthResult(status="healthy", latency_ms=_elapsed_ms(start))
    except TimeoutError:
        LOGGER.exception("Redis queue health check timed out")
        return HealthResult(
            status="unhealthy",
            latency_ms=_elapsed_ms(start),
            error="Redis queue check timed out. Timeout after 5.0s",
        )
    except Exception as e:
        LOGGER.exception(f"Redis queue health check failed with error: {e}")
        return HealthResult(status="unhealthy", latency_ms=_elapsed_ms(start), error=f"{type(e).__name__}: {e}")
