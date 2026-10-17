"""T-7: CPU% and RSS series for the whole run, sampled on the APP HOST.

Two constraints carry the weight, and both are corrections of how the Round-1
pack measured memory.

APP HOST, NOT LOAD HOST
-----------------------
The co-location defect (H-9/P-3) put the load generator on the same machine as
the server. A process reading taken on the load host describes the wrong machine
under a two-host topology - it would report the generator's memory alongside the
server's and produce a number that is neither. This module records which host it
ran on and refuses to present a series whose host identity is unknown, because
"we did not record which host" and "the load host" look identical in an artefact
that omits the field.

THE WINDOW, NOT THE INTERVAL
----------------------------
The pack sampled every 4 ms across 5.81 s and got 365 samples. The interval was
fine. The WINDOW was the defect: 5.81 s cannot distinguish a leak from allocator
arena growth, and perf G14 is the proof - RSS grew 8.18 MB over 5.81 s while
``tracemalloc`` accounted for 1.865 MB. Two instruments disagreeing by an order of
magnitude is not a precision problem to be resolved by averaging them; it is the
finding.

So :data:`MINIMUM_WINDOW_SECONDS` is 30 minutes, per addendum A-8, and
:func:`assert_window_long_enough` fails a shorter run rather than annotating it.

tracemalloc ATTRIBUTES, NEVER CONCLUDES
---------------------------------------
This asymmetry is deliberate and comes from that measured disagreement. RSS is
the number that decides whether memory grew; ``tracemalloc`` is the number that
says which Python allocations might account for it. ``tracemalloc`` accounts for
1.865 MB of an 8.18 MB delta, so a conclusion drawn from ``tracemalloc`` alone
would assert a precision it does not have - and the missing 6.3 MB is exactly
where the interesting part is (native allocations, C extensions, the Redis client
buffers).

:func:`export` therefore reports both side by side and never emits a single
combined figure, and :func:`assert_tracemalloc_is_attribution_only` refuses an
export in which ``tracemalloc`` has been promoted to the concluding number.

SAMPLE-AND-EMIT
---------------
Sampling reads ``/proc`` and ``os`` counters and appends to a ring buffer. It does
not await anything, take a lock the request path wants, or allocate unboundedly.
On Linux it reads ``/proc/self/statm`` for RSS, which is a single small read;
:mod:`psutil` is used only if it is already importable, and its absence is
recorded rather than treated as an error, because an instrument that fails to
import cannot be the reason a run is unmeasured.
"""

from __future__ import annotations

import os
import time
from typing import Any

try:  # pragma: no cover - platform-dependent
    import resource
except ImportError:  # Windows has no resource module
    resource = None  # type: ignore[assignment]

from src.app.core.monitoring.telemetry import (
    Ceiling,
    SeriesStore,
    assert_cost_under_ceiling,
    summarise,
)
from src.app.core.monitoring.telemetry import (
    measure_call_cost_robust as measure_call_cost,
)

#: Addendum A-8: the RSS series must span >=30 minutes. Five minutes cannot
#: distinguish a leak from arena growth, and that is the specific defect being
#: corrected.
MINIMUM_WINDOW_SECONDS = 30 * 60

#: The pack's window, recorded so an artefact can show what it replaced.
PACK_WINDOW_SECONDS = 5.81

#: The sampler's per-tick cost budget. Absolute, chosen in advance. A sampler
#: that reads /proc every 4 ms is 250 reads per second; 50 us per read is 12.5 ms
#: of CPU per second, which is the "free at its production sampling rate" bar.
SAMPLER_COST_CEILING_US = 50.0
SAMPLER_COST_CEILING = Ceiling(SAMPLER_COST_CEILING_US, "T-7 per-sample cost")

#: Where RSS is read from on Linux. ``statm`` is one small read; the status file
#: is several and grows, so the smaller source is preferred.
_STATM = "/proc/self/statm"
_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


def _statm_is_readable() -> bool:
    """Whether ``/proc/self/statm`` can be read here. Evaluated ONCE.

    A module constant rather than a per-sample check because the answer cannot
    change while the process runs, and re-asking costs a syscall plus a raised
    exception on every sample on a host without ``/proc``.
    """
    try:
        fd = os.open(_STATM, os.O_RDONLY)
    except OSError:
        return False
    try:
        return bool(os.read(fd, 1))
    except OSError:
        return False
    finally:
        os.close(fd)


#: Resolved once at import. See :func:`_statm_is_readable`.
_STATM_AVAILABLE = _statm_is_readable()


def _schedstat_is_readable() -> bool:
    """Whether ``/proc/self/schedstat`` reports nanosecond CPU time. Evaluated ONCE.

    The distinction matters more than it looks: ``/proc/<pid>/stat`` reports
    utime/stime in clock ticks, so at the usual ``SC_CLK_TCK=100`` a cross-process
    CPU series quantises to 10 ms. That cannot resolve a 50 us budget, and a
    series that cannot resolve what it is compared against must not be compared
    against it. ``schedstat``'s first field is nanoseconds.
    """
    try:
        with open("/proc/self/schedstat", encoding="utf-8") as fh:
            return bool(int(fh.read().split()[0]))
    except (OSError, ValueError, IndexError):
        return False


#: Resolved once at import, for the same reason as ``_STATM_AVAILABLE``.
_SCHEDSTAT_AVAILABLE = _schedstat_is_readable()

#: Upper bound on how many cores' worth of CPU one sample interval may consume.
#: A sample cannot use more CPU than wall time x cores, so anything above that is
#: a measurement artefact rather than a fact about the process. Set to 4 because
#: the highest core count this project is measured on is a 4-core VM; a wider
#: bound would let a genuinely saturated machine's samples through, and the point
#: is to catch a mixed-population reader, not to cap real saturation.
_MAX_CPU_CORES_PER_SAMPLE = 4


def _max_rss_bytes() -> int | None:
    """Peak RSS from the kernel's own accounting, or ``None`` if unavailable.

    Less precise than ``/proc/self/statm`` - it is the process's high-water mark,
    not its current resident set - which is why it is a fallback and not the
    primary source. It is a fallback rather than a guess, and returns ``None``
    rather than a fabricated number on a platform that has neither: an RSS series
    of zeros is distinguishable from a real low reading, whereas an invented
    figure is not.
    """
    if resource is None:
        return None
    # Bound to a local first: mypy does not narrow a module that was assigned
    # ``None`` in an except branch, and this is the only read of it.
    getrusage = resource.getrusage  # type: ignore[attr-defined]
    usage = getrusage(resource.RUSAGE_SELF)  # type: ignore[attr-defined]
    return int(usage.ru_maxrss) * 1024

#: How the host identity was established. ``unrecorded`` is a state the export
#: refuses to present as a measurement.
HOST_RECORDED = "recorded"
HOST_UNRECORDED = "unrecorded"


class ProcessSeriesSampler:
    """Samples CPU% and RSS for this process, on this host, for the whole run."""

    name = "process_series"

    def __init__(self, *, enabled: bool = False, interval: float = 1.0,
                 maxlen: int = 8192) -> None:
        self._enabled = enabled
        self._interval = interval
        self._rss = SeriesStore(maxlen=maxlen)
        self._cpu = SeriesStore(maxlen=maxlen)
        self._started_at: float | None = None
        self._ended_at: float | None = None
        self._first_rss_bytes: int | None = None
        self._host_identity = HOST_UNRECORDED
        self._tracemalloc_peak_bytes: int | None = None
        # The workload the window covered. L-10 requires the RSS series to be
        # reported WITH the request count and concurrency it spans: a flat RSS
        # curve means "no leak" only relative to the traffic that produced it,
        # and the 20261011-190914 artefact could not supply either number for
        # its own window - the sampler simply had nowhere to record them.
        self._request_count: int | None = None
        self._concurrency: int | None = None
        # WHICH process this series describes. None means "me", which is right
        # when the sampler runs inside the app and wrong when it runs beside it:
        # a standalone scripts/rss_window.py would otherwise report the RSS of
        # its own small interpreter and call it a server-side measurement. That
        # is not a rounding error, it is the wrong instrument pointed at the
        # wrong process - so the target is named in the export, and a run that
        # never named one is visibly self-sampled rather than quietly so.
        self._target_pid: int | None = None
        self._target_name: str | None = None
        # CPU time is read as a delta between samples rather than a rate, because
        # a rate needs a denominator that changes with the interval and a delta
        # does not.
        self._last_cpu_seconds: float | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def record_host_identity(self, identity: str) -> None:
        """Record WHICH host this series describes.

        Required before the series is admissible. H-9 put the generator on the
        load host and the pack never said which machine a reading came from, so a
        two-host run can silently attribute the generator's memory to the server.
        """
        self._host_identity = identity

    def record_target(self, pid: int, name: str | None = None) -> None:
        """Point this series at another process, by PID.

        Required for any server-side claim made from outside the server. The
        default reads ``/proc/self/statm``, so a standalone sampler measures
        ITSELF unless told otherwise - which is how a 35-minute run produced a
        beautifully flat RSS curve for a small Python interpreter that was doing
        nothing but sleeping. The export names the target so a reader can tell
        which process the series describes, and records ``self`` when none was
        named rather than leaving it ambiguous.
        """
        self._target_pid = int(pid)
        self._target_name = name

    @staticmethod
    def _target_rss_bytes(pid: int) -> int:
        """Resident set size of ``pid``, from field 2 of ``/proc/<pid>/statm``."""
        fd = os.open(f"/proc/{pid}/statm", os.O_RDONLY)
        try:
            data = os.read(fd, 128)
        finally:
            os.close(fd)
        fields = data.split()
        if len(fields) < 2:
            raise OSError(f"/proc/{pid}/statm returned only {len(fields)} fields")
        return int(fields[1]) * _PAGE_SIZE

    @staticmethod
    def _target_cpu_resolution() -> str:
        """Which reader this host supports, so a coarse series is never silent.

        ``/proc/<pid>/schedstat`` reports nanoseconds; ``/proc/<pid>/stat``
        reports clock TICKS, which at the usual SC_CLK_TCK=100 quantises to
        10 ms. That is not a small error: the 20261012 cross-process run
        reported a median of exactly 10000 us against a 50 us ceiling, which
        was the tick granularity rather than any real cost. A series that
        cannot resolve the thing it is being compared against must say so.
        """
        return "nanoseconds" if _SCHEDSTAT_AVAILABLE else "clock-ticks"

    @staticmethod
    def _target_cpu_seconds(pid: int) -> float:
        """CPU seconds for ``pid``, from ONE reader chosen once and never mixed.

        ``/proc/<pid>/schedstat`` field 0 is nanoseconds on CPU for the MAIN
        THREAD. ``/proc/<pid>/stat`` utime+stime is ticks for the WHOLE PROCESS.
        Those are not two resolutions of one quantity - they are two different
        quantities, and the process total is the larger.

        So the previous "try schedstat, fall back to stat" was a trap: one
        transient read failure mid-run switched the series from thread-only to
        process-wide, and the delta across that switch was reported as a single
        52-second sample of CPU consumed inside a 1-second interval. The reader
        is therefore bound per sampler and never changes underneath a series.

        The ``stat`` parser skips to after the LAST ``)`` rather than splitting
        the whole line: the parenthesised comm field can itself contain spaces
        and parentheses, and parsing that wrongly corrupts the series silently
        instead of raising.
        """
        if _SCHEDSTAT_AVAILABLE:
            with open(f"/proc/{pid}/schedstat", encoding="utf-8") as fh:
                return int(fh.read().split()[0]) / 1_000_000_000.0
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            raw = fh.read()
        rest = raw[raw.rindex(")") + 1 :].split()
        ticks = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
        # rest[0] is state (field 3), so utime (14) and stime (15) are 11 and 12.
        return float(int(rest[11]) + int(rest[12])) / ticks

    @staticmethod
    def _target_cpu_scope() -> str:
        """Which quantity this host's reader actually reports.

        schedstat counts the main THREAD; stat counts the whole PROCESS. Stating
        it matters because the two are not comparable, so a series must never be
        read as if it were the other's.
        """
        return "main-thread" if _SCHEDSTAT_AVAILABLE else "whole-process"

    def start(self) -> None:
        if not self._enabled:
            return
        self._started_at = time.monotonic()
        self._last_cpu_seconds = self._cpu_seconds()
        self.sample()

    def stop(self) -> None:
        if not self._enabled:
            return
        self.sample()
        self._ended_at = time.monotonic()

    def sample(self) -> None:
        """Take one sample. Returns immediately when disabled.

        Reads the TARGET process when one was named and this process otherwise.
        A target that exits mid-run is not silently replaced by this process's
        numbers: the read fails, the sample is skipped, and the window still
        reports its true sample count. Swapping in our own RSS would turn a dead
        server into a flat, healthy-looking line - which is the exact shape of
        confident error this programme exists to catch.
        """
        if not self._enabled:
            return
        pid = self._target_pid
        try:
            if pid is None:
                rss = self._rss_bytes()
                cpu_seconds = self._cpu_seconds()
            else:
                rss = self._target_rss_bytes(pid)
                cpu_seconds = self._target_cpu_seconds(pid)
        except (OSError, ValueError, IndexError):
            return
        self._rss.record(float(rss))
        if self._first_rss_bytes is None:
            self._first_rss_bytes = rss
        if self._last_cpu_seconds is not None:
            delta = cpu_seconds - self._last_cpu_seconds
            # A CPU delta cannot exceed the wall time between two samples. It
            # exceeded 50x in the 20261012 cross-process run, because the reader
            # switched from thread-only to process-wide mid-series. Discarding
            # the impossible sample - rather than clamping or recording it - is
            # what keeps a mixed-population series from reading as a real spike.
            if 0.0 <= delta <= self._interval * _MAX_CPU_CORES_PER_SAMPLE:
                self._cpu.record(delta)
        self._last_cpu_seconds = cpu_seconds

    # -- the readings ------------------------------------------------------

    @staticmethod
    def _rss_bytes() -> int:
        """Resident set size in bytes, or 0 where the platform cannot report it.

        ``/proc/self/statm`` field 2 is resident pages, times the page size. Read
        with ``os.open``/``os.read`` rather than ``Path.read_text`` so a failure
        cannot raise into the sampler and stop the series mid-run - a sampler that
        raised on one bad read would lose exactly the data it exists to preserve.

        THE CAPABILITY CHECK IS DONE ONCE (:data:`_STATM_AVAILABLE`), and that is
        not a micro-optimisation. Probing ``/proc`` per sample meant a host without
        it paid a failing syscall and a raised-and-caught exception on EVERY
        sample - pure waste, and enough of it to push this instrument past its own
        cost ceiling under load: the T-7 ceiling test was failing at 69 us against
        a 50 us ceiling for a function whose steady-state cost is 15 us. That gap
        was this, not the scheduler.
        """
        if _STATM_AVAILABLE:
            try:
                fd = os.open(_STATM, os.O_RDONLY)
                try:
                    data = os.read(fd, 128)
                finally:
                    os.close(fd)
                fields = data.split()
                if len(fields) >= 2:
                    return int(fields[1]) * _PAGE_SIZE
            except (OSError, ValueError, IndexError):
                # /proc exists but did not answer this time; fall through.
                pass
        return _max_rss_bytes() or 0

    @staticmethod
    def _cpu_seconds() -> float:
        """User+system CPU seconds for this process so far.

        ``time.process_time`` rather than ``resource.getrusage``: it is the same
        quantity, it is available on every platform this project runs on, and it
        excludes the children a ``RUSAGE_SELF``-vs-``RUSAGE_CHILDREN`` mistake
        would silently fold in.
        """
        return time.process_time()

    # -- the reporting -----------------------------------------------------

    @property
    def window_seconds(self) -> float:
        if self._started_at is None or self._ended_at is None:
            return 0.0
        return self._ended_at - self._started_at

    def export(self) -> dict[str, Any]:
        """Serialise both series, and tracemalloc as attribution beside RSS.

        Two instruments side by side, never one combined figure: the disagreement
        between them is the finding (perf G14), and averaging them into a single
        "memory" number would erase the only thing worth knowing.
        """
        rss_values = self._rss.values()
        return {
            "instrument": self.name,
            # WHICH process the series describes. "is_self" is stated rather than
            # implied, so a reader is never left guessing whether a series that
            # looks server-side actually watched the server.
            "target": {
                "pid": self._target_pid,
                "name": self._target_name,
                "is_self": self._target_pid is None,
            },
            "enabled": self._enabled,
            "host_identity": self._host_identity,
            "host_recorded": self._host_identity != HOST_UNRECORDED,
            "window_seconds": round(self.window_seconds, 3),
            "window_meets_minimum": self.window_seconds >= MINIMUM_WINDOW_SECONDS,
            "minimum_window_seconds": MINIMUM_WINDOW_SECONDS,
            "pack_window_seconds_superseded": PACK_WINDOW_SECONDS,
            "sample_count": len(self._rss),
            "interval_seconds": self._interval,
            # RSS is the concluding instrument.
            "rss_series_bytes": self._rss.export(),
            "rss_summary_bytes": summarise(rss_values),
            "rss_first_bytes": self._first_rss_bytes,
            "rss_last_bytes": rss_values[-1] if rss_values else None,
            "rss_growth_bytes": (
                rss_values[-1] - self._first_rss_bytes
                if rss_values and self._first_rss_bytes is not None
                else None
            ),
            # tracemalloc attributes; it never concludes.
            "tracemalloc_peak_bytes": self._tracemalloc_peak_bytes,
            "tracemalloc_role": "attribution-only",
            # The workload this window covered. Absent rather than defaulted: a
            # series carrying no traffic figures is a series whose "flat" cannot
            # be weighed against anything, which is what L-10 exists to prevent.
            "workload": {
                "request_count": self._request_count,
                "concurrency": self._concurrency,
                "recorded": self._request_count is not None,
            },
            "tracemalloc_divergence": self._tracemalloc_divergence(),
            "cpu_series_seconds": self._cpu.export(),
            "cpu_summary_seconds": summarise(self._cpu.values()),
            # The resolution this CPU series was ACTUALLY measured at. A reader
            # comparing a clock-tick series (10 ms quantisation) against a
            # microsecond ceiling would conclude the sampler is 200x over budget
            # when it is merely unresolvable - which is exactly what the
            # 20261012 cross-process run reported. Stated so it cannot recur.
            "cpu_resolution": (
                "clock-ticks"
                if self._target_pid is not None and not _SCHEDSTAT_AVAILABLE
                else "nanoseconds"
            ),
            "cpu_resolution_note": (
                "clock-ticks quantises to 1/SC_CLK_TCK seconds, so this series "
                "CANNOT resolve a microsecond cost ceiling and must not be "
                "compared against one."
                if self._target_pid is not None and not _SCHEDSTAT_AVAILABLE
                else None
            ),
            # WHICH quantity a cross-process CPU series counts. schedstat
            # reports the MAIN THREAD; the stat fallback reports the WHOLE
            # PROCESS. Those are different numbers, so a cross-process series
            # must never be read against a process-wide ceiling - it is a
            # different population, not merely a coarser one.
            "cpu_scope": (
                "main-thread"
                if self._target_pid is not None and _SCHEDSTAT_AVAILABLE
                else "whole-process"
            ),
            "cost_ceiling_us": SAMPLER_COST_CEILING_US,
        }

    def reset(self) -> None:
        self._rss.reset()
        self._cpu.reset()
        self._started_at = None
        self._ended_at = None
        self._first_rss_bytes = None
        self._last_cpu_seconds = None

    def record_tracemalloc_peak(self, peak_bytes: int) -> None:
        """Record a tracemalloc peak for attribution alongside the RSS series."""
        self._tracemalloc_peak_bytes = peak_bytes

    def record_workload(self, *, request_count: int, concurrency: int) -> None:
        """Record the traffic this window covered.

        Set by the caller rather than inferred: the sampler watches one process
        and cannot know how many requests the load generator sent, or at what
        concurrency. A recorded value is a claim the caller owns; an inferred one
        would be a guess wearing the same field name.
        """
        self._request_count = int(request_count)
        self._concurrency = int(concurrency)

    def _rss_growth_bytes(self) -> float | None:
        values = self._rss.values()
        if not values or self._first_rss_bytes is None:
            return None
        return values[-1] - self._first_rss_bytes

    def _tracemalloc_divergence(self) -> dict[str, Any]:
        """The disagreement between RSS growth and tracemalloc: stated, not resolved.

        L-10 asks for the instruments to be cross-checked with the divergence
        REPORTED rather than resolved by fiat. Two honest outcomes exist and both
        carry information: tracemalloc accounts for part of the RSS delta and the
        remainder is allocator arenas and non-Python allocations, or tracemalloc
        was never enabled for this window and there is nothing to compare.

        The second is reported as its own ``status`` rather than left as a bare
        null, because a silent null is indistinguishable from "the difference was
        zero" - which is exactly how the 20261011-190914 artefact's
        ``tracemalloc_peak_bytes: null`` read, and why its divergence could not be
        assessed at all.
        """
        growth = self._rss_growth_bytes()
        peak = self._tracemalloc_peak_bytes
        if peak is None:
            return {
                "status": "not-available",
                "reason": (
                    "tracemalloc was not enabled for this window, so RSS has no "
                    "second instrument to be compared against. tracemalloc is "
                    "attribution-only and enabling it perturbs the series it would "
                    "cross-check, so it runs in a companion window."
                ),
                "rss_growth_bytes": growth,
                "tracemalloc_accounted_bytes": None,
            }
        accounted = max(0, min(peak, growth)) if growth is not None and growth > 0 else 0
        return {
            "status": "compared",
            "rss_growth_bytes": growth,
            "tracemalloc_peak_bytes": peak,
            "tracemalloc_accounted_bytes": accounted,
            "unattributed_bytes": (growth - accounted) if growth is not None else None,
        }


def assert_window_long_enough(window_seconds: float,
                              *, minimum: float = MINIMUM_WINDOW_SECONDS) -> float:
    """Fail a run whose RSS window is too short to distinguish a leak.

    Not an annotation. A 5.81 s window cannot tell retained growth from allocator
    arena growth, and perf G14's numbers came from exactly such a window - so a
    short window does not produce a weaker conclusion, it produces a conclusion
    that is known to be unsupported before it is drawn.
    """
    if window_seconds < minimum:
        raise AssertionError(
            f"T-7 window {window_seconds:.1f}s is below the {minimum:.0f}s minimum "
            f"(addendum A-8). A window this short cannot distinguish a leak from "
            f"allocator arena growth; the pack's {PACK_WINDOW_SECONDS}s window is "
            f"the defect being corrected, not an acceptable substitute."
        )
    return window_seconds


def assert_host_recorded(export: dict[str, Any]) -> dict[str, Any]:
    """Refuse an RSS series whose host identity was never recorded.

    H-9's whole point is that the load host and the app host are different
    machines. A series that does not say which one it came from cannot be checked
    against that rule, and an unchecked memory figure from a two-host run is the
    exact confusion this instrument exists to prevent.
    """
    if export.get("host_recorded"):
        return export
    raise AssertionError(
        "T-7 series has no recorded host identity. Under H-9 the load host and "
        "the app host are different machines, so a series that does not say which "
        "one it describes cannot be attributed to the server."
    )


def assert_tracemalloc_is_attribution_only(export: dict[str, Any]) -> dict[str, Any]:
    """Refuse an export in which tracemalloc has become the concluding number.

    Fails if there is a tracemalloc value with no RSS value beside it, or if
    tracemalloc has been renamed to a key that reads as a conclusion. Both are the
    ways an attribution slips into being the answer - and perf G14 is the reason
    it must not: tracemalloc accounted for 1.865 MB of an 8.18 MB RSS delta, so a
    conclusion drawn from it alone would assert about 4x the precision it has.
    """
    has_tracemalloc = export.get("tracemalloc_peak_bytes") is not None
    has_rss = export.get("rss_summary_bytes", {}).get("count", 0) > 0
    if has_tracemalloc and not has_rss:
        raise AssertionError(
            "T-7 export carries a tracemalloc peak with no RSS series beside it. "
            "tracemalloc attributes and never concludes (addendum A-8); the two "
            "must be reported side by side so their disagreement stays visible."
        )
    # The divergence block is the ONE shape that legitimately sits under a
    # tracemalloc* key: it exists to put the two instruments side by side, which
    # is what this guard exists to enforce. It is allowed only if it actually
    # carries an RSS figure beside the tracemalloc one - otherwise it would be a
    # tracemalloc number wearing the name of a comparison, and the guard's
    # original objection would apply in full.
    divergence = export.get("tracemalloc_divergence")
    if divergence is not None:
        if not isinstance(divergence, dict) or divergence.get("rss_growth_bytes") is None:
            raise AssertionError(
                "T-7 export carries 'tracemalloc_divergence' with no RSS figure "
                "beside it, so it reports a tracemalloc number rather than a "
                "disagreement between two instruments. Either include "
                "rss_growth_bytes or drop the block."
            )
    for key in export:
        if (
            "tracemalloc" in key
            and "role" not in key
            and "peak" not in key
            and key != "tracemalloc_divergence"
        ):
            raise AssertionError(
                f"T-7 export promotes tracemalloc into key {key!r}; it may only be "
                f"reported as an attribution beside RSS."
            )
    return export


def assert_cost(measured_us: float) -> float:
    """Check the sampler's measured per-sample cost against its ceiling."""
    return assert_cost_under_ceiling(measured_us, SAMPLER_COST_CEILING)


def measure_sampler_cost(*, repeats: int = 5000) -> dict[str, Any]:
    """Measure the sampler's per-tick cost against its absolute ceiling."""
    sampler = ProcessSeriesSampler(enabled=True)

    def one_sample() -> None:
        sampler.sample()

    cost_us = measure_call_cost(one_sample, repeats=repeats)
    assert_cost(cost_us)
    return {"measured_us": round(cost_us, 4), "ceiling_us": SAMPLER_COST_CEILING_US}
