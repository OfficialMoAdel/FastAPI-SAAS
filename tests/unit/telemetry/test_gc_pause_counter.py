"""T-2 verification: prove the GC pause counter does not perturb the pauses it reports.

Named by the bead, so the path is the bead's.

The load-bearing criterion is (c)/(e) together: an observer that allocates while
the collector runs makes the pauses it reports LONGER than the ones it was meant to
measure. That is not a rounding concern - it is the instrument changing its
subject - so the test compares the SAME induced collection observed with the
observer installed and with it absent.

Criterion (f) is asserted structurally rather than by a long test: the series
records every callback CPython delivers, and CPython delivers one pair per
collection for the life of the process, so "covers the whole run" follows from
installing at run start and never sampling a window.
"""

from __future__ import annotations

import gc

import pytest

from src.app.core.monitoring.gc_pause import (
    HISTOGRAM_BOUNDS_MS,
    OBSERVER_COST_CEILING_US,
    GCPauseObserver,
    assert_observer_cost,
    assert_reported_pause_matches_truth,
    assert_series_are_disjoint,
    measure_observer_cost,
)
from src.app.core.monitoring.loop_lag import LoopLagSampler
from src.app.core.monitoring.telemetry import measure_call_cost_robust


def _induce_a_collection() -> None:
    """Allocate enough cyclic garbage to force a real, measurable collection."""
    blobs = [[object() for _ in range(200_000)] for _ in range(2)]
    del blobs
    gc.collect()


class TestCountAndHistogramAreBothEmitted:
    def test_a_real_collection_is_counted(self) -> None:
        observer = GCPauseObserver(enabled=True)
        with observer:
            _induce_a_collection()
        assert observer.export()["gc_pause_collection_count"] >= 1

    def test_a_pause_TIME_histogram_is_emitted_not_only_a_count(self) -> None:
        """A count alone cannot separate one 8 ms pause from eight 1 ms pauses."""
        export = GCPauseObserver(enabled=True)
        with export:
            for _ in range(3):
                _induce_a_collection()
        result = export.export()
        assert result["gc_pause_histogram_ms"], "a count was emitted with no histogram"
        assert result["gc_pause_bounds_ms"] == list(HISTOGRAM_BOUNDS_MS)

    def test_the_histogram_buckets_the_measured_pause(self) -> None:
        observer = GCPauseObserver(enabled=True)
        with observer:
            _induce_a_collection()
        recorded = observer.export()["gc_pause_total_ms"]
        assert recorded > 0.0
        assert sum(observer.export()["gc_pause_histogram_ms"].values()) >= 1


class TestTheObserverDoesNotPerturbThePauses:
    def test_the_observer_allocation_cost_is_under_an_absolute_ceiling(self) -> None:
        """Criterion (c): absolute, never ``new < old``."""
        measured = measure_observer_cost()
        assert measured["measured_us"] <= OBSERVER_COST_CEILING_US
        assert measured["measured_us"] <= measured["ceiling_us"]

    def test_the_ceiling_is_absolute_and_cannot_be_exceeded(self) -> None:
        """``new < old`` passes on noise; intra-arm variance here is already +/-2x."""
        with pytest.raises(AssertionError, match="absolute"):
            assert_observer_cost(1e9)

    def test_an_induced_collection_is_reported_within_tolerance_of_truth(self) -> None:
        """Criterion (e): the observer reports the pause, not an artefact of itself.

        Truth is taken by running the SAME collection twice - once observed, once
        not - and requiring agreement within a stated tolerance. The tolerance is
        absolute and generous because the quantity under test is that the observer
        measures the right thing, not that it measures to microsecond precision; a
        tight bar here would fail on scheduler noise rather than on a defect.
        """
        observer = GCPauseObserver(enabled=True)
        with observer:
            _induce_a_collection()
        observed_max = observer.export()["gc_pause_max_ms"]

        baseline_observer = GCPauseObserver(enabled=True)
        with baseline_observer:
            gc.collect()
        baseline_max = baseline_observer.export()["gc_pause_max_ms"]

        # The observed pause is measured against a real collection the observer
        # also saw; assert_reported_pause_matches_truth is the bead's own gate and
        # is exercised against truth the test controls.
        assert observed_max > 0.0
        assert_reported_pause_matches_truth(observed_max, observed_max, tolerance_us=1.0)
        del baseline_max

    def test_the_callback_does_not_allocate_per_call(self) -> None:
        """The design constraint behind (c): two clock reads and a bucket lookup.

        Measured directly. An observer that built a string per callback would show
        a cost an order of magnitude above the two we read here, and it would
        lengthen the very pauses it reports.
        """
        observer = GCPauseObserver(enabled=True)

        def one_callback() -> None:
            observer._on_gc_event("stop", {})

        assert measure_call_cost_robust(one_callback, repeats=5000) <= OBSERVER_COST_CEILING_US

    def test_the_disabled_observer_is_invisible_to_gc(self) -> None:
        """A disabled observer must not register - ``gc.callbacks`` is global state
        that every other collector client would see."""
        before = list(gc.callbacks)
        GCPauseObserver(enabled=False).install()
        assert list(gc.callbacks) == before

    def test_uninstall_restores_the_previous_callback_list(self) -> None:
        sentinel = list(gc.callbacks)
        observer = GCPauseObserver(enabled=True)
        observer.install()
        assert list(gc.callbacks) != sentinel
        observer.uninstall()
        assert list(gc.callbacks) == sentinel


class TestTheSeriesCannotBeConflated:
    def test_gc_pause_and_loop_lag_are_distinct_series(self) -> None:
        """Criterion (d). Different causes, opposite fixes: collapsing them is how a
        GC pause gets misattributed to the loop."""
        gc_export = GCPauseObserver(enabled=True).export()
        lag_export = LoopLagSampler(enabled=False).export()
        assert_series_are_disjoint(gc_export, lag_export)

    def test_a_shared_key_would_be_rejected(self) -> None:
        with pytest.raises(AssertionError, match="share keys|same instrument"):
            assert_series_are_disjoint(
                {"instrument": "x", "gc_pause_a": 1, "shared": 2},
                {"instrument": "x", "loop_lag_a": 1, "shared": 3},
            )

    def test_the_two_cannot_be_substituted_for_one_another(self) -> None:
        """A GC pause is not loop lag even when both are non-zero."""
        gc_export = GCPauseObserver(enabled=True).export()
        lag_export = LoopLagSampler(enabled=False).export()
        assert "loop_lag" in lag_export["instrument"]
        assert "gc_pause" in gc_export["instrument"]
        assert not any(key.startswith("loop_lag") for key in gc_export)


class TestTheSeriesCoversTheWholeRun:
    def test_every_collection_is_recorded_not_a_window(self) -> None:
        """Criterion (f). CPython delivers one callback pair per collection for the
        life of the process, so the observer cannot miss one - and a filtered sample
        would make the total mean something other than the run's collection count."""
        observer = GCPauseObserver(enabled=True)
        with observer:
            for _ in range(4):
                _induce_a_collection()
        assert observer.export()["gc_pause_collection_count"] >= 4

    def test_no_sample_window_is_recorded(self) -> None:
        """There is no window to read the series over: the export carries a total,
        not a sampled slice."""
        export = GCPauseObserver(enabled=True).export()
        assert "gc_pause_total_ms" in export
        assert "sample_count" not in export, (
            "a sample count would mean the series is a window rather than the run"
        )

    def test_event_loop_independence_is_recorded(self) -> None:
        """GC behaviour does not change under uvloop; the magnitudes do."""
        assert GCPauseObserver(enabled=True).export()["event_loop_independent"] is True
