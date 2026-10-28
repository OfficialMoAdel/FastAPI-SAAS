import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.responses import JSONResponse

from src.app.api.monitoring import (
    check_cache_metrics,
    check_database,
    check_dlq_size,
    check_redis,
    check_worker_queue,
    health_check,
    liveness_probe,
    metrics_export,
    metrics_json,
    readiness_probe,
)
from src.app.core.config import settings
from src.app.core.health import HealthResult


@pytest.mark.asyncio
async def test_check_database_healthy() -> None:
    mock_conn = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_engine = MagicMock()
    mock_engine.connect.return_value = mock_ctx

    # Consolidated probe: patch the core.health delegate instead of engine.
    from src.app.core.health import HealthResult

    with (
        patch("src.app.core.db.database.local_session") as mock_session_factory,
        patch("src.app.core.health.check_database_health") as mock_probe,
    ):
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session
        mock_probe.return_value = HealthResult(status="healthy", latency_ms=1.2)

        res = await check_database()
        assert res["status"] == "healthy"
        assert "latency_ms" in res
        assert "pool_size" in res


@pytest.mark.asyncio
async def test_check_database_unhealthy() -> None:
    mock_engine = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.side_effect = Exception("DB down")
    mock_engine.connect.return_value = mock_ctx

    from src.app.core.health import HealthResult

    with (
        patch("src.app.core.db.database.local_session") as mock_session_factory,
        patch("src.app.core.health.check_database_health") as mock_probe,
    ):
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session
        mock_probe.return_value = HealthResult(status="unhealthy", error="DB down")

        res = await check_database()
        assert res["status"] == "unhealthy"
        assert "error" in res


@pytest.mark.asyncio
async def test_check_redis_healthy() -> None:
    mock_redis = AsyncMock()
    with patch("src.app.core.utils.cache.client", mock_redis):
        res = await check_redis()
        assert res["status"] == "healthy"
        assert "latency_ms" in res


@pytest.mark.asyncio
async def test_check_redis_bounds_the_ping_with_the_canonical_timeout() -> None:
    """The probe must delegate to core/health, which wraps the ping in wait_for.

    A bare ``await redis_client.ping()`` was bounded only by the pool's
    socket_timeout times the retry count — an operator-tunable ceiling (5s
    default, 30s max) rather than a fixed budget. Pinning the delegate keeps
    the one timeout policy; the sibling probes already use it.
    """
    mock_redis = AsyncMock()
    with (
        patch("src.app.core.utils.cache.client", mock_redis),
        patch("src.app.core.health.check_redis_health", new_callable=AsyncMock) as probe,
    ):
        probe.return_value = HealthResult(status="healthy", latency_ms=2.5)
        res = await check_redis()

        probe.assert_awaited_once_with(redis=mock_redis)
        assert res["status"] == "healthy"
        assert res["latency_ms"] == 2.5


@pytest.mark.asyncio
async def test_check_redis_publishes_only_the_class_name() -> None:
    """A failing probe must not put driver text (host/port/user) on the wire.

    core/health formats ``error`` as ``"<ClassName>: <text>"``; this endpoint
    publishes the leading identifier only and logs the rest.
    """
    mock_redis = AsyncMock()
    with (
        patch("src.app.core.utils.cache.client", mock_redis),
        patch("src.app.core.health.check_redis_health", new_callable=AsyncMock) as probe,
        patch("src.app.api.monitoring._log_probe_failure") as logged,
    ):
        probe.return_value = HealthResult(
            status="unhealthy", error="ConnectionError: redis://user:secret@cache:6379"
        )
        res = await check_redis()

    assert res["status"] == "unhealthy"
    assert res["error"] == "ConnectionError"
    assert "secret" not in json.dumps(res)
    logged.assert_called_once()


@pytest.mark.asyncio
async def test_check_redis_unhealthy() -> None:
    mock_redis = AsyncMock()
    mock_redis.ping.side_effect = Exception("Redis error")
    with patch("src.app.core.utils.cache.client", mock_redis):
        res = await check_redis()
        assert res["status"] == "unhealthy"
        assert "error" in res


@pytest.mark.asyncio
async def test_check_redis_disabled() -> None:
    with patch("src.app.core.utils.cache.client", None):
        res = await check_redis()
        assert res["status"] == "disabled"


@pytest.mark.asyncio
async def test_check_worker_queue_healthy() -> None:
    with patch("src.app.core.utils.queue.get_queue_size", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = 5
        res = await check_worker_queue()
        assert res["status"] == "healthy"
        assert res["queue_size"] == 5


@pytest.mark.asyncio
async def test_check_worker_queue_unhealthy() -> None:
    with patch("src.app.core.utils.queue.get_queue_size", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = Exception("Queue error")
        res = await check_worker_queue()
        assert res["status"] == "unhealthy"


@pytest.mark.asyncio
async def test_check_worker_queue_without_a_pool_is_degraded_not_healthy() -> None:
    """An uninitialised queue must not read as a healthy empty queue.

    ``get_queue_size()`` returns 0 when the pool is None, so the pre-fix probe
    answered ``healthy, queue_size: 0`` — indistinguishable from "enabled and
    idle", while nothing was consuming jobs at all. ``check_redis_queue_health``
    already answers ``degraded`` for the same condition.

    The autouse Redis fixture installs a fake pool, so the pool MUST be forced
    to None here for this branch to be reachable at all.
    """
    import src.app.core.utils.queue as queue_module

    with (
        patch.object(queue_module, "pool", None),
        patch.object(settings.redis_queue, "REDIS_QUEUE_ENABLED", True),
    ):
        res = await check_worker_queue()

    assert res["status"] == "degraded"
    assert res["message"] == "Queue pool not initialized"
    # The depth/window fields stay published — degraded is not a field purge.
    assert res["queue_size"] == 0
    assert "workers" in res
    assert "max_jobs" in res


@pytest.mark.asyncio
async def test_check_worker_queue_disabled_by_config_is_not_a_fault() -> None:
    """``REDIS_QUEUE_ENABLED=False`` is a supported choice, not a degradation.

    An operator who runs the app without ARQ must not be paged about it, so a
    disabled queue answers ``disabled`` — matching what ``check_redis`` returns
    when there is no client. Reporting it ``degraded`` would make the diagnostic
    route cry wolf on a deliberate configuration.
    """
    import src.app.core.utils.queue as queue_module

    with (
        patch.object(queue_module, "pool", None),
        patch.object(settings.redis_queue, "REDIS_QUEUE_ENABLED", False),
    ):
        res = await check_worker_queue()

    assert res["status"] == "disabled"
    assert "configuration" in str(res["message"]).lower()
    assert res["queue_size"] == 0


@pytest.mark.asyncio
async def test_check_worker_queue_keeps_the_depth_payload() -> None:
    """Depth is the ops signal this endpoint owns; readiness owns liveness.

    Guards against "fixing" the deque probe by replacing it with the ping in
    core/health, which would silently drop queue_size/workers/max_jobs.
    """
    with patch("src.app.core.utils.queue.get_queue_size", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = 42
        res = await check_worker_queue()

    assert res == {
        "status": "healthy",
        "queue_size": 42,
        "workers": res["workers"],
        "max_jobs": res["max_jobs"],
    }


@pytest.mark.asyncio
async def test_check_cache_metrics_healthy() -> None:
    mock_metrics = MagicMock()
    mock_metrics.get_counter.side_effect = lambda name: {
        "r1_xfetch_cache_hits_total": 10,
        "r1_xfetch_cache_misses_total": 5,
        "r1_rbac_cache_hits_total": 20,
        "r1_rbac_cache_misses_total": 5,
    }.get(name, 0)

    with patch("src.app.api.monitoring.get_r1_metrics", return_value=mock_metrics):
        res = await check_cache_metrics()
        assert res["status"] == "healthy"
        # 10 hits, 15 total => 66.67%
        # 20 hits, 25 total => 80.0%
        assert res["xfetch_hit_rate"] == 66.67
        assert res["rbac_hit_rate"] == 80.0


@pytest.mark.asyncio
async def test_check_cache_metrics_unhealthy() -> None:
    with patch("src.app.api.monitoring.get_r1_metrics", side_effect=Exception("Metrics error")):
        res = await check_cache_metrics()
        assert res["status"] == "unhealthy"


@pytest.mark.asyncio
async def test_check_dlq_size_healthy() -> None:
    mock_metrics = MagicMock()
    mock_metrics.get_gauge.side_effect = lambda name: {
        "r1_dlq_size": 10,
        "r1_dlq_size_pct": 5.0,
    }.get(name, 0)
    with patch("src.app.api.monitoring.get_r1_metrics", return_value=mock_metrics):
        res = await check_dlq_size()
        assert res["status"] == "healthy"
        assert res["threshold_exceeded"] is False


@pytest.mark.asyncio
async def test_check_dlq_size_unhealthy() -> None:
    with patch("src.app.api.monitoring.get_r1_metrics", side_effect=Exception("Metrics error")):
        res = await check_dlq_size()
        assert res["status"] == "unhealthy"


@pytest.mark.asyncio
async def test_health_check_all_healthy() -> None:
    with (
        patch("src.app.api.monitoring.check_database", new_callable=AsyncMock, return_value={"status": "healthy"}),
        patch("src.app.api.monitoring.check_redis", new_callable=AsyncMock, return_value={"status": "healthy"}),
        patch("src.app.api.monitoring.check_worker_queue", new_callable=AsyncMock, return_value={"status": "healthy"}),
        patch(
            "src.app.api.monitoring.check_cache_metrics",
            new_callable=AsyncMock,
            return_value={"status": "healthy"},
        ),
        patch("src.app.api.monitoring.check_dlq_size", new_callable=AsyncMock, return_value={"status": "healthy"}),
    ):
        res = await health_check()
        assert res.status == "ok"


@pytest.mark.asyncio
async def test_health_check_one_unhealthy() -> None:
    with (
        patch("src.app.api.monitoring.check_database", new_callable=AsyncMock, return_value={"status": "healthy"}),
        patch("src.app.api.monitoring.check_redis", new_callable=AsyncMock, return_value={"status": "healthy"}),
        patch(
            "src.app.api.monitoring.check_worker_queue", new_callable=AsyncMock, return_value={"status": "unhealthy"}
        ),
        patch(
            "src.app.api.monitoring.check_cache_metrics",
            new_callable=AsyncMock,
            return_value={"status": "healthy"},
        ),
        patch("src.app.api.monitoring.check_dlq_size", new_callable=AsyncMock, side_effect=Exception("Fail")),
    ):
        res = await health_check()
        assert res.status == "error"


@pytest.mark.asyncio
async def test_health_check_degraded() -> None:
    with (
        patch("src.app.api.monitoring.check_database", new_callable=AsyncMock, return_value={"status": "healthy"}),
        patch("src.app.api.monitoring.check_redis", new_callable=AsyncMock, return_value={"status": "disabled"}),
        patch("src.app.api.monitoring.check_worker_queue", new_callable=AsyncMock, return_value={"status": "healthy"}),
        patch(
            "src.app.api.monitoring.check_cache_metrics",
            new_callable=AsyncMock,
            return_value={"status": "healthy"},
        ),
        patch("src.app.api.monitoring.check_dlq_size", new_callable=AsyncMock, return_value={"status": "healthy"}),
    ):
        res = await health_check()
        assert res.status == "degraded"


@pytest.mark.asyncio
async def test_liveness_probe() -> None:
    res = await liveness_probe()
    assert res == {"status": "alive"}


@pytest.mark.asyncio
async def test_readiness_probe_ready() -> None:
    healthy = HealthResult(status="healthy")
    with patch(
        "src.app.api.monitoring._collect_readiness_results",
        new=AsyncMock(return_value=(healthy, healthy, healthy, True)),
    ):
        res = await readiness_probe()
        assert res == {"status": "ready"}


@pytest.mark.asyncio
async def test_readiness_probe_not_ready() -> None:
    database = HealthResult(status="unhealthy")
    healthy = HealthResult(status="healthy")
    with patch(
        "src.app.api.monitoring._collect_readiness_results",
        new=AsyncMock(return_value=(database, healthy, healthy, True)),
    ):
        res = await readiness_probe()
        assert isinstance(res, JSONResponse)
        assert res.status_code == 503
        assert json.loads(bytes(res.body)) == {
            "status": "not_ready",
            "reason": "unhealthy",
            "checks": {"database": "unhealthy", "redis": "healthy", "queue": "healthy", "events": "healthy"},
        }


@pytest.mark.asyncio
async def test_readiness_probe_exception() -> None:
    with patch(
        "src.app.api.monitoring._collect_readiness_results",
        new=AsyncMock(side_effect=Exception("probe failed")),
    ):
        res = await readiness_probe()
        assert isinstance(res, JSONResponse)
        assert res.status_code == 503
        assert json.loads(bytes(res.body)) == {"status": "not_ready", "reason": "health_check_failed"}


@pytest.mark.asyncio
async def test_metrics_export() -> None:
    mock_metrics = MagicMock()
    mock_metrics.export_prometheus.return_value = "metric 1\n"
    with patch("src.app.api.monitoring.get_r1_metrics", return_value=mock_metrics):
        res = await metrics_export()
        assert res == "metric 1\n"


@pytest.mark.asyncio
async def test_metrics_export_exception() -> None:
    """An export failure returns the operator envelope, it does not raise.

    The route used to raise ``HTTPException(detail=f"...: {e}")``, which the
    framework handler renders verbatim into the client body — raw exception
    text on a route whose own comment claimed it "never leaks internals".
    It now returns the envelope explicitly, so the text is present by
    decision (superuser-gated) rather than by accident.
    """
    with patch("src.app.api.monitoring.get_r1_metrics", side_effect=Exception("export blew up")):
        res = await metrics_export()

    assert isinstance(res, JSONResponse)
    assert res.status_code == 500
    assert json.loads(bytes(res.body)) == {
        "status": "error",
        "error_type": "Exception",
        "error_detail": "export blew up",
        "request_id": None,
    }


@pytest.mark.asyncio
async def test_metrics_json() -> None:
    mock_metrics = MagicMock()
    mock_metrics.get_all_metrics.return_value = {"metric": 1}
    with patch("src.app.api.monitoring.get_r1_metrics", return_value=mock_metrics):
        res = await metrics_json()
        assert res == {"metric": 1}


@pytest.mark.asyncio
async def test_metrics_json_exception() -> None:
    """/metrics/json mirrors the same envelope on failure."""
    with patch("src.app.api.monitoring.get_r1_metrics", side_effect=Exception("json blew up")):
        res = await metrics_json()

    assert isinstance(res, JSONResponse)
    assert res.status_code == 500
    assert json.loads(bytes(res.body)) == {
        "status": "error",
        "error_type": "Exception",
        "error_detail": "json blew up",
        "request_id": None,
    }


class TestDiagnosticVersusGatingSurface:
    """``/api/health`` reports; ``/api/v1/ready`` gates.

    The distinction is operational, not cosmetic: wiring ``/api/health`` into a
    Kubernetes ``readinessProbe`` would keep a pod with a dead database in
    rotation, because it answers 200 and puts the verdict in the body. These
    tests pin the asymmetry so a future "consistency" refactor cannot quietly
    turn the diagnostic route into a gate or vice versa.
    """

    @pytest.mark.asyncio
    async def test_the_diagnostic_route_reports_error_inside_a_200(self) -> None:
        with (
            patch(
                "src.app.api.monitoring.check_database",
                new_callable=AsyncMock,
                return_value={"status": "unhealthy", "error": "OperationalError"},
            ),
            patch("src.app.api.monitoring.check_redis", new_callable=AsyncMock, return_value={"status": "healthy"}),
            patch(
                "src.app.api.monitoring.check_worker_queue",
                new_callable=AsyncMock,
                return_value={"status": "healthy"},
            ),
            patch(
                "src.app.api.monitoring.check_cache_metrics",
                new_callable=AsyncMock,
                return_value={"status": "healthy"},
            ),
            patch("src.app.api.monitoring.check_dlq_size", new_callable=AsyncMock, return_value={"status": "healthy"}),
        ):
            res = await health_check()

        # The verdict is in the body...
        assert res.status == "error"
        # ...and the model carries no status code of its own: the route returns
        # 200 unconditionally. ``readiness_probe`` is where 503 lives.
        assert not hasattr(res, "status_code")

    @pytest.mark.asyncio
    async def test_the_gate_returns_503_when_a_dependency_is_down(self) -> None:
        unhealthy = HealthResult(status="unhealthy")
        healthy = HealthResult(status="healthy")
        with patch(
            "src.app.api.monitoring._collect_readiness_results",
            new=AsyncMock(return_value=(unhealthy, healthy, healthy, True)),
        ):
            res = await readiness_probe()

        assert isinstance(res, JSONResponse)
        assert res.status_code == 503

    @pytest.mark.asyncio
    async def test_the_gate_returns_503_when_only_the_queue_is_degraded(self) -> None:
        """The gate is stricter than the diagnostic: ANY non-healthy dep fails it.

        ``resolve_readiness_status`` returns ``degraded`` for a non-database
        failure, and only ``healthy`` yields 200 — which is exactly the
        behaviour the diagnostic route does not have.
        """
        healthy = HealthResult(status="healthy")
        degraded = HealthResult(status="degraded", error="Pool not initialized")
        with patch(
            "src.app.api.monitoring._collect_readiness_results",
            new=AsyncMock(return_value=(healthy, healthy, degraded, True)),
        ):
            res = await readiness_probe()

        assert isinstance(res, JSONResponse)
        assert res.status_code == 503
        assert json.loads(bytes(res.body))["reason"] == "degraded"
