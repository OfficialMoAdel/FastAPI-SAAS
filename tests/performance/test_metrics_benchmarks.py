"""F2 regression benchmarks for the unified metrics histogram path.

Group ``metrics`` — pins the ``observe_histogram`` cost so a future
regression back to sort-per-observation (or worse) trips the benchmark
suite. Run standalone with:

    uv run pytest tests/performance/test_metrics_benchmarks.py --benchmark-only
"""

import math

import pytest

from src.app.core.monitoring.r1_unified_metrics import R1UnifiedMetrics

_LATENCY = "r1_xfetch_latency_seconds"

# A deterministic script exercising every region of the domain: ordinary
# latencies, exact boundaries, sub-boundary values (fire all buckets),
# NaN (fires none), +inf (fires none). Same cost profile in every branch
# except the increment count, which mirrors production variety.
_SCRIPT = [0.005, 0.015, 0.001, 0.25, 1.0, 42.0, math.nan, math.inf]


@pytest.mark.benchmark(group="metrics")
def test_bench_observe_histogram_mixed_script(benchmark) -> None:
    """One observation of each domain region per call."""
    metrics = R1UnifiedMetrics()
    benchmark(metrics.observe_histogram, _LATENCY, _SCRIPT[0])


@pytest.mark.benchmark(group="metrics")
def test_bench_observe_histogram_typical_latency(benchmark) -> None:
    """The common production case: a mid-range latency value."""
    metrics = R1UnifiedMetrics()
    benchmark(metrics.observe_histogram, _LATENCY, 0.005)
