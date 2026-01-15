"""E2E Performance Test Fixtures and Utilities."""

import asyncio
import statistics
import time
from dataclasses import dataclass, field
from typing import Callable

import pytest


@dataclass
class PerformanceMetrics:
    """Collects and calculates performance metrics from timing measurements."""

    latencies: list[float] = field(default_factory=list)
    cache_hits: int = 0
    cache_misses: int = 0
    errors: int = 0
    _start_time: float = 0.0

    def record(self, latency_ms: float) -> None:
        """Record a single latency measurement in milliseconds."""
        self.latencies.append(latency_ms)

    def record_cache_hit(self) -> None:
        """Record a cache hit."""
        self.cache_hits += 1

    def record_cache_miss(self) -> None:
        """Record a cache miss."""
        self.cache_misses += 1

    def record_error(self) -> None:
        """Record an error."""
        self.errors += 1

    def start_timer(self) -> None:
        """Start the timer for measuring latency."""
        self._start_time = time.perf_counter()

    def stop_timer(self) -> float:
        """Stop timer and record the latency. Returns latency in ms."""
        latency_ms = (time.perf_counter() - self._start_time) * 1000
        self.record(latency_ms)
        return latency_ms

    @property
    def count(self) -> int:
        """Total number of recorded latencies."""
        return len(self.latencies)

    @property
    def p50(self) -> float:
        """50th percentile latency in ms."""
        if not self.latencies:
            return 0.0
        return statistics.median(self.latencies)

    @property
    def p95(self) -> float:
        """95th percentile latency in ms."""
        if not self.latencies:
            return 0.0
        sorted_latencies = sorted(self.latencies)
        idx = int(len(sorted_latencies) * 0.95)
        return sorted_latencies[min(idx, len(sorted_latencies) - 1)]

    @property
    def p99(self) -> float:
        """99th percentile latency in ms."""
        if not self.latencies:
            return 0.0
        sorted_latencies = sorted(self.latencies)
        idx = int(len(sorted_latencies) * 0.99)
        return sorted_latencies[min(idx, len(sorted_latencies) - 1)]

    @property
    def mean(self) -> float:
        """Mean latency in ms."""
        if not self.latencies:
            return 0.0
        return statistics.mean(self.latencies)

    @property
    def min(self) -> float:
        """Minimum latency in ms."""
        return min(self.latencies) if self.latencies else 0.0

    @property
    def max(self) -> float:
        """Maximum latency in ms."""
        return max(self.latencies) if self.latencies else 0.0

    @property
    def cache_hit_ratio(self) -> float:
        """Cache hit ratio as a percentage (0-100)."""
        total = self.cache_hits + self.cache_misses
        if total == 0:
            return 0.0
        return (self.cache_hits / total) * 100

    @property
    def error_rate(self) -> float:
        """Error rate as a percentage (0-100)."""
        if self.count == 0:
            return 0.0
        return (self.errors / self.count) * 100

    def summary(self) -> dict:
        """Return a summary dict of all metrics."""
        return {
            "count": self.count,
            "p50_ms": round(self.p50, 2),
            "p95_ms": round(self.p95, 2),
            "p99_ms": round(self.p99, 2),
            "mean_ms": round(self.mean, 2),
            "min_ms": round(self.min, 2),
            "max_ms": round(self.max, 2),
            "cache_hit_ratio": round(self.cache_hit_ratio, 1),
            "error_rate": round(self.error_rate, 1),
        }

    def __str__(self) -> str:
        """Human-readable summary."""
        return (
            f"PerformanceMetrics(n={self.count}, "
            f"p50={self.p50:.2f}ms, p95={self.p95:.2f}ms, p99={self.p99:.2f}ms, "
            f"cache_hit={self.cache_hit_ratio:.1f}%, errors={self.errors})"
        )


# Performance thresholds for E2E tests (in milliseconds)
# Note: These are relaxed for test environments; production should be stricter
E2E_THRESHOLDS = {
    # Auth flow latencies (relaxed for test env with DB/audit overhead)
    "auth_login_p95": 500.0,  # First request has cold start overhead
    "auth_refresh_p95": 200.0,
    "auth_me_p95": 150.0,
    # Cache performance
    "cache_hit_ratio_min": 80.0,  # Minimum acceptable cache hit ratio (%)
    "cache_operation_p95": 50.0,
    # Concurrent load
    "concurrent_p95": 300.0,
    "concurrent_error_rate_max": 5.0,  # Max acceptable error rate (%)
    # Middleware overhead
    "middleware_p95": 100.0,
}


@pytest.fixture
def perf_metrics() -> PerformanceMetrics:
    """Create a fresh PerformanceMetrics instance."""
    return PerformanceMetrics()


@pytest.fixture
def e2e_thresholds() -> dict:
    """Return E2E performance thresholds."""
    return E2E_THRESHOLDS.copy()


@pytest.fixture
def measure_latency() -> Callable:
    """Factory for measuring async function latency."""

    async def _measure(
        func: Callable,
        metrics: PerformanceMetrics,
        *args,
        **kwargs,
    ):
        """Measure latency of an async function call."""
        metrics.start_timer()
        try:
            result = await func(*args, **kwargs)
            metrics.stop_timer()
            return result
        except Exception:
            metrics.stop_timer()
            metrics.record_error()
            raise

    return _measure


@pytest.fixture
def run_concurrent() -> Callable:
    """Factory for running concurrent load tests."""

    async def _run_concurrent(
        func: Callable,
        concurrency: int,
        iterations: int,
        metrics: PerformanceMetrics,
        *args,
        **kwargs,
    ) -> None:
        """Run a function concurrently with specified concurrency level."""

        async def worker(worker_id: int) -> None:
            for _ in range(iterations):
                metrics.start_timer()
                try:
                    await func(*args, **kwargs)
                    metrics.stop_timer()
                except Exception:
                    metrics.stop_timer()
                    metrics.record_error()

        tasks = [worker(i) for i in range(concurrency)]
        await asyncio.gather(*tasks)

    return _run_concurrent
