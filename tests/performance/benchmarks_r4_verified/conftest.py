"""Performance benchmark fixtures and utilities.

Provides:
- Benchmark runner with statistical analysis
- Baseline comparison
- Regression detection
- Result logging
"""

import json
import logging
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

logger = logging.getLogger(__name__)

# Baseline metrics file
BASELINE_FILE = Path(__file__).parent / "baseline_metrics.json"

# Regression threshold (10% slower is a failure)
REGRESSION_THRESHOLD = 1.10


class BenchmarkResult:
    """Container for benchmark results with statistical analysis."""

    def __init__(self, name: str, times: list[float]):
        """Initialize benchmark result.

        Args:
            name: Benchmark name
            times: List of execution times in seconds
        """
        self.name = name
        self.times = times
        self.iterations = len(times)

        # Calculate statistics
        self.min = min(times)
        self.max = max(times)
        self.mean = statistics.mean(times)
        self.median = statistics.median(times)

        if len(times) >= 2:
            self.stdev = statistics.stdev(times)
        else:
            self.stdev = 0.0

        # Calculate percentiles
        sorted_times = sorted(times)
        self.p50 = sorted_times[int(len(sorted_times) * 0.50)]
        self.p95 = sorted_times[int(len(sorted_times) * 0.95)]
        self.p99 = sorted_times[int(len(sorted_times) * 0.99)]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "iterations": self.iterations,
            "min_ms": self.min * 1000,
            "max_ms": self.max * 1000,
            "mean_ms": self.mean * 1000,
            "median_ms": self.median * 1000,
            "stdev_ms": self.stdev * 1000,
            "p50_ms": self.p50 * 1000,
            "p95_ms": self.p95 * 1000,
            "p99_ms": self.p99 * 1000,
        }

    def log_summary(self):
        """Log statistical summary."""
        logger.info(f"\n{'='*60}")
        logger.info(f"Benchmark: {self.name}")
        logger.info(f"{'='*60}")
        logger.info(f"Iterations: {self.iterations}")
        logger.info(f"Min:        {self.min * 1000:.3f} ms")
        logger.info(f"Max:        {self.max * 1000:.3f} ms")
        logger.info(f"Mean:       {self.mean * 1000:.3f} ms")
        logger.info(f"Median:     {self.median * 1000:.3f} ms")
        logger.info(f"Stdev:      {self.stdev * 1000:.3f} ms")
        logger.info(f"P50:        {self.p50 * 1000:.3f} ms")
        logger.info(f"P95:        {self.p95 * 1000:.3f} ms")
        logger.info(f"P99:        {self.p99 * 1000:.3f} ms")
        logger.info(f"{'='*60}\n")


@pytest.fixture
def benchmark_runner():
    """Fixture to run benchmarks with statistical analysis."""

    def _run_benchmark(
        name: str,
        func: Callable,
        iterations: int = 100,
        threshold_ms: float | None = None,
    ) -> BenchmarkResult:
        """Run benchmark and analyze results.

        Args:
            name: Benchmark name
            func: Function to benchmark (should be callable with no args)
            iterations: Number of iterations to run
            threshold_ms: Maximum acceptable mean time in milliseconds

        Returns:
            BenchmarkResult with statistics
        """
        logger.info(f"Starting benchmark: {name} ({iterations} iterations)")

        times = []
        for i in range(iterations):
            start = time.perf_counter()
            func()
            elapsed = time.perf_counter() - start
            times.append(elapsed)

            if (i + 1) % 10 == 0:
                logger.debug(f"Completed {i + 1}/{iterations} iterations")

        result = BenchmarkResult(name, times)
        result.log_summary()

        # Check threshold
        if threshold_ms is not None:
            mean_ms = result.mean * 1000
            if mean_ms > threshold_ms:
                logger.error(
                    f"THRESHOLD EXCEEDED: {mean_ms:.3f}ms > {threshold_ms}ms"
                )
                pytest.fail(
                    f"Benchmark {name} exceeded threshold: "
                    f"{mean_ms:.3f}ms > {threshold_ms}ms"
                )
            else:
                logger.info(
                    f"✓ Threshold check passed: {mean_ms:.3f}ms <= {threshold_ms}ms"
                )

        return result

    return _run_benchmark


@pytest.fixture
def async_benchmark_runner():
    """Fixture to run async benchmarks with statistical analysis."""
    import asyncio

    def _run_async_benchmark(
        name: str,
        func: Callable,
        iterations: int = 100,
        threshold_ms: float | None = None,
    ) -> BenchmarkResult:
        """Run async benchmark and analyze results.

        Args:
            name: Benchmark name
            func: Async function to benchmark
            iterations: Number of iterations to run
            threshold_ms: Maximum acceptable mean time in milliseconds

        Returns:
            BenchmarkResult with statistics
        """
        logger.info(f"Starting async benchmark: {name} ({iterations} iterations)")

        async def run_iterations():
            times = []
            for i in range(iterations):
                start = time.perf_counter()
                await func()
                elapsed = time.perf_counter() - start
                times.append(elapsed)

                if (i + 1) % 10 == 0:
                    logger.debug(f"Completed {i + 1}/{iterations} iterations")
            return times

        times = asyncio.run(run_iterations())
        result = BenchmarkResult(name, times)
        result.log_summary()

        # Check threshold
        if threshold_ms is not None:
            mean_ms = result.mean * 1000
            if mean_ms > threshold_ms:
                logger.error(
                    f"THRESHOLD EXCEEDED: {mean_ms:.3f}ms > {threshold_ms}ms"
                )
                pytest.fail(
                    f"Benchmark {name} exceeded threshold: "
                    f"{mean_ms:.3f}ms > {threshold_ms}ms"
                )
            else:
                logger.info(
                    f"✓ Threshold check passed: {mean_ms:.3f}ms <= {threshold_ms}ms"
                )

        return result

    return _run_async_benchmark


@pytest.fixture
def baseline_comparator():
    """Fixture to compare benchmark results against baseline."""

    def _compare_with_baseline(result: BenchmarkResult) -> dict[str, Any]:
        """Compare result with baseline and detect regressions.

        Args:
            result: BenchmarkResult to compare

        Returns:
            Comparison dict with regression status
        """
        # Load baseline
        baseline = _load_baseline()
        baseline_mean = baseline.get(result.name, {}).get("mean_ms")

        if baseline_mean is None:
            logger.warning(f"No baseline found for {result.name}")
            return {
                "has_baseline": False,
                "regression": False,
                "message": "No baseline for comparison",
            }

        current_mean = result.mean * 1000
        ratio = current_mean / baseline_mean
        percent_change = (ratio - 1.0) * 100

        logger.info(f"\n{'='*60}")
        logger.info(f"Baseline Comparison: {result.name}")
        logger.info(f"{'='*60}")
        logger.info(f"Baseline:  {baseline_mean:.3f} ms")
        logger.info(f"Current:   {current_mean:.3f} ms")
        logger.info(f"Ratio:     {ratio:.2f}x")
        logger.info(f"Change:    {percent_change:+.1f}%")

        is_regression = ratio > REGRESSION_THRESHOLD

        if is_regression:
            logger.error(f"⚠️  REGRESSION DETECTED: {percent_change:+.1f}% slower")
            logger.info(f"{'='*60}\n")
            pytest.fail(
                f"Performance regression in {result.name}: "
                f"{percent_change:+.1f}% slower than baseline"
            )
        else:
            logger.info("✓ No regression detected")
            logger.info(f"{'='*60}\n")

        return {
            "has_baseline": True,
            "baseline_ms": baseline_mean,
            "current_ms": current_mean,
            "ratio": ratio,
            "percent_change": percent_change,
            "regression": is_regression,
        }

    def _load_baseline() -> dict[str, Any]:
        """Load baseline metrics from file."""
        if not BASELINE_FILE.exists():
            return {}

        try:
            with open(BASELINE_FILE) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load baseline: {e}")
            return {}

    def _save_baseline(result: BenchmarkResult):
        """Save benchmark result as new baseline."""
        baseline = _load_baseline()
        baseline[result.name] = result.to_dict()

        BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(BASELINE_FILE, "w") as f:
            json.dump(baseline, f, indent=2)

        logger.info(f"Saved baseline for {result.name}")

    return _compare_with_baseline


@pytest.fixture
def save_baseline():
    """Fixture to save benchmark results as baseline."""

    def _save(result: BenchmarkResult):
        """Save result as baseline."""
        baseline = {}
        if BASELINE_FILE.exists():
            try:
                with open(BASELINE_FILE) as f:
                    baseline = json.load(f)
            except Exception:
                pass

        baseline[result.name] = result.to_dict()

        BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(BASELINE_FILE, "w") as f:
            json.dump(baseline, f, indent=2)

        logger.info(f"✓ Saved baseline for {result.name}")

    return _save
