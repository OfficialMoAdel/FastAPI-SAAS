"""The microbenchmark protocol, in one place: best_of(min-of-N, median-of-M).

Every microbenchmark number this project publishes is supposed to be
comparable by construction rather than by argument. That is the whole point of
standardising on one runner, and it is why the protocol lives here rather than
being re-implemented per benchmark file.

Why the two-level estimator
---------------------------
``min-of-N`` and ``median-of-M`` solve different problems and neither alone is
enough. Within one batch, a single call can be made slow by a GC pause, a page
fault, or the scheduler descheduling the thread; taking the minimum over N calls
measures the code's true cost because the noise is strictly one-directional.
But across M batches a plain minimum is not enough either: on a loaded runner
every batch can catch a spike, and the winner is then whichever batch happened
to dodge one - a number that is lucky rather than true. So: minimum within a
batch, median across batches.

This is strictly stronger than the "median of batch means" that several
benchmark files use today. A mean lets one slow call inside an otherwise fast
batch drag that batch's figure up, and the median then inherits it; a minimum
cannot.

Why the negative form is banned
-------------------------------
``new < old`` is not a pass criterion. Demanding the new number be smaller than
the old one passes on noise - the noise floor for this workload is +/-2x
intra-arm and 7.1x run-to-run (perf E1 recorded 1808 ms vs 4059 ms for the same
arm on the same 12-CPU box; perf C11 recorded /users/me p50 at 7.36 vs 9.81 ms
and refresh at 19.96 vs 66.83 ms). The only sound form is a fixed ceiling
chosen in advance, which is why :class:`Ceiling` holds an absolute value and
:class:`RelativeComparisonRejected` exists: the API cannot express a relative
comparison in the first place.

Why a bare number is not admissible
-----------------------------------
A number without its context is not evidence. Every emitted figure carries the
OS, the event loop, the CPU count, the sample count and the observed spread,
because a 40 us figure measured under a different event loop on a different
core count is a different quantity wearing the same clothes. :func:`render` is
the only sanctioned way to emit one.

Request scale
-------------
At request scale the per-operation cost is milliseconds and the variance is far
larger, so the protocol additionally requires at least
:data:`MIN_REQUEST_REPETITIONS` repetitions and reports both median-of-medians
and min-of-medians rather than collapsing them to one figure.
"""

from __future__ import annotations

import asyncio
import gc
import math
import os
import platform
import statistics
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final

__all__ = [
    "MIN_ITERATIONS",
    "MIN_REQUEST_REPETITIONS",
    "MIN_WARMUP",
    "NOISE_FLOOR_RATIO",
    "Ceiling",
    "MicroResult",
    "ProtocolViolation",
    "RelativeComparisonRejected",
    "RunStamp",
    "Verdict",
    "measure",
    "measure_async",
    "measure_request_scale",
    "render",
    "stamp",
    "verdict",
]

#: A run with no warm-up measures import-time and first-call costs, not steady
#: state. The protocol does not merely default this to non-zero - it refuses a
#: zero, because the failure it prevents (a number that is an order of
#: magnitude too high, blamed on the code) is exactly the one that wastes an
#: investigation.
MIN_WARMUP: Final[int] = 1

#: Below this many iterations a batch's minimum is dominated by the clock's own
#: resolution rather than by the code.
MIN_ITERATIONS: Final[int] = 1

#: Request-scale work needs at least this many repetitions before
#: median-of-medians means anything.
MIN_REQUEST_REPETITIONS: Final[int] = 5

#: Intra-arm spread above this is noise, not a result (perf J13). A delta
#: smaller than this cannot be distinguished from the machine being busy.
NOISE_FLOOR_RATIO: Final[float] = 2.0

#: ``time.perf_counter`` reports seconds. Every figure this module publishes is
#: documented and named in microseconds (``Ceiling.value_us``, ``best_us``,
#: ``batch_mins_us``, ``reportable_us``), so every sample is scaled at the point
#: it is taken. The conversion lives here, once, rather than at the call sites:
#: a caller-side conversion is what let the un-scaled seconds leak out as
#: ``*_us`` in the first place.
_US_PER_SECOND: Final[float] = 1_000_000.0


class ProtocolViolation(RuntimeError):
    """A run was configured in a way that cannot produce a sound number."""


class RelativeComparisonRejected(ProtocolViolation):
    """A relative comparison was offered where an absolute ceiling is required."""


@dataclass(frozen=True, slots=True)
class Ceiling:
    """An absolute bound in microseconds, chosen before the run.

    Only an absolute value can be constructed. There is deliberately no way to
    build one of these from another measurement, because a ceiling derived from
    a previous run is just ``new < old`` wearing a different hat.
    """

    value_us: float
    label: str = "ceiling"

    def __post_init__(self) -> None:
        if not math.isfinite(self.value_us):
            raise ProtocolViolation(f"a ceiling must be finite, got {self.value_us!r}")
        if self.value_us <= 0:
            raise ProtocolViolation(f"a ceiling must be positive, got {self.value_us!r}")


@dataclass(frozen=True, slots=True)
class RunStamp:
    """The conditions a number was produced under. A number without these is not evidence."""

    platform: str
    release: str
    machine: str
    cpu_count: int | None
    event_loop: str
    python: str

    def one_line(self) -> str:
        return (
            f"platform={self.platform}/{self.release} machine={self.machine} "
            f"cpu_count={self.cpu_count} event_loop={self.event_loop} python={self.python}"
        )


def _event_loop_name() -> str:
    try:
        return type(asyncio.get_running_loop()).__name__
    except RuntimeError:
        pass
    try:
        policy = type(asyncio.get_event_loop_policy()).__module__
    except Exception:  # noqa: BLE001 - a policy that cannot be introspected must not fail the benchmark
        return "unknown"
    return policy.rsplit(".", 1)[-1]


def stamp() -> RunStamp:
    """Capture the conditions of this run. Called for you by every measure()."""
    return RunStamp(
        platform=platform.system(),
        release=platform.release(),
        machine=platform.machine(),
        cpu_count=os.cpu_count(),
        event_loop=_event_loop_name(),
        python=platform.python_version(),
    )


@dataclass(frozen=True, slots=True)
class MicroResult:
    """One measurement, with the estimator applied and the context attached."""

    name: str
    unit: str
    #: min-of-N inside a batch, then median of the per-batch minima. This is the
    #: protocol's headline number.
    best_us: float
    #: Every per-batch minimum, sorted. Exposed so a caller can check the
    #: spread rather than trusting a single figure.
    batch_mins_us: tuple[float, ...]
    #: The raw median of each batch, which is what a mean-based runner would
    #: have reported. Kept so the two estimators can be compared, not because it
    #: is the answer.
    batch_medians_us: tuple[float, ...]
    iterations: int
    repetitions: int
    warmup: int
    run: RunStamp = field(default_factory=stamp)

    @property
    def spread_ratio(self) -> float:
        """max(batch minima) / min(batch minima).

        Above :data:`NOISE_FLOOR_RATIO` this arm is not self-consistent and no
        delta against another arm is a result.
        """
        if not self.batch_mins_us:
            return 0.0
        lowest = min(self.batch_mins_us)
        if lowest <= 0:
            return math.inf
        return max(self.batch_mins_us) / lowest

    @property
    def is_inside_noise(self) -> bool:
        return self.spread_ratio < NOISE_FLOOR_RATIO

    def reportable_us(self) -> float:
        """The figure a human should quote, in microseconds."""
        return self.best_us


@dataclass(frozen=True, slots=True)
class Verdict:
    """The outcome of comparing a measurement against a fixed ceiling."""

    passed: bool
    name: str
    measured_us: float
    ceiling_us: float
    spread_ratio: float
    message: str

    def assert_passed(self) -> None:
        if not self.passed:
            raise AssertionError(self.message)


def _validate(*, iterations: int, warmup: int, repetitions: int) -> None:
    if warmup < MIN_WARMUP:
        raise ProtocolViolation(
            f"warmup={warmup} measures cold-start, not steady state; the protocol requires "
            f"warmup >= {MIN_WARMUP}. Pass a real warm-up count."
        )
    if iterations < MIN_ITERATIONS:
        raise ProtocolViolation(f"iterations={iterations} cannot be timed; minimum is {MIN_ITERATIONS}.")
    if repetitions < 1:
        raise ProtocolViolation(f"repetitions={repetitions} produces no median; minimum is 1.")


def _collect(
    fn: Callable[[], Any],
    *,
    iterations: int,
    warmup: int,
    repetitions: int,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    _validate(iterations=iterations, warmup=warmup, repetitions=repetitions)
    for _ in range(warmup):
        fn()
    batch_mins: list[float] = []
    batch_medians: list[float] = []
    for _ in range(repetitions):
        samples = [0.0] * iterations
        gc.collect()
        for index in range(iterations):
            call_start = time.perf_counter()
            fn()
            samples[index] = (time.perf_counter() - call_start) * _US_PER_SECOND
        samples.sort()
        batch_mins.append(samples[0])
        batch_medians.append(statistics.median(samples))
    return tuple(batch_mins), tuple(batch_medians)


def _build(
    name: str,
    unit: str,
    batch_mins: tuple[float, ...],
    batch_medians: tuple[float, ...],
    *,
    iterations: int,
    warmup: int,
    repetitions: int,
) -> MicroResult:
    ordered = sorted(batch_mins)
    middle = len(ordered) // 2
    best = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    return MicroResult(
        name=name,
        unit=unit,
        best_us=best,
        batch_mins_us=batch_mins,
        batch_medians_us=batch_medians,
        iterations=iterations,
        repetitions=repetitions,
        warmup=warmup,
    )


def measure(
    fn: Callable[[], Any],
    *,
    name: str,
    iterations: int = 2000,
    warmup: int = 100,
    repetitions: int = 5,
    unit: str = "us",
) -> MicroResult:
    """Time ``fn`` under the protocol: warm up, collect, min-of-N then median-of-M.

    ``repetitions`` defaults to 5, not to 1: with a single batch there is no
    median, and a single batch's minimum is the luckiest measurement rather
    than the truest one.
    """
    mins, medians = _collect(fn, iterations=iterations, warmup=warmup, repetitions=repetitions)
    return _build(name, unit, mins, medians, iterations=iterations, warmup=warmup, repetitions=repetitions)


async def measure_async(
    fn: Callable[[], Awaitable[Any]],
    *,
    name: str,
    iterations: int = 200,
    warmup: int = 20,
    repetitions: int = 5,
    unit: str = "us",
) -> MicroResult:
    """Async twin of :func:`measure`. ``fn`` must be an awaitable-returning callable.

    Warm-up and the collection loop are awaited in sequence rather than
    gathered, because concurrency is exactly the variable under test.
    """
    _validate(iterations=iterations, warmup=warmup, repetitions=repetitions)
    for _ in range(warmup):
        await fn()
    batch_mins: list[float] = []
    batch_medians: list[float] = []
    for _ in range(repetitions):
        samples = [0.0] * iterations
        gc.collect()
        for index in range(iterations):
            call_start = time.perf_counter()
            await fn()
            samples[index] = (time.perf_counter() - call_start) * _US_PER_SECOND
        samples.sort()
        batch_mins.append(samples[0])
        batch_medians.append(statistics.median(samples))
    return _build(
        name,
        unit,
        tuple(batch_mins),
        tuple(batch_medians),
        iterations=iterations,
        warmup=warmup,
        repetitions=repetitions,
    )


@dataclass(frozen=True, slots=True)
class RequestResult:
    """Request-scale output: two figures, because one hides the spread."""

    name: str
    #: Median across repetitions of each repetition's median.
    median_of_medians_us: float
    #: Median across repetitions of each repetition's minimum.
    min_of_medians_us: float
    per_repetition_medians_us: tuple[float, ...]
    per_repetition_mins_us: tuple[float, ...]
    iterations: int
    repetitions: int
    warmup: int
    unit: str = "us"
    run: RunStamp = field(default_factory=stamp)

    @property
    def spread_ratio(self) -> float:
        if not self.per_repetition_medians_us:
            return 0.0
        lowest = min(self.per_repetition_medians_us)
        if lowest <= 0:
            return math.inf
        return max(self.per_repetition_medians_us) / lowest

    @property
    def is_inside_noise(self) -> bool:
        return self.spread_ratio < NOISE_FLOOR_RATIO


async def measure_request_scale(
    fn: Callable[[], Awaitable[Any]],
    *,
    name: str,
    iterations: int = 50,
    warmup: int = 5,
    repetitions: int = MIN_REQUEST_REPETITIONS,
    unit: str = "us",
) -> RequestResult:
    """Request-scale extension of the protocol (unified plan §4.4).

    Unlike :func:`measure_async` this refuses fewer than
    :data:`MIN_REQUEST_REPETITIONS` repetitions. At request scale the
    inter-request variance dominates, so a single run's median is not a
    measurement - it is one observation of how busy the machine was.
    """
    if repetitions < MIN_REQUEST_REPETITIONS:
        raise ProtocolViolation(
            f"request scale requires at least {MIN_REQUEST_REPETITIONS} repetitions, got {repetitions}; "
            "fewer than that measures the machine, not the endpoint."
        )
    _validate(iterations=iterations, warmup=warmup, repetitions=repetitions)
    for _ in range(warmup):
        await fn()
    medians: list[float] = []
    mins: list[float] = []
    for _ in range(repetitions):
        samples = [0.0] * iterations
        gc.collect()
        for index in range(iterations):
            call_start = time.perf_counter()
            await fn()
            samples[index] = (time.perf_counter() - call_start) * _US_PER_SECOND
        ordered = sorted(samples)
        mins.append(ordered[0])
        medians.append(statistics.median(ordered))
    return RequestResult(
        name=name,
        median_of_medians_us=statistics.median(medians),
        min_of_medians_us=statistics.median(mins),
        per_repetition_medians_us=tuple(medians),
        per_repetition_mins_us=tuple(mins),
        iterations=iterations,
        repetitions=repetitions,
        warmup=warmup,
        unit=unit,
    )


def render(result: MicroResult | RequestResult) -> str:
    """The only admissible way to emit a microbenchmark number.

    Includes OS, event loop, CPU count, sample count and the observed spread,
    because a bare figure cannot be checked against anything and a spread that
    is not printed is a spread nobody notices is noise.
    """
    run = result.run.one_line()
    if isinstance(result, RequestResult):
        return (
            f"{result.name}: median-of-medians {result.median_of_medians_us:.2f}{result.unit}, "
            f"min-of-medians {result.min_of_medians_us:.2f}{result.unit} | "
            f"n={result.iterations}x{result.repetitions} warmup={result.warmup} | "
            f"spread={result.spread_ratio:.2f}x | {run}"
        )
    return (
        f"{result.name}: best_of(min-of-{result.iterations}, median-of-{result.repetitions}) "
        f"= {result.best_us:.2f}{result.unit} | "
        f"batch_mins={[round(v, 3) for v in sorted(result.batch_mins_us)]} | "
        f"batch_medians={[round(v, 3) for v in sorted(result.batch_medians_us)]} | "
        f"n={result.iterations}x{result.repetitions} warmup={result.warmup} | "
        f"spread={result.spread_ratio:.2f}x | {run}"
    )


def verdict(result: MicroResult | RequestResult, ceiling: Ceiling) -> Verdict:
    """Compare a measurement against a FIXED ceiling.

    The ceiling is absolute by construction. A below-ceiling regression is a
    pass here and must not be, which is the point: the ceiling is the
    specification, and movement under it is only a result when it exceeds the
    noise floor.
    """
    if not isinstance(ceiling, Ceiling):
        raise RelativeComparisonRejected(
            f"{ceiling!r} is not an absolute ceiling. `new < old` is not a pass criterion: it "
            "passes on noise. Construct a Ceiling(value_us=...) instead."
        )
    measured = result.best_us if isinstance(result, MicroResult) else result.median_of_medians_us
    passed = measured < ceiling.value_us
    noise_note = (
        " spread is inside the noise floor, so any comparison against another arm is not a result"
        if result.is_inside_noise
        else f" spread {result.spread_ratio:.2f}x already exceeds the {NOISE_FLOOR_RATIO}x noise floor"
    )
    if passed:
        message = (
            f"{result.name} {measured:.2f}{result.unit} is under the {ceiling.label} ceiling "
            f"{ceiling.value_us:.2f}{result.unit};" + noise_note
        )
    else:
        message = (
            f"{result.name} {measured:.2f}{result.unit} EXCEEDS the {ceiling.label} ceiling "
            f"{ceiling.value_us:.2f}{result.unit} by {measured / ceiling.value_us:.2f}x;" + noise_note
        )
    return Verdict(
        passed=passed,
        name=result.name,
        measured_us=measured,
        ceiling_us=ceiling.value_us,
        spread_ratio=result.spread_ratio,
        message=message,
    )
