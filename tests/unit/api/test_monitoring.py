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
