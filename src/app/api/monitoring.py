"""Monitoring and observability endpoints.

Provides Prometheus metrics export and health check endpoints for monitoring.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import text

from src.app.api.dependencies import get_current_superuser
from src.app.core.config import settings
from src.app.core.db.database import async_engine
from src.app.core.monitoring.r1_unified_metrics import get_r1_metrics

router = APIRouter(dependencies=[Depends(get_current_superuser)])


class HealthCheckResponse(BaseModel):
    """Health check response model."""

    status: str
    timestamp: str
    checks: dict[str, dict[str, str | float | bool]]


class ComponentHealth(BaseModel):
    """Component health status."""

    name: str
    status: str
    message: str | None = None
    metrics: dict[str, float] | None = None


async def check_database() -> dict[str, str | float | bool]:
    """Check database connectivity."""
    start_time = time.perf_counter()
    try:
        async with async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        latency = time.perf_counter() - start_time
        return {
            "status": "healthy",
            "latency_ms": round(latency * 1000, 2),
            "pool_size": settings.DB_POOL_SIZE,
            "max_overflow": settings.DB_MAX_OVERFLOW,
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "latency_ms": round((time.perf_counter() - start_time) * 1000, 2),
        }


async def check_redis() -> dict[str, str | float | bool]:
    """Check Redis connectivity."""
    start_time = time.perf_counter()
    try:
        from src.app.core.utils.cache import client as redis_client

        if redis_client is None:
            return {"status": "disabled", "message": "Redis not configured"}

        await redis_client.ping()
        latency = time.perf_counter() - start_time
        return {"status": "healthy", "latency_ms": round(latency * 1000, 2)}
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "latency_ms": round((time.perf_counter() - start_time) * 1000, 2),
        }


async def check_worker_queue() -> dict[str, str | float | bool]:
    """Check ARQ worker queue status."""
    try:
        from src.app.core.utils.queue import get_queue_size

        queue_size = await get_queue_size()
        return {
            "status": "healthy",
            "queue_size": queue_size,
            "workers": settings.ARQ_WORKERS,
            "max_jobs": settings.ARQ_MAX_JOBS,
        }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e), "queue_size": 0}


async def check_cache_metrics() -> dict[str, str | float | bool]:
    """Check cache hit rates."""
    try:
        metrics = get_r1_metrics()
        xfetch_hits = metrics.get_counter("r1_xfetch_cache_hits_total")
        xfetch_misses = metrics.get_counter("r1_xfetch_cache_misses_total")
        rbac_hits = metrics.get_counter("r1_rbac_cache_hits_total")
        rbac_misses = metrics.get_counter("r1_rbac_cache_misses_total")

        xfetch_total = xfetch_hits + xfetch_misses
        rbac_total = rbac_hits + rbac_misses

        return {
            "status": "healthy",
            "xfetch_hit_rate": round(xfetch_hits / xfetch_total * 100, 2) if xfetch_total > 0 else 0,
            "rbac_hit_rate": round(rbac_hits / rbac_total * 100, 2) if rbac_total > 0 else 0,
        }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


async def check_dlq_size() -> dict[str, str | float | bool]:
    """Check Dead Letter Queue size."""
    try:
        metrics = get_r1_metrics()
        dlq_size = metrics.get_gauge("r1_dlq_size")
        dlq_pct = metrics.get_gauge("r1_dlq_size_pct")

        return {
            "status": "healthy",
            "dlq_size": dlq_size,
            "dlq_size_pct": dlq_pct,
            "threshold_exceeded": dlq_pct > 90,
        }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


@router.get("/health", response_model=HealthCheckResponse)
async def health_check() -> HealthCheckResponse:
    """Health check endpoint.

    Returns overall system health with component-level checks.
    """
    # Run all health checks in parallel
    results = await asyncio.gather(
        check_database(),
        check_redis(),
        check_worker_queue(),
        check_cache_metrics(),
        check_dlq_size(),
        return_exceptions=True,
    )

    keys = ["database", "redis", "worker_queue", "cache_metrics", "dlq_size"]
    checks: dict[str, dict[str, str | float | bool]] = {}

    for key, result in zip(keys, results):
        # M4: Use Exception instead of BaseException to avoid catching KeyboardInterrupt/SystemExit
        if isinstance(result, Exception):
            checks[key] = {"status": "unhealthy", "error": str(result)}
        else:
            # Type narrowing: result is guaranteed to be dict after isinstance check
            checks[key] = result  # type: ignore[assignment]

    # Determine overall status
    overall_status = "ok"
    for check_result in checks.values():
        status = check_result.get("status")
        if status == "unhealthy":
            overall_status = "error"
            break
        elif status != "healthy" and overall_status == "ok":
            overall_status = "degraded"

    return HealthCheckResponse(
        status=overall_status,
        timestamp=datetime.now(UTC).isoformat(),
        checks=checks,
    )


@router.get("/health/live")
async def liveness_probe() -> dict[str, str]:
    """Liveness probe - checks if the application is running."""
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness_probe() -> dict[str, str]:
    """Readiness probe - checks if the application is ready to serve traffic."""
    try:
        db_status = await check_database()
        if db_status.get("status") == "healthy":
            return {"status": "ready"}
        return {"status": "not_ready", "reason": "database_unhealthy"}
    except Exception:
        return {"status": "not_ready", "reason": "health_check_failed"}


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics_export() -> str:
    """Prometheus metrics export endpoint.

    Returns metrics in Prometheus text format.
    """
    try:
        metrics = get_r1_metrics()
        return metrics.export_prometheus()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to export metrics: {e}")


@router.get("/metrics/json", dependencies=[Depends(get_current_superuser)])
async def metrics_json() -> dict[str, Any]:
    """Metrics export in JSON format.

    Returns metrics as a JSON object for easier programmatic access.
    """
    try:
        metrics = get_r1_metrics()
        return metrics.get_all_metrics()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to export metrics: {e}")
