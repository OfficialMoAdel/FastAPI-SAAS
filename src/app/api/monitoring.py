"""Monitoring and observability endpoints.

Provides Prometheus metrics export and health check endpoints for monitoring.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse

from src.app.api.deps import get_current_superuser
from src.app.api.monitoring_schemas import (
    HealthCheckResponse,
    MetricsErrorResponse,
    ProbeCheck,
)
from src.app.core.config import settings
from src.app.core.health import (
    HealthResult,
    collect_readiness_results,
    resolve_readiness_status,
)
from src.app.core.logger import get_logger
from src.app.core.middleware_state import get_request_id
from src.app.core.monitoring.r1_unified_metrics import get_r1_metrics

logger = get_logger(__name__)

router = APIRouter()


def _request_id_of(request: Request | None) -> str | None:
    """The current request ID, or None when called outside a request.

    ``request`` is optional so the probe helpers stay directly callable
    from unit tests; FastAPI always injects it on the HTTP path.
    """
    return get_request_id(request.state) if request is not None else None


def _log_probe_failure(probe: str, error: str, request_id: str | None) -> None:
    """Log the FULL probe failure server-side, correlated by request ID.

    The text is redacted from the response but never discarded: an
    operator diagnoses a degraded probe from the log line carrying the
    request ID the client already holds (echoed on ``X-Request-ID``).
    """
    logger.warning("health_probe_failed", probe=probe, error=error, request_id=request_id)


def _class_name_of(raw: str) -> str:
    """Reduce a probe failure message to a bare exception class name.

    ``core/health`` formats ``HealthResult.error`` as ``"<ClassName>: <text>"``
    (or ``"Unexpected: <ClassName>: <text>"``). Only the leading
    identifier-shaped, capitalised token is published — never the text,
    which carries the failed statement, the DSN, and credentials. Anything
    that is not identifier-shaped (e.g. a timeout sentence) gets a fixed
    label, so this function cannot emit arbitrary message content.
    """
    for token in raw.split(":", maxsplit=2):
        token = token.strip()
        if not token or token == "Unexpected":
            continue
        if token[0].isupper() and token.isidentifier():
            return token
    return "ProbeError"


async def check_database(request: Request | None = None) -> dict[str, str | float | bool]:
    """Check database connectivity.

    Consolidated: delegates the probe to core/health.check_database_health
    (single source of truth with timeout + typed errors) and re-shapes the
    result into this endpoint's richer contract (pool stats included).
    """
    from src.app.core.db.database import local_session
    from src.app.core.health import check_database_health

    async with local_session() as db:
        result = await check_database_health(db)

    payload: dict[str, str | float | bool] = {
        "status": result.status,
        "pool_size": settings.postgres.DB_POOL_SIZE,
        "max_overflow": settings.postgres.DB_MAX_OVERFLOW,
    }
    # Publish the measured elapsed time whenever there is one — including on
    # FAILURE, which is what an alert keyed on "slow then failed" reads. A
    # ``latency_ms: 0`` placeholder cannot be told apart from "measured zero"
    # and silently blanked that signal; when nothing was measured (a probe that
    # never ran, e.g. the degraded queue-pool branch) the field is omitted and
    # ``ProbeCheck.latency_ms`` defaults to None on the wire.
    if result.latency_ms is not None:
        payload["latency_ms"] = round(result.latency_ms, 2)
    if result.error:
        # core/health's `error` embeds the driver message, which for
        # SQLAlchemy/asyncpg carries the statement, the DSN, and the
        # connection user. Publish only the exception class name; the full
        # text goes to the log, correlated by request_id.
        _log_probe_failure("database", result.error, _request_id_of(request))
        payload["error"] = _class_name_of(result.error)
    return payload


async def check_redis(request: Request | None = None) -> dict[str, str | float | bool]:
    """Check Redis connectivity.

    Delegates the probe to ``core/health.check_redis_health`` — the canonical
    implementation — and re-shapes the result into this endpoint's contract.

    Before this, the probe called ``redis_client.ping()`` directly with NO
    ``asyncio.wait_for``. The shared pool's own ``socket_timeout``
    (``REDIS_CACHE_SOCKET_TIMEOUT``, 5 s by default and configurable up to
    30 s) plus ``retry_on_timeout=True`` meant the worst case was a MULTIPLE of
    an operator-tunable timeout, not a fixed budget — so the slowest dependency
    was also the one with the least predictable ceiling, on the endpoint a load
    balancer waits on. ``check_redis_health`` wraps the identical call in
    ``wait_for(..., timeout=5.0)``, which is the budget the sibling probes use.

    The three-state answer is preserved: ``disabled`` when no client is
    installed, ``healthy`` or ``unhealthy`` from the canonical probe.
    """
    from src.app.core.health import check_redis_health
    from src.app.core.redis_provider import get_redis

    redis_client = get_redis()
    if redis_client is None:
        return {"status": "disabled", "message": "Redis not configured"}

    result = await check_redis_health(redis=redis_client)
    payload: dict[str, str | float | bool] = {"status": result.status}
    # Same rule as the database probe: report the measurement when there is one
    # (healthy AND unhealthy), omit the field when the probe never produced one.
    if result.latency_ms is not None:
        payload["latency_ms"] = round(result.latency_ms, 2)
    if result.error:
        # core/health's `error` embeds the redis-py message, which carries the
        # host, port, and username. Publish only the exception class name; the
        # full text goes to the log, correlated by request_id.
        _log_probe_failure("redis", result.error, _request_id_of(request))
        payload["error"] = _class_name_of(result.error)
    return payload


async def check_worker_queue(request: Request | None = None) -> dict[str, str | float | bool]:
    """Check ARQ worker queue status.

    Reports DEPTH plus the configured worker/job ceilings. This answers a
    different question from ``core/health.check_redis_queue_health``, which
    pings to answer LIVENESS — depth is a backlog signal an operator needs and
    ``ProbeCheck`` models deliberately (``queue_size`` / ``workers`` /
    ``max_jobs``). The two probes are therefore kept side by side: swapping this
    one for the ping would drop three published fields and call it a fix.

    What WAS wrong here: ``get_queue_size()`` returns ``0`` when the pool is
    ``None`` (``core/utils/queue.py``), so an UNINITIALISED queue reported
    ``healthy`` with a depth of zero — indistinguishable from "enabled and
    empty". ``check_redis_queue_health`` correctly answers ``degraded`` for the
    same condition, and readiness treats only ``healthy`` as ready. The guard
    below closes that asymmetry without giving up the depth payload.

    An operator who disabled queues on purpose gets ``disabled``, not
    ``degraded``: ``REDIS_QUEUE_ENABLED=False`` is a supported configuration,
    and reporting it as a fault would page someone about a choice they made.
    """
    from src.app.core.utils.queue import get_queue_size, pool

    if pool is None:
        # A missing pool has two very different meanings, and conflating them
        # either hides an outage or invents one.
        if not settings.redis_queue.REDIS_QUEUE_ENABLED:
            return {
                "status": "disabled",
                "message": "Queue disabled by configuration",
                "queue_size": 0,
                "workers": settings.arq.ARQ_WORKERS,
                "max_jobs": settings.arq.ARQ_MAX_JOBS,
            }
        # Enabled but not installed: ARQ is not consuming anything. Not a
        # healthy empty queue. Reported per probe (this endpoint always
        # answers 200; readiness is the surface that turns ``degraded`` into
        # a 503).
        return {
            "status": "degraded",
            "message": "Queue pool not initialized",
            "queue_size": 0,
            "workers": settings.arq.ARQ_WORKERS,
            "max_jobs": settings.arq.ARQ_MAX_JOBS,
        }

    try:
        queue_size = await get_queue_size()
        return {
            "status": "healthy",
            "queue_size": queue_size,
            "workers": settings.arq.ARQ_WORKERS,
            "max_jobs": settings.arq.ARQ_MAX_JOBS,
        }
    except Exception as e:  # noqa: BLE001 - P1-17: probes report degraded, never raise
        _log_probe_failure("worker_queue", str(e), _request_id_of(request))
        return {"status": "unhealthy", "error": type(e).__name__, "queue_size": 0}


async def check_cache_metrics(request: Request | None = None) -> dict[str, str | float | bool]:
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
    except Exception as e:  # noqa: BLE001 - P1-17: probes report degraded, never raise
        _log_probe_failure("cache_metrics", str(e), _request_id_of(request))
        return {"status": "unhealthy", "error": type(e).__name__}


async def check_dlq_size(request: Request | None = None) -> dict[str, str | float | bool]:
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
    except Exception as e:  # noqa: BLE001 - P1-17: probes report degraded, never raise
        _log_probe_failure("dlq_size", str(e), _request_id_of(request))
        return {"status": "unhealthy", "error": type(e).__name__}


@router.get("/health", response_model=HealthCheckResponse, response_model_exclude_unset=True)
async def health_check(request: Request = None) -> HealthCheckResponse:  # type: ignore[assignment]
    """Health check endpoint — DIAGNOSTIC, not the traffic gate.

    Returns overall system health with component-level checks. Every probe
    failure is reported as an exception CLASS NAME only; see
    ``api/monitoring_schemas.ProbeCheck`` for why.

    **Status semantics are deliberately diagnostic:** this route always answers
    HTTP 200 and carries the verdict in the body (``status`` is ``ok`` /
    ``degraded`` / ``error``). It is a reporting surface for an operator or a
    dashboard, not an admission decision.

    **The traffic gate is ``GET /api/v1/ready``** (and its alias
    ``/api/health/ready``), which returns 503 unless EVERY required dependency
    is healthy. A load balancer or Kubernetes readiness probe must be pointed at
    ``/api/v1/ready``: wiring this route into ``readinessProbe`` would keep a
    pod with a dead database in rotation, because the 200 here says "I answered"
    and not "I can serve".

    ``/api/health`` is tenant-exempt (``middleware/tenant.py``) so an
    orchestrator does not need to know a tenant slug, but it is deliberately NOT
    exempt from ``ip_filter`` or ``attack_detection``: it runs five live probes
    and is the most expensive path to serve per byte.
    """
    # Run all health checks in parallel
    results = await asyncio.gather(
        check_database(request),
        check_redis(request),
        check_worker_queue(request),
        check_cache_metrics(request),
        check_dlq_size(request),
        return_exceptions=True,
    )

    keys = ["database", "redis", "worker_queue", "cache_metrics", "dlq_size"]
    checks: dict[str, ProbeCheck] = {}

    for key, result in zip(keys, results, strict=False):
        if isinstance(result, dict):
            # A probe reports only the fields it actually measured, so fields
            # left unset here are absent from the response rather than
            # rendered as a wall of nulls. `model_fields_set` is what the
            # response serializer keys off, so the wire keeps exactly the
            # keys the probe produced.
            checks[key] = ProbeCheck.model_validate({k: v for k, v in result.items() if v is not None})
            continue
        if not isinstance(result, Exception):
            # M4: a non-Exception BaseException (KeyboardInterrupt/SystemExit)
            # is not a probe outcome — propagate it instead of swallowing it.
            raise result
        # Full text server-side; the client gets the class name, which is
        # what tells an operator WHICH dependency failed.
        _log_probe_failure(key, str(result), _request_id_of(request))
        checks[key] = ProbeCheck(status="unhealthy", error=type(result).__name__)

    # Determine overall status
    overall_status = "ok"
    for check_result in checks.values():
        status = check_result.status
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


async def _collect_readiness_results() -> tuple[HealthResult, HealthResult, HealthResult, bool]:
    """Gather the readiness inputs for the compatibility alias.

    Delegates to ``core.health.collect_readiness_results`` — the same
    collector the canonical ``/api/v1/ready`` surface uses — so the two
    surfaces cannot disagree about the current state of the system. The
    only difference left is the envelope: this alias answers
    ``{"status": "ready"}`` / ``{"status": "not_ready", ...}`` where the
    canonical surface answers the full ``ReadyCheck`` body.
    """
    return await collect_readiness_results()


@router.get("/health/ready", response_model=None)
async def readiness_probe(request: Request = None) -> dict[str, str] | JSONResponse:  # type: ignore[assignment]
    """Compatibility alias for the canonical readiness dependency set."""
    try:
        database, redis, queue, events_ready = await _collect_readiness_results()
        overall_status = resolve_readiness_status(database, redis, queue, events_ready)
        if overall_status == "healthy":
            return {"status": "ready"}
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "reason": overall_status,
                # Statuses only. The HealthResult.error text is deliberately
                # absent: it embeds the driver message (DSN, credentials).
                "checks": {
                    "database": database.status,
                    "redis": redis.status,
                    "queue": queue.status,
                    "events": "healthy" if events_ready else "unhealthy",
                },
            },
        )
    except Exception as e:  # noqa: BLE001 - readiness reports not_ready, never raises
        logger.warning("readiness_probe_failed", error=str(e), request_id=_request_id_of(request))
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": "health_check_failed"})


def _metrics_error_response(exc: Exception, request: Request | None) -> JSONResponse:
    """Build the operator-only failure envelope for the metrics routes.

    Unlike the probes, this route is gated on ``get_current_superuser`` and
    the operator scraping it is the person who needs the raw driver text,
    so ``error_detail`` carries it in full. It is returned as a
    ``JSONResponse`` rather than raised as ``HTTPException`` because the
    framework handler renders ``exc.detail`` verbatim into the client
    body — the old ``detail=f"Failed to export metrics: {e}"`` leaked the
    text while the adjacent comment claimed the route "never leaks
    internals". The comment and the code disagreed; the code is now
    explicit about carrying the text and about WHY that is acceptable here.
    """
    request_id = _request_id_of(request)
    logger.warning("metrics_export_failed", error=str(exc), request_id=request_id)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=MetricsErrorResponse(
            error_type=type(exc).__name__,
            error_detail=str(exc),
            request_id=request_id,
        ).model_dump(),
    )


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    response_model=None,
    dependencies=[Depends(get_current_superuser)],
)
async def metrics_export(request: Request = None) -> str | JSONResponse:  # type: ignore[assignment]
    """Prometheus metrics export endpoint (superuser-gated).

    Returns metrics in Prometheus text format. An export failure returns a
    500 whose ``error_detail`` holds the FULL exception text — deliberate,
    and safe only because of the superuser gate; see
    :func:`_metrics_error_response`.
    """
    try:
        metrics = get_r1_metrics()
        return metrics.export_prometheus()
    except Exception as e:  # noqa: BLE001 - P1-17: any export failure maps to 500
        return _metrics_error_response(e, request)


@router.get("/metrics/json", response_model=None, dependencies=[Depends(get_current_superuser)])
async def metrics_json(request: Request = None) -> dict[str, Any] | JSONResponse:  # type: ignore[assignment]
    """Metrics export in JSON format (superuser-gated).

    Returns metrics as a JSON object for easier programmatic access. On
    failure the envelope mirrors ``/metrics``: full text under
    ``error_detail``, reachable only by a superuser.
    """
    try:
        metrics = get_r1_metrics()
        return metrics.get_all_metrics()
    except Exception as e:  # noqa: BLE001 - P1-17: any export failure maps to 500
        return _metrics_error_response(e, request)


__all__ = [
    "HealthCheckResponse",
    "MetricsErrorResponse",
    "ProbeCheck",
    "check_cache_metrics",
    "check_database",
    "check_dlq_size",
    "check_redis",
    "check_worker_queue",
    "health_check",
    "liveness_probe",
    "metrics_export",
    "metrics_json",
    "readiness_probe",
    "router",
]
