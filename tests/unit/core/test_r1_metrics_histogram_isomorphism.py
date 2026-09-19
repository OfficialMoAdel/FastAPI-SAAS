"""F2 isomorphism tests for the precomputed-boundary histogram observer.

Oracle: ``R1UnifiedMetrics.observe_histogram`` (bisect + precomputed
ascending boundaries) must produce **byte-identical** ``export_prometheus``
output and identical bucket counters as the previous
sort-per-observation implementation, across the full float domain —
including NaN, ±inf, negatives, zero, exact-boundary values, and mixed
sequences.

The legacy reference implementation below is a verbatim copy of the
pre-change code (sorted() per observation, inclusive ``<=``), so any
future drift on either side trips the differential test.
"""

from __future__ import annotations

import math

from src.app.core.monitoring.r1_unified_metrics import (
    HistogramBuckets,
    R1UnifiedMetrics,
)


class _LegacyReferenceMetrics(R1UnifiedMetrics):
    """Reference copy of the pre-F2 observe_histogram (sort per call)."""

    def observe_histogram(self, name: str, value: float) -> None:  # type: ignore[override]
        hist = self._histograms.get(name)
        if hist is None:
            return
        hist["sum"] += value
        hist["count"] += 1
        for boundary in sorted(hist["buckets"].keys()):
            if value <= boundary:
                hist["buckets"][boundary] += 1


_LATENCY = "r1_xfetch_latency_seconds"
_ROUNDTRIPS = "r1_session_eviction_roundtrips"

# Observation scripts covering the full float domain and the boundary
# structure. Deterministic (no RNG) so failures are reproducible.
_OBSERVATION_SCRIPTS: list[list[float]] = [
    [0.005, 0.015, 0.25, 2.0, 42.0],  # ordinary spread
    [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],  # exact boundaries (inclusive)
    [0.0, 0.0, 0.0],  # zero: <= every boundary
    [-1.0, -0.5, -1e-9],  # negatives: every boundary exceeds them
    [math.inf, 1e12, 1.0],  # +inf above all boundaries
    [1.0, 0.5, 0.25, 0.1],  # descending order
    [1e-12, 1e-6, 0.000999, 0.0010001],  # sub-1ms and boundary-adjacent
]


def _assert_isomorphic(script: list[float], histogram: str = _LATENCY) -> None:
    modern = R1UnifiedMetrics()
    legacy = _LegacyReferenceMetrics()
    for value in script:
        modern.observe_histogram(histogram, value)
        legacy.observe_histogram(histogram, value)

    assert modern.get_histogram(histogram)["buckets"] == legacy.get_histogram(histogram)["buckets"]
    # NaN-safe sum comparison: NaN != NaN by IEEE 754, so both-sides-NaN
    # is an isomorphic outcome (export renders both as "nan").
    modern_sum = modern.get_histogram(histogram)["sum"]
    legacy_sum = legacy.get_histogram(histogram)["sum"]
    assert (modern_sum != modern_sum and legacy_sum != legacy_sum) or modern_sum == legacy_sum
    assert modern.get_histogram(histogram)["count"] == legacy.get_histogram(histogram)["count"]
    # The oracle: identical exported bytes, histograms section included.
    assert modern.export_prometheus() == legacy.export_prometheus()


class TestObserveHistogramIsomorphism:
    def test_export_bytes_unchanged_ordinary_spread(self) -> None:
        _assert_isomorphic(_OBSERVATION_SCRIPTS[0])

    def test_export_bytes_unchanged_exact_boundaries(self) -> None:
        _assert_isomorphic(_OBSERVATION_SCRIPTS[1])

    def test_export_bytes_unchanged_zero(self) -> None:
        _assert_isomorphic(_OBSERVATION_SCRIPTS[2])

    def test_negative_values_fire_all_buckets_like_legacy(self) -> None:
        """value < 0 is <= every boundary, so ALL buckets fire — the naive
        bisect form would fire none. Pinned against the legacy reference."""
        _assert_isomorphic(_OBSERVATION_SCRIPTS[3])

        metrics = R1UnifiedMetrics()
        metrics.observe_histogram(_LATENCY, -1.0)
        buckets = metrics.get_histogram(_LATENCY)["buckets"]
        assert all(count == 1 for count in buckets.values()), buckets

    def test_nan_fires_no_buckets_like_legacy(self) -> None:
        """NaN satisfies no comparison, so NO bucket fires — the naive
        bisect form would fire all. Pinned against the legacy reference."""
        _assert_isomorphic([math.nan])

        metrics = R1UnifiedMetrics()
        metrics.observe_histogram(_LATENCY, math.nan)
        buckets = metrics.get_histogram(_LATENCY)["buckets"]
        assert all(count == 0 for count in buckets.values()), buckets
        # sum/count still accumulate, matching legacy float semantics.
        hist = metrics.get_histogram(_LATENCY)
        assert hist["count"] == 1
        assert math.isnan(hist["sum"])

    def test_export_bytes_unchanged_plus_inf(self) -> None:
        _assert_isomorphic(_OBSERVATION_SCRIPTS[4])

    def test_export_bytes_unchanged_descending(self) -> None:
        _assert_isomorphic(_OBSERVATION_SCRIPTS[5])

    def test_export_bytes_unchanged_sub_millisecond(self) -> None:
        _assert_isomorphic(_OBSERVATION_SCRIPTS[6])

    def test_mixed_script_full_isomorphism(self) -> None:
        for script in _OBSERVATION_SCRIPTS:
            _assert_isomorphic(script)

    def test_isomorphic_on_integer_roundtrip_histogram(self) -> None:
        """Second histogram shape (int boundaries, different domain)."""
        _assert_isomorphic([0, 1, 2, 3, 5, 10, 11, -3], histogram=_ROUNDTRIPS)


class TestStructuralGuards:
    def test_boundaries_precomputed_ascending(self) -> None:
        """Every registered histogram carries an ascending boundaries
        tuple covering exactly its bucket keys — the precondition the
        bisect path relies on."""
        metrics = R1UnifiedMetrics()
        for name, hist in metrics.get_histogram.__self__._histograms.items():  # type: ignore[attr-defined]
            boundaries: tuple[float, ...] = hist["boundaries"]
            assert boundaries == tuple(sorted(boundaries)), name
            assert set(boundaries) == set(hist["buckets"].keys()), name

        latency = R1UnifiedMetrics().get_histogram(_LATENCY)["boundaries"]
        assert latency == tuple(sorted(HistogramBuckets().boundaries))

    def test_unknown_histogram_still_warns_and_ignores(self) -> None:
        """Unchanged contract: unknown names log a warning and mutate
        nothing."""
        metrics = R1UnifiedMetrics()
        metrics.observe_histogram("r1_unknown_histogram", 1.0)  # must not raise
        assert metrics.get_histogram("r1_unknown_histogram") == {}

    def test_reset_still_zeroes_everything(self) -> None:
        metrics = R1UnifiedMetrics()
        metrics.observe_histogram(_LATENCY, 0.4)
        metrics.observe_histogram(_ROUNDTRIPS, 3)
        metrics.reset()
        for name in (_LATENCY, _ROUNDTRIPS):
            hist = metrics.get_histogram(name)
            assert hist["count"] == 0
            assert hist["sum"] == 0.0
            assert all(count == 0 for count in hist["buckets"].values())
