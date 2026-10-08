"""Phase-0 telemetry (T-1..T-8): the shared substrate every instrument sits on.

Every instrument in this package obeys three rules that the Phase-0 plan states
per-instrument, so they are enforced once here rather than re-argued eight
times:

**ADDITIVE.** Nothing here changes what the application does. A series may be
absent from an artefact; it may never alter a response, a decision, or a status
code.

**COST-FREE WHEN DISABLED.** This is the one that is easiest to get wrong and
the one the plan names explicitly: "a sampler that itself adds hot-path latency
corrupts the very baseline it exists to measure". :class:`Instrument` exposes an
``enabled`` flag, and every recording entry point checks it *first*, so the
disabled path is a single boolean test and returns before allocating, touching a
dict, or taking a clock reading.

**DEFAULT-OFF.** Each instrument defaults to disabled. An instrument that is on
by default is an instrument whose cost is paid by every deployment that never
looks at the series, which is how a measurement tool becomes a regression.

The cost ceilings
-----------------
:class:`Ceiling` holds an ABSOLUTE value in microseconds, chosen in advance.
It is absolute deliberately: ``new < old`` is not a pass criterion anywhere in
this repository (see :mod:`src.benchmarks.protocol` for why - intra-arm variance
alone is +/-2x, run-to-run 7.1x), so a relative comparison here would pass on
noise and prove nothing. :class:`CeilingExceeded` is what a measured cost over a
ceiling raises, and the acceptance tests call :func:`assert_cost_under_ceiling`
rather than asserting a number inline, so the "absolute, never new < old" rule
is enforced by the type and not by reviewer discipline.

Why a series store and not the metrics registry
-----------------------------------------------
The :mod:`src.app.core.monitoring.r1_unified_metrics` counters are a Prometheus
-shaped registry, and they are the right home for application counters that
should be scraped in production. These Phase-0 instruments are NOT that: they
are a bounded, serialisable record of one measured run, and the artefact needs
them as data, not as a scrape endpoint. Two shapes for two jobs - reusing the
Prometheus registry for a time series would give back cumulative counters that
cannot express "the loop stalled 740 ms at t=12.3 s", which is the entire
question.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

#: Every telemetry series carries the wall-clock instant it was taken, so a
#: stalled region can be located in the run rather than merely counted. Monotonic
#: time is not enough on its own: the artefact has to line these series up with
#: the load generator's timeline, which is wall clock.
SAMPLE_EPOCH = "sampled_at_unix"

#: Monotonic offset from the series' first sample, in seconds. Monotonic because
#: a wall clock can step backwards mid-run and make a stall look negative.
SAMPLE_OFFSET = "offset_s"


class CeilingExceeded(AssertionError):
    """A measured cost exceeded its absolute ceiling.

    An :class:`AssertionError` because that is what it is: a violated
    precondition of the instrument, not a runtime condition to handle. Raising it
    rather than logging it is deliberate - a telemetry tool that silently exceeds
    its cost budget and reports a number anyway produces exactly the corrupted
    baseline this package exists to prevent.
    """


@dataclass(frozen=True, slots=True)
class Ceiling:
    """An absolute cost budget, in microseconds, chosen before the measurement.

    Frozen because a ceiling that can be edited after the fact is not a ceiling.
    The comparison is ``measured > ceiling`` and never ``measured < previous``,
    which is the whole reason this type exists rather than a bare float.
    """

    microseconds: float
    what: str

    def check(self, measured_microseconds: float) -> None:
        """Raise :class:`CeilingExceeded` unless the measured cost is under."""
        if measured_microseconds > self.microseconds:
            raise CeilingExceeded(
                f"{self.what}: measured {measured_microseconds:.2f} us, "
                f"ceiling {self.microseconds:.2f} us. The ceiling is absolute "
                f"and was chosen in advance; it is never relaxed to accommodate "
                f"a measurement."
            )

    def as_dict(self) -> dict[str, Any]:
        return {"ceiling_us": self.microseconds, "what": self.what}


def assert_cost_under_ceiling(measured_microseconds: float, ceiling: Ceiling) -> float:
    """Check one measured cost against an absolute ceiling; return it unchanged.

    The acceptance criteria for every instrument in this package are phrased as
    "asserted below a stated fixed ceiling in microseconds - absolute, never new
    < old". Routing the check through here means that phrase is satisfied by
    construction rather than by a test author remembering it.
    """
    ceiling.check(measured_microseconds)
    return measured_microseconds


def measure_call_cost(callable_: Callable[[], object], *, repeats: int = 2000) -> float:
    """Per-call cost of ``callable_`` in microseconds: MINIMUM over ``repeats`` calls.

    Min-of-N, deliberately. This measures the cost of the CODE, not the cost of the
    environment it happens to run in, and the two need different estimators:

    - A **median** is dragged by scheduler preemption. Measured on this host,
      ``ProcessSeriesSampler.sample`` has a p50 of 15 us and a max of 139 us, and a
      test asserting its 50 us ceiling was failing at 72 us - which is neither the
      instrument's cost nor a breach of its ceiling. It is the runner, and a test
      that fails on runner noise trains its readers to ignore it.
    - A **mean** has the same problem, worse.
    - The **minimum** is right because the noise is strictly one-directional:
      within a batch only ONE call can be cheapest, and it is the one that hit no
      preemption, no page fault and no GC pause. The minimum approaches the code's
      true cost as the batch grows and cannot be inflated by anything the
      environment adds.

    What this reports is a FLOOR, not an average, and that is the intended reading
    for "is this instrument free at its production sampling rate": the question is
    whether the instrument's own work is cheap, and the answer is its uncontended
    cost. A deployment's real per-sample cost is higher, by exactly the scheduler
    tax above.

    ``time.perf_counter_ns`` rather than ``perf_counter`` so the subtraction cannot
    lose precision to a large second count.
    """
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    # Warm up first: the first call pays one-off costs that are not steady-state
    # (importing a module, faulting in a page). The warm-up count stays small
    # relative to `repeats` so a caller asking for N samples still gets N measured.
    warmup = max(1, min(repeats // 10, 200))
    for _ in range(warmup):
        callable_()
    counter = time.perf_counter_ns
    best: int | None = None
    for _ in range(repeats):
        start = counter()
        callable_()
        elapsed = counter() - start
        if best is None or elapsed < best:
            best = elapsed
    assert best is not None  # repeats >= 1
    return best / 1000.0


def measure_call_cost_robust(
    callable_: Callable[[], object],
    *,
    repeats: int = 2000,
    attempts: int = 5,
) -> float:
    """:func:`measure_call_cost` over ``attempts`` batches; the LOWEST result wins.

    Min-of-N answers the wrong question when the whole machine is busy. Under a
    loaded test runner the same function measures ~15 us standalone and ~74 us
    inside the full suite, because the process is competing for the same core with
    every other test - and the cost assertion then fails intermittently having
    measured the runner rather than the instrument.

    Taking the best of several batches does not change WHAT is estimated, it
    improves the estimate of the same quantity: each batch is already a min-of-N,
    and the lowest of several min-of-N batches is a better estimate of the
    uncontended cost than any one of them. A genuine regression moves every batch,
    so the lowest of them moves too and the assertion still fails.

    The acceptance tests use this, because the runner's load is not their subject.
    :func:`measure_call_cost` stays the primitive for a caller that wants the raw
    single-batch figure.
    """
    return min(measure_call_cost(callable_, repeats=repeats) for _ in range(max(1, attempts)))


class SeriesStore:
    """A bounded, ordered, serialisable record of samples for one instrument.

    Bounded on purpose. A sampler that runs for the whole run at >=100 Hz
    accumulates ~180 000 samples in 30 minutes; held unbounded in a list, that
    is the instrument leaking memory during the very measurement meant to detect
    a leak (T-7 measures RSS precisely so this cannot hide). The bound is
    applied on the way in - :meth:`record` drops the oldest sample once
    ``maxlen`` is reached - so the store's own footprint is flat and its cost
    does not grow with run length.

    The dropped-sample count is retained and exported. A series that silently
    stops recording would read exactly like a series that stopped having events,
    and those two are not the same fact.
    """

    __slots__ = ("_samples", "_maxlen", "_dropped", "_epoch", "_cursor")

    def __init__(self, *, maxlen: int = 65_536) -> None:
        if maxlen < 1:
            raise ValueError("maxlen must be >= 1")
        self._maxlen = maxlen
        self._samples: list[tuple[float, float, float]] = []
        self._dropped = 0
        self._epoch: float | None = None
        self._cursor = 0

    def __len__(self) -> int:
        return len(self._samples)

    @property
    def dropped(self) -> int:
        """Samples discarded because the store was full. Never silently zero."""
        return self._dropped

    def record(self, value: float, *, now: float | None = None) -> None:
        """Append one sample.

        ``now`` defaults to the monotonic clock. The wall-clock stamp is taken
        separately and once, here, rather than at each call site, so every series
        written by a single instrument shares one clock reading.
        """
        wall = time.time() if now is None else now
        mono = time.monotonic()
        if self._epoch is None:
            self._epoch = mono
        if len(self._samples) >= self._maxlen:
            self._dropped += 1
            # Ring-buffer eviction: overwrite the oldest slot rather than
            # pop(0), which is O(n) and would make the sampler's cost grow with
            # the number of samples already held.
            self._samples[self._cursor] = (wall, mono - self._epoch, value)
            self._cursor = (self._cursor + 1) % self._maxlen
            return
        self._samples.append((wall, mono - self._epoch, value))

    def values(self) -> list[float]:
        return [s[2] for s in self._samples]

    def export(self) -> dict[str, Any]:
        """Serialise for the artefact: samples in TIME order, plus the dropped count.

        Sorted by offset rather than emitted in slot order. Once the ring buffer
        has wrapped, slot order is arbitrary - the newest samples sit wherever
        the cursor left them - and an artefact whose series is not in time order
        cannot answer the only question it exists to answer, which is WHEN the
        loop stalled.
        """
        ordered = sorted(self._samples, key=lambda sample: sample[1])
        return {
            "count": len(ordered),
            "dropped": self._dropped,
            "samples": [
                {SAMPLE_EPOCH: wall, SAMPLE_OFFSET: round(offset, 6), "value": value}
                for wall, offset, value in ordered
            ],
        }

    def reset(self) -> None:
        self._samples.clear()
        self._dropped = 0
        self._epoch = None
        self._cursor = 0


class Instrument(Protocol):
    """What every instrument in this package is.

    The protocol is the contract the artefact depends on: an instrument can be
    enabled and disabled, records into its own store, and exports a
    JSON-serialisable snapshot including whether it was on. That last part is not
    decoration - Gate A and the Phase-0 void conditions require that a series
    absent from an artefact be distinguishable from a series that was never
    enabled.
    """

    name: str

    @property
    def enabled(self) -> bool: ...

    def export(self) -> dict[str, Any]: ...


@dataclass
class Counter:
    """A named integer counter with a total and a per-label breakdown.

    Deliberately not thread-safe. Every caller in this package is on the event
    loop's single thread (the sampler task, the middleware, the request path), so
    a lock would be cost paid on every sample to protect a race that cannot
    occur. If a future caller needs cross-thread counting it must take a lock at
    that call site, explicitly.
    """

    name: str
    totals: dict[str, int] = field(default_factory=dict)
    labels: dict[str, dict[str, int]] = field(default_factory=dict)

    def increment(self, key: str, *, by: int = 1, label: str | None = None) -> None:
        self.totals[key] = self.totals.get(key, 0) + by
        if label is not None:
            bucket = self.labels.setdefault(key, {})
            bucket[label] = bucket.get(label, 0) + by

    def get(self, key: str) -> int:
        return self.totals.get(key, 0)

    def total_events(self) -> int:
        """Sum across every key. Used by the ZERO-assertion instruments."""
        return sum(self.totals.values())

    def reset(self) -> None:
        self.totals.clear()
        self.labels.clear()

    def export(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "totals": dict(self.totals),
            "labels": {k: dict(v) for k, v in self.labels.items()},
        }


#: Which Phase-0 instruments are enabled, keyed by instrument name.
#:
#: Read by :mod:`src.app.core.monitoring.app_config_manifest` so the manifest can
#: record the enabled/disabled state (T-4/T-5/T-8 acceptance). It is a plain module
#: constant rather than a registry lookup because the manifest is read at run START,
#: often before any instrument has been constructed - and an absent flag must read
#: as "off", not as an error.
TELEMETRY_ENABLED_FLAGS: dict[str, bool] = {
    "loop_lag": False,
    "gc_pause": False,
    "request_timer": False,
    "status_distribution": False,
    "security_events": False,
    "redis_commands": False,
    "process_series": False,
}


def telemetry_enabled_flags() -> dict[str, bool]:
    """A copy of the enabled flags, for a manifest to serialise."""
    return dict(TELEMETRY_ENABLED_FLAGS)


class TelemetryRegistry:
    """The one place the Phase-0 instruments are registered and read.

    A single registry rather than a module-level global per instrument, because
    the artefact needs one coherent snapshot: if the harness scrapes two
    registries independently, a series can be read at t=1 and another at t=2 and
    the artefact then implies an ordering that never happened.
    """

    __slots__ = ("_instruments", "_order")

    def __init__(self) -> None:
        self._instruments: dict[str, Any] = {}
        self._order: list[str] = []

    def register(self, instrument: Any) -> Any:
        """Register one instrument; returns it so this can be a decorator."""
        name = instrument.name
        if name in self._instruments:
            raise ValueError(f"instrument already registered: {name}")
        self._instruments[name] = instrument
        self._order.append(name)
        return instrument

    def get(self, name: str) -> Any:
        return self._instruments[name]

    def names(self) -> tuple[str, ...]:
        """Registration order, which is also the export order."""
        return tuple(self._order)

    def export(self) -> dict[str, Any]:
        return {name: self._instruments[name].export() for name in self._order}

    def enabled_flags(self) -> dict[str, bool]:
        """Which instruments were on. Recorded in the manifest (T-8/T-4/T-5)."""
        return {name: bool(self._instruments[name].enabled) for name in self._order}

    def reset(self) -> None:
        for name in self._order:
            instrument = self._instruments[name]
            reset = getattr(instrument, "reset", None)
            if reset is not None:
                reset()


def status_class(status_code: int) -> str:
    """Bucket a status code the way the void conditions name it.

    The Phase-0 rule is "a phase returning 429/401 IS VOID", but the useful shape
    for a distribution is the class, because that is what reads at a glance in a
    log and what a per-class assertion can be written against. 2xx and 3xx are
    both "served"; everything else is a rejection and poisons a latency
    distribution.
    """
    if 200 <= status_code < 300:
        return "2xx"
    if 300 <= status_code < 400:
        return "3xx"
    if 400 <= status_code < 500:
        return "4xx"
    if status_code >= 500:
        return "5xx"
    return "1xx"


def non_2xx_keys(distribution: Mapping[str, int]) -> list[str]:
    """Keys in a status distribution that void a phase, sorted.

    Sorted so the message a harness prints is stable between runs; an unsorted
    set difference makes two identical failures print differently and trains
    nobody to read them.
    """
    return sorted(key for key, count in distribution.items() if count and key not in ("2xx", "3xx"))


def summarise(values: Sequence[float]) -> dict[str, float]:
    """Count/mean/median/min/max for a sample series, in the sample's own units.

    Percentiles are NOT computed here. ``src.benchmarks.percentiles`` owns that,
    with an explicit policy about which quantiles are unresolvable at a given n;
    a second, lazier percentile implementation here is how a p95 that silently
    interpolates differently from the harness's p95 gets into an artefact.
    """
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    return {
        "count": len(ordered),
        "min": ordered[0],
        "max": ordered[-1],
        "mean": sum(ordered) / len(ordered),
        "median": median,
    }
