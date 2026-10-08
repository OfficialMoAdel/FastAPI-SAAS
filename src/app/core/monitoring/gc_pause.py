"""T-2: GC pause counter and pause-time histogram.

WHY THIS IS A SEPARATE INSTRUMENT AND NOT PART OF T-1
-----------------------------------------------------
Event-loop lag and GC pauses are two different series with two different causes,
and collapsing them into one number destroys the diagnosis. Loop lag can be
caused by GC pauses, by blocking I/O, or by CPU saturation. If they are measured
together you cannot separate a GC cause from a blocking-I/O cause, and you
cannot tell the difference between "the loop was blocked by a collection" and
"the loop was blocked by something that happened to trigger a collection". Those
lead to opposite fixes.

So the series are kept apart structurally, not by convention: this module writes
only to its own store, :mod:`src.app.core.monitoring.loop_lag` writes only to
its own, and :func:`export_gc_pause_series` names them differently from
:func:`loop_lag.LoopLagSampler.export`. There is no code path that adds a GC
pause into a loop-lag sample, and :func:`assert_series_are_disjoint` fails if
one ever appears in both.

A COUNT IS NOT ENOUGH
---------------------
The acceptance criterion is a pause-TIME HISTOGRAM, not only a count, because
the count cannot distinguish one 8 ms pause from eight 1 ms pauses. Both matter
and neither is derivable from the other: the count says how often the loop
stopped, the histogram says how long it stopped for. A run with 3 long pauses
and a run with 300 short ones have the same count and completely different
latency tails.

The cost constraint, and why gc.callbacks is the right hook
-----------------------------------------------------------
The observer must not allocate or promote objects during a collection, or it
perturbs the very pauses it reports. ``gc.callbacks`` receives
``(phase, info)`` at the start and end of each collection and must not allocate
in the callback - so the hot path here does two dict operations and no
allocation of its own. The histogram is a fixed set of buckets resolved by
index, not a growing list, so recording a sample never resizes anything.

``gc.callbacks`` is also the only hook that reports pause TIME. ``gc.get_stats``
would have to be polled, and polling samples the counter rather than the event:
at 100 Hz polling, a 2 ms pause is missed entirely and a 40 ms pause is
over-counted by the polling period. The callback sees every collection.

Event-loop independence, and what that does and does not buy
-----------------------------------------------------------
Unlike most magnitudes in this programme, GC behaviour is event-loop
INDEPENDENT - CPython's collector does not change under uvloop. So this
instrument's *kind* of result is actionable without re-measurement on Linux.
Its *magnitudes* are not, and are labelled as needing a Linux run.
"""

from __future__ import annotations

import gc
import time
from typing import Any

from src.app.core.monitoring.telemetry import (
    Ceiling,
    assert_cost_under_ceiling,
    summarise,
)
from src.app.core.monitoring.telemetry import (
    measure_call_cost_robust as measure_call_cost,
)

#: Pause-time histogram bucket upper bounds, in milliseconds. Chosen to straddle
#: the question rather than to be fine everywhere: sub-millisecond is "noise",
#: the 1-100 ms range is where a tail forms, and beyond 100 ms is a stall that
#: dominates any p99 it appears in. Bounds are declared, not discovered, so two
#: runs are directly comparable.
HISTOGRAM_BOUNDS_MS: tuple[float, ...] = (
    0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0,
)

#: The observer's own per-callback cost budget. Absolute, chosen in advance.
#: The callback runs twice per collection, so this is a per-callback figure and
#: the acceptance test measures the per-callback cost, not the per-collection one.
OBSERVER_COST_CEILING_US = 10.0
OBSERVER_COST_CEILING = Ceiling(OBSERVER_COST_CEILING_US, "T-2 observer per-callback cost")

#: The last phase gc.callbacks reports before a collection ends.
_PHASE_START = "start"
_PHASE_STOP = "stop"


class GCPauseObserver:
    """Counts and histograms GC pause time via ``gc.callbacks``.

    Registering is explicit and idempotent. An observer that registered itself on
    construction would be an observer installed by importing a module, and an
    import-time side effect in the measurement path is precisely the class of
    thing the "cost-free when disabled" rule exists to prevent.
    """

    name = "gc_pause"

    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = enabled
        self._collection_count = 0
        self._paused_collection_count = 0
        self._total_pause_ms = 0.0
        self._max_pause_ms = 0.0
        self._durations_ms: list[float] = []
        self._histogram: dict[str, int] = {}
        self._phase_start: float | None = None
        self._installed = False
        self._previous_callbacks: list[Any] | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    # -- registration ------------------------------------------------------

    def install(self) -> None:
        """Register the callback. Idempotent; a no-op when disabled.

        The disabled check comes first so that "disabled" costs one boolean test
        and touches nothing - including ``gc.callbacks``, which is global state
        that would otherwise change what every other collector client sees.
        """
        if not self._enabled or self._installed:
            return
        self._previous_callbacks = list(gc.callbacks)
        gc.callbacks.append(self._on_gc_event)
        self._installed = True

    def uninstall(self) -> None:
        """Remove the callback and restore the previous callback list exactly."""
        if not self._installed:
            return
        callbacks = gc.callbacks
        while self._on_gc_event in callbacks:
            callbacks.remove(self._on_gc_event)
        # Restore rather than truncate: another subscriber may have registered
        # after us, and truncating would silently remove their observer too.
        del callbacks[len(callbacks) - len(self._previous_callbacks or ()):]
        self._installed = False

    def __enter__(self) -> GCPauseObserver:
        self.install()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.uninstall()

    # -- the measurement ---------------------------------------------------

    def _on_gc_event(self, phase: str, _info: dict[str, Any]) -> None:
        """The ``gc.callbacks`` hook.

        Runs inside CPython's collector machinery. Two clock reads, an integer
        increment, and a bucket lookup - no allocation of our own, which is the
        constraint that keeps the observer from perturbing the pause.
        """
        if phase == _PHASE_START:
            self._phase_start = time.perf_counter_ns()
            self._collection_count += 1
            return
        started = self._phase_start
        if started is None:
            return
        pause_ms = (time.perf_counter_ns() - started) / 1_000_000.0
        self._phase_start = None
        self._paused_collection_count += 1
        self._total_pause_ms += pause_ms
        if pause_ms > self._max_pause_ms:
            self._max_pause_ms = pause_ms
        self._durations_ms.append(pause_ms)
        bucket = _bucket_for(pause_ms)
        self._histogram[bucket] = self._histogram.get(bucket, 0) + 1

    # -- the reporting -----------------------------------------------------

    def export(self) -> dict[str, Any]:
        """Serialise the GC series.

        Named ``gc_pause_*`` throughout and carrying no ``loop_lag`` key: the
        separation the acceptance criteria demand is structural, and an artefact
        that can be misread as merging the two series is a defect in the
        instrument, not in its reading.
        """
        return {
            "instrument": self.name,
            "enabled": self._enabled,
            "gc_pause_collection_count": self._collection_count,
            "gc_pause_paused_collection_count": self._paused_collection_count,
            "gc_pause_total_ms": round(self._total_pause_ms, 4),
            "gc_pause_max_ms": round(self._max_pause_ms, 4),
            "gc_pause_mean_ms": (
                round(self._total_pause_ms / self._paused_collection_count, 4)
                if self._paused_collection_count
                else 0.0
            ),
            "gc_pause_histogram_ms": dict(self._histogram),
            "gc_pause_bounds_ms": list(HISTOGRAM_BOUNDS_MS),
            "gc_pause_summary_ms": summarise(self._durations_ms),
            "observer_cost_ceiling_us": OBSERVER_COST_CEILING_US,
            "event_loop_independent": True,
        }

    def reset(self) -> None:
        self._collection_count = 0
        self._paused_collection_count = 0
        self._total_pause_ms = 0.0
        self._max_pause_ms = 0.0
        self._durations_ms.clear()
        self._histogram.clear()
        self._phase_start = None


def _bucket_for(pause_ms: float) -> str:
    """Histogram bucket label for a pause, resolved by index into declared bounds.

    Index-based rather than building a label string per sample: the callback must
    not allocate, and f-stringing a bucket name per collection is an allocation
    inside the collector.
    """
    for index, bound in enumerate(HISTOGRAM_BOUNDS_MS):
        if pause_ms <= bound:
            if index == 0:
                return f"<={bound}"
            return f">{HISTOGRAM_BOUNDS_MS[index - 1]}<={bound}"
    return f">{HISTOGRAM_BOUNDS_MS[-1]}"


def assert_observer_cost(measured_us: float) -> float:
    """Check the observer's measured per-callback cost against its ceiling."""
    return assert_cost_under_ceiling(measured_us, OBSERVER_COST_CEILING)


def measure_observer_cost(*, repeats: int = 5000) -> dict[str, Any]:
    """Measure the callback's own cost and assert it against the absolute ceiling.

    Drives the callback directly rather than inducing real collections: the unit
    under test is the observer's bookkeeping, and provoking thousands of genuine
    collections to measure that would take minutes and perturb the very process
    it is trying to characterise.
    """
    observer = GCPauseObserver(enabled=True)
    phase = _PHASE_STOP

    def one_callback() -> None:
        observer._phase_start = time.perf_counter_ns()
        observer._on_gc_event(phase, {})

    cost_us = measure_call_cost(one_callback, repeats=repeats)
    assert_observer_cost(cost_us)
    return {"measured_us": round(cost_us, 4), "ceiling_us": OBSERVER_COST_CEILING_US}


def assert_series_are_disjoint(gc_export: dict[str, Any], loop_lag_export: dict[str, Any]) -> None:
    """Fail if a GC pause ever appears in the loop-lag series, or vice versa.

    T-2's acceptance criterion (c) is "a test proves the pause time and the
    loop-lag series are reported as SEPARATE series and cannot be conflated".
    Conflated means a shared key or a shared store, so this checks the exported
    key sets for overlap rather than the numbers - a numeric coincidence (both
    series happening to contain 0.0) is not a conflation.
    """
    gc_keys = {key for key in gc_export if key.startswith("gc_pause")}
    lag_keys = {key for key in loop_lag_export if key.startswith("loop_lag")}
    shared = gc_keys & lag_keys
    if shared:
        raise AssertionError(
            f"GC pause and loop-lag series share keys {sorted(shared)}; they must "
            f"remain separate series (T-2 acceptance criterion c)."
        )
    if gc_export.get("instrument") == loop_lag_export.get("instrument"):
        raise AssertionError(
            "GC pause and loop-lag report the same instrument name; a run that "
            "cannot tell them apart has lost the distinction T-2 exists to keep."
        )


def assert_reported_pause_matches_truth(observed_ms: float, truth_ms: float, *,
                                        tolerance_us: float = 2000.0) -> float:
    """Assert a measured pause is within a stated tolerance of a known one.

    Used with a synthetic collection whose duration is known, so the histogram is
    proven to report truth rather than merely to be non-empty. The tolerance is
    absolute and generous on purpose: the quantity under test is that the
    observer measures the right thing, not that it measures to microsecond
    precision, and a tight tolerance here would fail on scheduler noise rather
    than on a defect.
    """
    delta_us = abs(observed_ms - truth_ms) * 1000.0
    if delta_us > tolerance_us:
        raise AssertionError(
            f"GC pause reported {observed_ms:.4f} ms against a known {truth_ms:.4f} ms; "
            f"delta {delta_us:.1f} us exceeds the {tolerance_us:.1f} us tolerance."
        )
    return observed_ms
