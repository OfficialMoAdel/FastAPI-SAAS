import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.job_monitoring import JobMonitoringService
from src.app.core.resilience import db_circuit_breaker
from src.app.core.resilience.registry import CircuitBreakerRegistry, ServiceType
from src.app.core.utils.circuit_breaker import CircuitBreaker

# --- Circuit Breaker Registry Benchmarks ---


@pytest.fixture
def populated_registry():
    registry = CircuitBreakerRegistry()
    # Populate with some dummy circuits
    for i in range(100):
        # Create a dummy circuit breaker
        cb = CircuitBreaker(name=f"service_{i}", failure_threshold=5, recovery_timeout=30)
        registry.register(
            name=f"service_{i}",
            breaker=cb,
            description=f"Description for service_{i}",
            service_type=ServiceType.DATABASE,
        )
    return registry


@pytest.mark.benchmark(group="resilience_registry")
def test_registry_get_circuit_overhead(benchmark, populated_registry):
    """Benchmark retrieving a circuit breaker from the registry (lookup overhead)."""

    def _work():
        return populated_registry.get("service_50")

    benchmark(_work)


@pytest.mark.benchmark(group="resilience_registry")
def test_registry_health_summary_overhead(benchmark, populated_registry):
    """Benchmark generating a full health summary for 100 services."""

    def _work():
        return populated_registry.get_health_summary()

    benchmark(_work)


# --- Database Circuit Breaker Benchmarks ---


def test_db_circuit_overhead_closed_sync(benchmark):
    """Sync wrapper to benchmark db_circuit_guard overhead (closed circuit)."""
    # Setup
    cb = CircuitBreaker(name="db_bench", failure_threshold=5, recovery_timeout=30)

    original_cb = db_circuit_breaker.db_circuit
    db_circuit_breaker.db_circuit = cb

    async def _work():
        async with db_circuit_breaker.db_circuit_guard():
            pass

    def _run():
        asyncio.run(_work())

    try:
        benchmark(_run)
    finally:
        db_circuit_breaker.db_circuit = original_cb


def test_db_circuit_overhead_open_fallback_sync(benchmark):
    """Sync wrapper to benchmark db_circuit_guard fail-fast overhead (open circuit)."""
    # Setup
    cb = CircuitBreaker(name="db_bench", failure_threshold=5, recovery_timeout=30)
    cb._open()

    original_cb = db_circuit_breaker.db_circuit
    db_circuit_breaker.db_circuit = cb

    async def _work():
        try:
            async with db_circuit_breaker.db_circuit_guard():
                pass
        except Exception:
            pass

    def _run():
        asyncio.run(_work())

    try:
        benchmark(_run)
    finally:
        db_circuit_breaker.db_circuit = original_cb


# --- Job Monitoring Benchmarks ---


def test_job_monitor_record_start_sync(benchmark):
    """Sync wrapper to benchmark job monitor record start."""
    mock_redis = AsyncMock()
    service = JobMonitoringService()

    # Fix: pipeline is synchronous
    mock_pipeline = MagicMock()
    # Mocking pipeline attribute directly instead of return_value of pipeline method
    # because AsyncMock makes all method calls return coroutines.
    # To make .pipeline() synchronous, we must replace the method itself.
    mock_redis.pipeline = MagicMock(return_value=mock_pipeline)

    # pipe.execute() is awaited, so it should return a coroutine
    mock_pipeline.execute = AsyncMock()

    async def _work():
        await service.record_job_start("test_job_123", "process_data")

    def _run():
        asyncio.run(_work())

    with patch("src.app.core.utils.cache.client", mock_redis):
        benchmark(_run)


def test_job_monitor_get_metrics_sync(benchmark):
    """Sync wrapper to benchmark job monitor get metrics."""
    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {b"total_jobs": b"100", b"failed_jobs": b"2", b"success_jobs": b"98"}
    mock_redis.scan.return_value = (0, [b"arq:stats:process_data"])

    service = JobMonitoringService()

    async def _work():
        await service.get_job_metrics()

    def _run():
        asyncio.run(_work())

    with patch("src.app.core.utils.cache.client", mock_redis):
        benchmark(_run)
