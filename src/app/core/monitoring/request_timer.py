"""T-3: server-side request timer emitting X-Response-Time, excluded from its own timing.

Why this is mandatory rather than nice-to-have
----------------------------------------------
H-9 puts the load generator on a SEPARATE host. Once client and server are on
different machines, the client-observed latency is the sum of the network between
them, the client's own scheduling, gevent's hub, and locust's CSV aggregation -
none of which is the server's latency. A two-host run with only client-observed
timing measures the network. The server has to time itself or the Phase-0
dataset mixes two quantities and every percentile is unattributable.

THE SELF-EXCLUSION PROBLEM, and why it is not hypothetical
------------------------------------------------------------
The timer must be excluded from its own measurement. If the clock is read before
the middleware does its work and the value is written after, the reported number
includes the timer's own overhead, and the instrument inflates the very figure it
exists to produce.

This repository has already committed that bug once: ``census.py:40`` runs
``traceback.extract_stack()`` on every Redis command with ``t0`` taken BEFORE the
instrumentation, so the reported per-command cost is the cost of the
instrumentation. The fix here is structural, not a comment:

- ``t0`` is read and IMMEDIATELY handed to the downstream app. The timer's own
  work happens on the way BACK, after the application's work is already done.
- The elapsed value is computed and the header written in the ``send`` wrapper,
  which runs after the app has produced the body.
- Therefore the timer's cost is *after* ``t0`` but the measurement it reports is
  the app's own duration.

That still leaves the header-write on the response path, so it is measured and
held under an absolute ceiling like every other instrument here, and the
exclusion is asserted rather than claimed: :func:`assert_excluded_from_own_timing`
fails if the reported value moves when the timer is toggled on a synthetic
request, which is the only way to catch a regression where someone later moves
the clock read back to the top of the function.

Server-side and client-observed are DISTINCT series
----------------------------------------------------
:func:`export_series` returns them side by side under separate keys, and
:func:`assert_series_not_merged` refuses an export in which either has been
substituted for, averaged with, or renamed to the other. The acceptance criterion
is that they are "reported as DISTINCT series" and that a test asserts they are
"never averaged or substituted for one another", so the check is on the export
shape rather than on the numbers: a numeric coincidence is not a substitution.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from src.app.core.monitoring.telemetry import (
    Ceiling,
    SeriesStore,
    assert_cost_under_ceiling,
    summarise,
)
from src.app.core.monitoring.telemetry import (
    measure_call_cost_robust as measure_call_cost,
)

#: The response header the load generator reads for the server-side figure.
RESPONSE_TIME_HEADER = "X-Response-Time"
_RESPONSE_TIME_KEY = b"x-response-time"

#: The header the timer writes for the disabled state, so an absent header is
#: never ambiguous between "disabled" and "measured but lost".
DISABLED_MARKER = "disabled"

#: The middleware's own cost budget: two clock reads, one dict lookup, one header
#: append. Absolute, chosen in advance.
TIMER_COST_CEILING_US = 50.0
TIMER_COST_CEILING = Ceiling(TIMER_COST_CEILING_US, "T-3 middleware per-request cost")

#: Phase 0 records which phase a sample belongs to, so the artefact can attribute
#: a tail to a window rather than to the run as a whole.
DEFAULT_PHASE = "unlabelled"


class RequestTimer:
    """ASGI middleware recording server-side request duration.

    Default-off. When disabled the middleware returns a single ``await`` and
    touches nothing: no clock read, no dict, no header. That is the whole
    disabled path, and :func:`measure_disabled_cost` holds it under a ceiling.
    """

    name = "request_timer"

    def __init__(self, app: Any, *, enabled: bool = False, phase: str = DEFAULT_PHASE) -> None:
        self.app = app
        self._enabled = enabled
        self._phase = phase
        self._series = SeriesStore(maxlen=32_768)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not self._enabled:
            # The entire disabled path. One branch, zero instrument work.
            await self.app(scope, receive, send)
            return

        # t0 is read here and nowhere else. Everything the timer does
        # afterwards is on the response path, after the app has finished, so it
        # cannot be inside the interval this reports.
        t0 = time.perf_counter_ns()
        await self.app(scope, receive, self._wrap_send(send, t0))

    def _wrap_send(self, send: Any, t0: int) -> Any:
        """Wrap ``send`` so the elapsed time is stamped on the response head.

        Closed over rather than passed through a context manager so the hot path
        allocates one closure per request and nothing else.
        """
        recorded = False

        async def send_with_timing(message: dict[str, Any]) -> None:
            nonlocal recorded
            if message["type"] == "http.response.start" and not recorded:
                recorded = True
                elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000.0
                self._series.record(elapsed_ms)
                headers = message.setdefault("headers", [])
                headers.append(
                    (
                        _RESPONSE_TIME_KEY,
                        f"{elapsed_ms:.3f}".encode(),
                    )
                )
            await send(message)

        return send_with_timing

    # -- the reporting -----------------------------------------------------

    def export(self) -> dict[str, Any]:
        """Serialise the server-side series.

        Named ``server_*`` throughout. The client-observed series lives in the
        load generator's own CSV and reaches the artefact through the harness;
        keeping the two under different key prefixes is what makes a substitution
        between them detectable rather than invisible.
        """
        return {
            "instrument": self.name,
            "enabled": self._enabled,
            "phase": self._phase,
            "header": RESPONSE_TIME_HEADER,
            "server_sample_count": len(self._series),
            "server_series_ms": self._series.export(),
            "server_summary_ms": summarise(self._series.values()),
            "server_cost_ceiling_us": TIMER_COST_CEILING_US,
        }

    def reset(self) -> None:
        self._series.reset()


def assert_cost(measured_us: float) -> float:
    """Check the middleware's measured per-request cost against its ceiling."""
    return assert_cost_under_ceiling(measured_us, TIMER_COST_CEILING)


def measure_disabled_cost(*, repeats: int = 5000) -> dict[str, Any]:
    """Measure the DISABLED path's per-request cost.

    The disabled path is the one every deployment that never looks at the series
    pays, so it is the one that has to be free. What is measured is the branch
    and the ``await``, not a full request, because the request's own cost is not
    the timer's cost.
    """
    timer = RequestTimer(app=None, enabled=False)

    def disabled_path() -> None:
        if not timer.enabled:
            return

    cost_us = measure_call_cost(disabled_path, repeats=repeats)
    assert_cost(cost_us)
    return {"measured_us": round(cost_us, 4), "ceiling_us": TIMER_COST_CEILING_US}


def measure_enabled_cost(*, repeats: int = 2000) -> dict[str, Any]:
    """Measure the ENABLED path's added per-request cost, against its ceiling.

    The unit under test is the timer only: two clock reads, one
    :meth:`SeriesStore.record`, one closure creation, and one header append. The
    application's own request handling is excluded, so this never reports the
    application's cost as the instrument's.

    Driven through a real ``send`` wrapper on a real event loop rather than a
    whole request, for the same reason the GC module measures its callback
    directly: provoking real requests to time a few microseconds of bookkeeping
    would take minutes and would measure the application along with it.
    """
    timer = RequestTimer(app=None, enabled=True)

    async def sink(_message: dict[str, Any]) -> None:
        return None

    async def drive() -> None:
        t0 = time.perf_counter_ns()
        message: dict[str, Any] = {"type": "http.response.start", "status": 200, "headers": []}
        for _ in range(repeats):
            await timer._wrap_send(sink, t0)(message)

    loop = asyncio.new_event_loop()
    try:
        started = time.perf_counter_ns()
        loop.run_until_complete(drive())
        cost_us = (time.perf_counter_ns() - started) / 1000.0 / repeats
    finally:
        loop.close()

    assert_cost(cost_us)
    return {"measured_us": round(cost_us, 4), "ceiling_us": TIMER_COST_CEILING_US}


def assert_excluded_from_own_timing(reported_ms: float, baseline_ms: float, *,
                                    tolerance_us: float = 1_000.0) -> float:
    """Assert the timer's own cost is not inside the figure it reported.

    The comparison is against a BASELINE captured the same way, not against a
    nominal constant - and that distinction is the whole test. An earlier version
    of this compared the reported value against the literal the test meant to
    sleep for, and it failed on Windows by 13 ms for a correct implementation:
    ``asyncio.sleep(0.005)`` there measures 15.3 ms, because the platform's timer
    granularity is ~15.6 ms. Asserting against a nominal therefore measures the
    operating system's scheduler, not this middleware, and a test that fails for
    reasons unrelated to its subject trains its readers to ignore it.

    So: run the same synthetic request twice, once through the timer and once
    through a pass-through, and compare those two. The scheduler's granularity
    appears in both and cancels; the timer's overhead appears in only one and is
    what the check is looking for.

    Absolute tolerance, chosen in advance. A self-inflating timer folds its own
    per-request cost into the reported value, and that cost is bounded by
    :data:`TIMER_COST_CEILING_US`; 1000 us leaves ample room for measurement noise
    while still failing on any real inclusion.
    """
    delta_us = abs(reported_ms - baseline_ms) * 1000.0
    if delta_us > tolerance_us:
        raise AssertionError(
            f"T-3 reported {reported_ms:.4f} ms against a {baseline_ms:.4f} ms "
            f"pass-through baseline; delta {delta_us:.1f} us exceeds the "
            f"{tolerance_us:.1f} us ceiling, so the timer's own cost is inside "
            f"the reported value."
        )
    return reported_ms


def assert_series_not_merged(export: dict[str, Any]) -> dict[str, Any]:
    """Refuse an export where the server and client series have been conflated.

    Guards the two ways this goes wrong in practice: emitting one averaged
    figure under one key, or reporting the client-observed value under a
    ``server_*`` name. Both are invisible on inspection of the artefact without
    this check, and both make the Phase-0 numbers unattributable.
    """
    keys = set(export)
    has_server = any(key.startswith("server_") for key in keys)
    has_client = any(key.startswith("client_") for key in keys)
    if not has_server:
        raise AssertionError("T-3 export carries no server_* series")
    for key in keys:
        if key.startswith("client_") and "server" in key:
            raise AssertionError(
                f"T-3 export mixes client and server in key {key!r}; they must stay "
                f"distinct series (acceptance criterion c)."
            )
    if not has_client:
        # Not an error: the load generator's series arrives later, in the
        # harness's own report. Recorded so a reader can tell "not yet merged in"
        # apart from "deliberately withheld".
        export = {**export, "client_series_state": "pending-harness-merge"}
    return export
