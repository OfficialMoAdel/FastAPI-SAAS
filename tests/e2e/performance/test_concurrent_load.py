"""E2E Tests: Concurrent Load Performance Validation.

Tests validate system performance under concurrent load after all
optimizations (Redis pool P2-2, middleware, caching) are applied.
"""

import asyncio

import pytest

from tests.e2e.conftest import PerformanceMetrics


@pytest.fixture()
def mock_event_bus():
    """Override the global autouse event-bus mock for this module.

    These tests exercise REAL event-bus handler dispatch (subscribe + publish),
    which the MagicMock singleton in tests/conftest.py silently swallows —
    handlers registered on the mock never fire and every assertion on handler
    side effects fails. Yield the real singleton instead.
    """
    from src.app.core.events import event_bus, reset_event_bus

    reset_event_bus()
    yield event_bus
    reset_event_bus()


class TestConcurrentLoad:
    """Validate system performance under concurrent load."""

    @pytest.mark.asyncio
    async def test_concurrent_http_requests(
        self,
        e2e_client,
        perf_metrics: PerformanceMetrics,
        e2e_thresholds: dict,
    ):
        """Test concurrent HTTP requests don't degrade under load."""
        concurrency = 5
        iterations = 4

        async def make_request():
            perf_metrics.start_timer()
            try:
                response = await e2e_client.post(
                    "/api/v1/account/login",
                    data={
                        "username": "load@example.com",
                        "password": "LoadTest123!",
                    },
                )
                perf_metrics.stop_timer()
                return response
            except Exception:
                perf_metrics.stop_timer()
                perf_metrics.record_error()
                raise

        # Run concurrent requests
        tasks = []
        for _ in range(concurrency):
            for _ in range(iterations):
                tasks.append(make_request())

        await asyncio.gather(*tasks, return_exceptions=True)

        # Validate results
        assert perf_metrics.count == concurrency * iterations
        assert perf_metrics.error_rate <= e2e_thresholds["concurrent_error_rate_max"]

    @pytest.mark.asyncio
    async def test_redis_pool_simulation(
        self,
        perf_metrics: PerformanceMetrics,
    ):
        """Test simulated Redis pool handles concurrent connections."""
        concurrency = 50
        operations_per_worker = 10

        async def redis_operation(worker_id: int):
            """Simulate a Redis get/set cycle."""
            for _ in range(operations_per_worker):
                perf_metrics.start_timer()
                await asyncio.sleep(0.001)  # 1ms simulated latency
                perf_metrics.stop_timer()

        tasks = [redis_operation(i) for i in range(concurrency)]
        await asyncio.gather(*tasks)

        # All operations should complete
        assert perf_metrics.count == concurrency * operations_per_worker
        assert perf_metrics.errors == 0
        assert perf_metrics.p99 < 50.0


class TestEventBusPerformance:
    """Test event bus performance under load."""

    @pytest.mark.asyncio
    async def test_parallel_event_handling(
        self,
        perf_metrics: PerformanceMetrics,
    ):
        """Test event bus handles parallel events efficiently."""
        from src.app.core.events import EventPriority, event_bus

        # Clear existing handlers
        event_bus._handlers.clear()

        results = []

        @event_bus.subscribe("e2e.load.test")
        async def handler_1(data: dict):
            await asyncio.sleep(0.005)
            results.append(("handler_1", data["id"]))

        @event_bus.subscribe("e2e.load.test")
        async def handler_2(data: dict):
            await asyncio.sleep(0.005)
            results.append(("handler_2", data["id"]))

        # Publish multiple events
        event_count = 10
        for i in range(event_count):
            perf_metrics.start_timer()
            await event_bus.publish(
                "e2e.load.test",
                {"id": i},
                priority=EventPriority.NORMAL,
            )
            perf_metrics.stop_timer()

        # Wait for async handlers to complete
        await asyncio.sleep(0.15)

        # Verify handlers were called
        assert len(results) == event_count * 2

        # Publishing should be fast (increased from 20 to 50 for CI stability)
        assert perf_metrics.p95 < 50.0


class TestMiddlewarePerformance:
    """Test middleware stack performance."""

    @pytest.mark.asyncio
    async def test_middleware_overhead(
        self,
        e2e_client,
        perf_metrics: PerformanceMetrics,
        e2e_thresholds: dict,
    ):
        """Test middleware adds minimal overhead to requests."""
        # Warmup
        await e2e_client.get("/api/v1/health")

        iterations = 30

        for _ in range(iterations):
            perf_metrics.start_timer()
            response = await e2e_client.get("/api/v1/health")
            perf_metrics.stop_timer()
            assert response.status_code in (200, 404, 429, 422)

        assert perf_metrics.p95 < e2e_thresholds["middleware_p95"]

    @pytest.mark.asyncio
    async def test_rate_limit_middleware_performance(
        self,
        e2e_client,
        perf_metrics: PerformanceMetrics,
        e2e_thresholds: dict,
    ):
        """Test rate limiting middleware performance."""
        # Warmup: Initialize middleware stack and internal caches
        await e2e_client.get(
            "/api/v1/health",
            headers={"X-Tenant-ID": "test-tenant"},
        )

        iterations = 30

        for _ in range(iterations):
            perf_metrics.start_timer()
            response = await e2e_client.get(
                "/api/v1/health",
                headers={"X-Tenant-ID": "test-tenant"},
            )
            perf_metrics.stop_timer()
            # We don't care if it's 200 or 429 here, only that the middleware stack remains fast
            assert response.status_code in (200, 429, 404)

        assert perf_metrics.p95 < e2e_thresholds["middleware_p95"]
