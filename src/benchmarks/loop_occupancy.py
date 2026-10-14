"""L-8: attribute event-loop occupancy to the three named blocking sources.

WHAT this measures, on the Linux target, the fraction of a sampling interval the
event loop spends on: (1) the per-header attack scan, (2) the 401 path's
``exc_info`` traceback formatting, (3) the dead OTel span per DB request.

Two forms are reported for every source, because the second is the form T-1's
histogram can corroborate or contradict:

* **per-call cost** in microseconds, via the section 4.4 protocol
  (``min-of-N`` batches, ``median-of-M`` batches, warm-up, ``gc.collect()``),
  against ceilings declared *before* measuring.
* **event-loop occupancy** as a dimensionless fraction, measured two ways:
  ``injected`` (per-call cost x observed call count / observed wall time) and
  ``accounted`` (CPU time actually charged to the process while inside the three
  sites / CPU time consumed by the whole thread). They differ; reporting both is
  the point.

The cross-check the bead demands is arithmetic, not editorial: T-1
(``src/app/core/monitoring/loop_lag.py``) samples this same loop on an absolute
125 Hz grid and records, per sample, how late it woke up. Summing those samples
gives the total wall-clock the loop was unavailable. If the three sources'
occupancy is a negligible share of that sum, the hypothesis is REFUTED and this
artefact reports it as a refutation.

Instrumentation is runtime monkeypatching of three precise seams, installed and
removed in-process. No production file is modified.

Usage (Linux only; T-1 is representative only on uvloop):

    L8_ENV=/home/ansible/.l8_env .venv/bin/python -m src.benchmarks.loop_occupancy \
        --out perf-results/RESULT-<stamp>/l8_loop_occupancy.json
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import functools
import json
import os
import platform
import socket
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import structlog

from src.benchmarks.protocol import (
    Ceiling,
    MicroResult,
    ProtocolViolation,
    measure,
    render,
    stamp,
    verdict,
)
from src.benchmarks.security_state import clear_security_state

UNMEASURED: Final[str] = "unmeasured"

#: T-1 failed its own floor, so its histogram describes a sampler that could
#: not keep up. Dividing by it would turn a broken instrument into a verdict.
SAMPLER_UNUSABLE: Final[str] = (
    "UNUSABLE: T-1 did not reach its minimum sampling rate, so its histogram "
    "cannot corroborate or refute anything. The three occupancies below stand; "
    "the cross-check does not."
)

#: Absolute ceilings, declared before measuring. Each is set against a stated
#: anchor, never against another measurement of the same quantity.
#:
#: The anchor for both micro ceilings is T-1's own
#: ``SAMPLER_COST_CEILING_US = 100.0`` -- the repository's declared bound on
#: loop work allowed to be noticeable, which is one eighth of T-1's 8 ms
#: sampling interval. It is a judgement the repo already made and committed to,
#: not a number fitted to these measurements.
#:
#: Both values below were re-declared under bead FastAPI_saas-3uww because the
#: originals were authored off figures that do not survive scrutiny:
#:
#: * header_scan was 50 us, taken from reports/header_scan_cost.json, which set
#:   it as a PER-HEADER figure. Decomposed on the target host, one browser
#:   User-Agent costs 56.2 us of that whole-call figure and the other five
#:   headers together cost ~1 us, because only the UA trips the branch-prune
#:   prefilter. The artefact's own note says its per-length figures are not
#:   derived from the total, yet its ``per_scanned_header_us: 8.8102`` IS
#:   total/6 -- the misleading average this bead exists to stop quoting.
#: * otel_span was 10 us, taken from reports/span_cost.json's best_us 3.123 on
#:   a different host. The target measures 19.486 us for the same call: a 6x
#:   cross-host gap, inside the 7.1x run-to-run spread protocol.py records, so
#:   neither box's best figure is a portable ceiling.
HEADER_SCAN_CEILING_US: Final[Ceiling] = Ceiling(
    100.0,
    "T-1's SAMPLER_COST_CEILING_US: one eighth of one 8 ms sampling interval, "
    "so the whole request's header scan may never cost more of the loop than "
    "the repo already declares noticeable. Measured 56.2 us on the target "
    "(l8_probe_scan.py), i.e. 0.56 of this; a 1.8x regression trips it",
)
EXC_INFO_CEILING_US: Final[Ceiling] = Ceiling(
    1000.0,
    "no prior measurement exists; 1000 us is 2x the p50 of a whole 401 "
    "(3.29 ms) and 0.2x EM-3's 0.46 ms floor, so anything above this is "
    "material to the request rather than noise within it. Bead FastAPI_saas-"
    "xv1b removed the 401 path's call entirely, so this ceiling no longer has "
    "a live site to gate and is retained only so the harness still measures it",
)
SPAN_CEILING_US: Final[Ceiling] = Ceiling(
    50.0,
    "half of T-1's SAMPLER_COST_CEILING_US: a DEAD span is optional "
    "instrumentation that produces nothing, so it may not occupy more than "
    "half the loop budget the repo calls noticeable. Measured 19.486 us on the "
    "target, i.e. 0.39 of this",
)

#: The 401 endpoint, per reports/unauth_401.json.
PROBE_PATH: Final[str] = "/api/v1/users/me"

#: httpx's own default UA (`python-httpx/0.28.1`) is classified as a bad bot and
#: the request is blocked in the middleware, never reaching the three sites. A
#: desktop Chrome UA is what production traffic actually carries, and
#: reports/header_scan_cost.json shows it trips the branch-prune prefilter --
#: so this UA exercises the SLOW path of site 1, which is the conservative
#: choice for an occupancy measurement.
PROBE_HEADERS: Final[dict[str, str]] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

#: The tenant slug X-Tenant-ID carries. TenantMiddleware rejects a request
#: without one with 400 before any auth decision, so a probe that omits it
#: measures the middleware, not the three sites. This is the slug of a tenant
#: that exists and is active in the target database; override with --tenant.
PROBE_TENANT: Final[str] = "t1-iso"


def probe_headers(tenant: str) -> dict[str, str]:
    return {**PROBE_HEADERS, "X-Tenant-ID": tenant}


SOURCE_LABELS: Final[dict[str, str]] = {
    "header_scan": "per-header attack scan (prefilter + combined regex)",
    "exc_info": "exc_info traceback formatting on the 401 path",
    "otel_span": "dead OTel span per DB request",
}

#: Below this share of measured loop lag the three sources are not the
#: explanation, and the hypothesis is refuted rather than confirmed.
NEGLIGIBLE_SHARE: Final[float] = 0.02

#: Every knob here is one of the eight rate limits L-8's own sibling plan
#: section enumerates, and every one is set to a value this probe cannot trip.
#: They are relaxed for the same reason and in the same form as
#: src/benchmarks/census.py::bootstrap and
#: src/benchmarks/prof_request_path.py::bootstrap: unauthenticated requests at
#: production limits are stopped by the limiter (429) and by auto-ban (403)
#: before they reach any of the three sites, so the harness would measure the
#: limiter instead of the sites. The relaxation is RECORDED in the artefact, so
#: no occupancy number here can be misread as a production-limit measurement --
#: only the three per-call costs are, and those are measured in phase 1 with no
#: app running at all.
RELAXED_LIMITS: Final[dict[str, str]] = {
    "AUTO_BAN_ENABLED": "false",
    "SECURITY_BUDGETS": json.dumps(
        {
            key: [5000, 60]
            for key in (
                "auth",
                "mfa_verify",
                "mfa_verify_global",
                "mfa_mutate",
                "magic_link_send",
                "passkey_login",
                "refresh",
                "ai_chat",
            )
        }
    ),
    "TIER_RATE_LIMITS": json.dumps(
        {key: [100000, 3600] for key in ("free", "basic", "pro", "enterprise")}
    ),
    "DEFAULT_AUTHENTICATED_LIMIT": "[100000,3600]",
    "DEFAULT_ANONYMOUS_LIMIT": "[100000,3600]",
    "ANONYMOUS_IP_AGGREGATE_LIMIT": "[100000,3600]",
    "DEFAULT_RATE_LIMIT_LIMIT": "100000",
    "DEFAULT_RATE_LIMIT_PERIOD": "3600",
}


# --------------------------------------------------------------------------- #
# phase 1: per-call cost, section 4.4 protocol
# --------------------------------------------------------------------------- #


def measure_header_scan() -> MicroResult:
    """One scanned header value: prefilter short-circuit then combined regex."""
    from src.app.middleware.attack_detection import AttackDetectionMiddleware

    middleware = AttackDetectionMiddleware.__new__(AttackDetectionMiddleware)
    middleware._branch_prune = True

    # A desktop Chrome UA. It contains b";" and b"or", so the branch-prune
    # prefilter hits and the FULL combined regex runs -- the C-04 case, and the
    # conservative choice because this VM has no pyahocorasick, so
    # _PREFILTER_AUTOMATON is None and _attack_prefilter_hit runs its slower
    # literal-in-lowered fallback chain.
    user_agent = (
        b"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        b"(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    attacks: list[dict[str, Any]] = []

    def scan_once() -> None:
        middleware._check_for_patterns(user_agent, "header", attacks)

    result = measure(scan_once, name="header_scan_per_header", iterations=2000, warmup=100, repetitions=5)
    if not middleware._branch_prune:
        msg = "ATTACK_DETECTION_BRANCH_PRUNE is off; the scan is not the one L-8 names"
        raise ProtocolViolation(msg)
    return result


def measure_otel_span() -> MicroResult:
    """Enter and exit one `trace_span`. No SDK is installed, so this is the
    dead ProxyTracer path -- the span is built and discarded, which is
    exactly the cost L-8 names."""
    from src.app.core.observability.tracing import trace_span

    def span_once() -> None:
        with trace_span("l8_probe"):
            pass

    return measure(span_once, name="otel_span_per_invocation", iterations=20000, warmup=1000, repetitions=5)


def measure_exc_info(exc: BaseException, exc_info: Any) -> MicroResult:
    """One structlog error carrying an exc_info payload."""
    logger = structlog.get_logger("l8.probe")

    def log_once() -> None:
        logger.error("Database transaction failed, rolling back", tenant="None", exc_info=exc_info)

    return measure(log_once, name="exc_info_error_call", iterations=2000, warmup=100, repetitions=5)


# --------------------------------------------------------------------------- #
# occupancy accumulators
# --------------------------------------------------------------------------- #


@dataclass
class Source:
    key: str
    wall_s: float = 0.0
    cpu_s: float = 0.0
    count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"wall_s": self.wall_s, "cpu_s": self.cpu_s, "count": self.count}


@dataclass
class Meter:
    """Wall-clock and CPU time charged to the process while inside each site."""

    sources: dict[str, Source] = field(
        default_factory=lambda: {key: Source(key) for key in SOURCE_LABELS}
    )

    @contextlib.contextmanager
    def charge(self, key: str) -> Iterator[None]:
        source = self.sources[key]
        t0 = time.perf_counter()
        c0 = time.process_time()
        try:
            yield
        finally:
            source.wall_s += time.perf_counter() - t0
            source.cpu_s += time.process_time() - c0
            source.count += 1


METER: Final[Meter] = Meter()


def install_instruments() -> list[str]:
    """Monkeypatch the three seams. Returns the names installed."""
    from src.app.core.db import database
    from src.app.core.observability import tracing
    from src.app.middleware.attack_detection import AttackDetectionMiddleware

    installed: list[str] = []

    original_scan = AttackDetectionMiddleware._check_for_patterns

    @functools.wraps(original_scan)
    def metered_scan(self: AttackDetectionMiddleware, *a: Any, **kw: Any) -> Any:
        with METER.charge("header_scan"):
            return original_scan(self, *a, **kw)

    AttackDetectionMiddleware._check_for_patterns = metered_scan  # type: ignore[method-assign]
    installed.append("header_scan")

    original_trace_span = tracing.trace_span

    @functools.wraps(original_trace_span)
    def metered_trace_span(*a: Any, **kw: Any) -> Any:
        with METER.charge("otel_span"):
            return original_trace_span(*a, **kw)

    tracing.trace_span = metered_trace_span  # type: ignore[assignment]
    database.trace_span = metered_trace_span  # type: ignore[assignment]
    installed.append("otel_span")

    class _MeteredLogger:
        """Wraps the module's structlog logger; both its .error calls carry exc_info."""

        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        def error(self, *a: Any, **kw: Any) -> Any:
            if kw.get("exc_info") is not None:
                with METER.charge("exc_info"):
                    return self._inner.error(*a, **kw)
            return self._inner.error(*a, **kw)

    database.logger = _MeteredLogger(database.logger)  # type: ignore[assignment]
    installed.append("exc_info")

    return installed


# --------------------------------------------------------------------------- #
# phase 2: in-process load against the real app, T-1 sampling alongside
# --------------------------------------------------------------------------- #


async def drive_401(
    count: int, concurrency: int, tenant: str = PROBE_TENANT
) -> dict[int, int]:
    """Fire `count` unauthenticated GETs at the DB-dependent route.

    The 401 is raised by the auth dependency and thrown back into
    `async_get_db`'s `yield`, so one request pays all three sites.
    Returns the response-status histogram. It must be {401: count}; anything
    else means the probe never reached the sites and the run is void.
    """
    import httpx

    from src.app.main import app

    transport = httpx.ASGITransport(app=app)
    semaphore = asyncio.Semaphore(concurrency)
    statuses: dict[int, int] = {}

    async def one() -> None:
        async with semaphore:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://l8.invalid", timeout=30.0
            ) as client:
                response = await client.get(PROBE_PATH, headers=probe_headers(tenant))
                statuses[response.status_code] = statuses.get(response.status_code, 0) + 1

    await asyncio.gather(*(one() for _ in range(count)))
    return statuses


async def occupancy_run(request_count: int, concurrency: int, tenant: str = PROBE_TENANT) -> dict[str, Any]:
    """Measure the three occupancies and T-1's loop lag over the same window."""
    from src.app.core.setup import close_redis_cache_pool, create_redis_cache_pool

    # Only the Redis pool is opened. Running the full admin lifespan would also
    # run the token-blacklist cache warmup, which issues a real query against
    # an RLS-audited write path -- noise this measurement has no reason to pay,
    # and a schema dependency this harness must not have. The 401 path itself
    # opens a session but never acquires a connection, so Postgres is not on
    # this measurement's critical path at all.
    await create_redis_cache_pool()
    try:
        cleared = await clear_security_state()
        result = await _occupancy_run_within_lifespan(request_count, concurrency, tenant)
        result["security_keys_cleared"] = cleared
        return result
    finally:
        await close_redis_cache_pool()


async def _occupancy_run_within_lifespan(
    request_count: int, concurrency: int, tenant: str = PROBE_TENANT
) -> dict[str, Any]:
    from src.app.core.monitoring import LoopLagSampler, assert_validated_on_production_loop
    from src.app.core.monitoring.loop_lag import MINIMUM_RATE_HZ

    # Imported here, not at module scope, on purpose. src.app.core.monitoring's
    # __init__ pulls in the config package, so a module-level import builds the
    # settings singleton BEFORE bootstrap() has relaxed the rate limits, and the
    # relaxation then silently applies to nothing.
    from src.app.core.monitoring.loop_lag import (
        SAMPLE_INTERVAL_SECONDS as LOOP_SAMPLE_INTERVAL,
    )

    # Warm caches, JIT'd regex, connection pool -- discarded, because the 401
    # path's first call pays pool setup that no steady-state request pays.
    warm_statuses = await drive_401(50, min(concurrency, 10), tenant)
    if set(warm_statuses) != {401}:
        raise RuntimeError(f"warmup returned {warm_statuses}, not all 401")
    for source in METER.sources.values():
        source.wall_s = 0.0
        source.cpu_s = 0.0
        source.count = 0

    sampler = LoopLagSampler(enabled=True)
    sampler.enable()

    cpu_before = time.process_time()
    t0 = time.perf_counter()
    sampler.start()  # stop() owns the task handle from here on
    await asyncio.sleep(0)  # let the sampler claim the loop before load starts
    statuses = await drive_401(request_count, concurrency, tenant)
    if set(statuses) != {401}:
        raise RuntimeError(f"load returned {statuses}, not all 401")
    wall_s = time.perf_counter() - t0
    cpu_s = time.process_time() - cpu_before
    await sampler.stop()

    export = sampler.export()
    assert_validated_on_production_loop(export)

    lag_ms = list(sampler._series.values())
    lag_total_s = sum(lag_ms) / 1000.0
    samples = len(lag_ms)

    # T-1 samples an ABSOLUTE grid (due_k = origin + k * interval), so a block
    # longer than one interval is charged once to EVERY grid point it
    # overshoots: after a block of duration T the sampler wakes and reports
    # T, T - interval, T - 2 * interval, ... until it catches up. `sum(lag)`
    # therefore grows roughly as T^2 / (2 * interval) and can exceed the wall
    # clock by orders of magnitude. Summing is only a valid estimate of loop
    # occupancy while no single block exceeds the interval.
    #
    # The grid form below has no such failure: every sample is one grid slot
    # the loop failed to service on time, so it is bounded in [0, 1] by
    # construction and stays meaningful exactly when the naive sum degenerates
    # (a starved sampler, which is the interesting case). It is the
    # load-bearing denominator; the naive sum is reported beside it, unclamped,
    # so the divergence is visible in the artefact rather than hidden.
    serviced_s = samples * LOOP_SAMPLE_INTERVAL
    occupancy_grid = (wall_s - serviced_s) / wall_s if wall_s > 0 else None
    occupancy_naive = lag_total_s / wall_s if wall_s > 0 else None
    achieved_hz = export["achieved_rate_hz"]
    sampler_usable = export["meets_minimum_rate"] and achieved_hz >= MINIMUM_RATE_HZ

    three_wall = sum(s.wall_s for s in METER.sources.values())
    three_cpu = sum(s.cpu_s for s in METER.sources.values())

    return {
        "requests": {
            "issued": sum(statuses.values()),
            "unauthorized": statuses.get(401, 0),
            "warmup": sum(warm_statuses.values()),
            "warmup_unauthorized": warm_statuses.get(401, 0),
        },
        "warmup_unauthorized": warm_statuses.get(401, 0),
        "concurrency": concurrency,
        "wall_s": wall_s,
        "process_cpu_s": cpu_s,
        "per_source": {key: src.as_dict() for key, src in METER.sources.items()},
        "three_sources": {
            "wall_s": three_wall,
            "cpu_s": three_cpu,
            "occupancy_injected": three_wall / wall_s if wall_s > 0 else None,
            "occupancy_accounted": three_cpu / cpu_s if cpu_s > 0 else None,
        },
        "loop_lag": {
            "occupancy_observed": occupancy_grid,
            "occupancy_observed_basis": "grid: 1 - (samples * sample_interval) / wall_s",
            "occupancy_naive_lag_sum": occupancy_naive,
            "occupancy_naive_lag_sum_note": (
                "sum(lag) over an absolute-grid sampler double-counts any block "
                "longer than one interval, so it exceeds 1.0 and even exceeds "
                "the wall clock under starvation. Recorded unclamped as evidence "
                "of that degeneracy, never used as a denominator."
            ),
            "lag_sum_s": lag_total_s,
            "samples": samples,
            "requested_interval_s": export["requested_interval_s"],
            "sampler_usable": sampler_usable,
            "sampler_cost_note": (
                "the sampler's own cost is inside total_s and is bounded per "
                "sample by SAMPLER_COST_CEILING_US (100 us)"
            ),
            "summary_ms": export["summary_ms"],
            "achieved_rate_hz": achieved_hz,
            "meets_minimum_rate": export["meets_minimum_rate"],
            "minimum_rate_hz": MINIMUM_RATE_HZ,
            "loop_module": export["loop_module"],
            "representativeness": export["representativeness"],
        },
    }


# --------------------------------------------------------------------------- #
# analysis
# --------------------------------------------------------------------------- #


def occupancy_from_counts(
    per_call_us: float | None, count: int, wall_s: float
) -> float | None:
    """Occupancy the source would occupy at its measured per-call cost."""
    if per_call_us is None or wall_s <= 0:
        return None
    return (per_call_us * count) / (wall_s * 1e6)


def cross_check(occupancy: dict[str, Any]) -> dict[str, Any]:
    """Compare the summed occupancy against T-1's histogram. The bead's test."""
    lag = occupancy["loop_lag"]
    three = occupancy["three_sources"]
    observed = lag["occupancy_observed"]
    injected = three["occupancy_injected"]
    accounted = three["occupancy_accounted"]

    shares: dict[str, float | None] = {}
    for name, measured in (("injected", injected), ("accounted", accounted)):
        shares[name] = measured / observed if observed and measured is not None else None

    accounted_share = shares["accounted"]
    if accounted_share is None:
        finding = UNMEASURED
    elif not lag["sampler_usable"]:
        finding = SAMPLER_UNUSABLE
    elif accounted_share < NEGLIGIBLE_SHARE:
        finding = "REFUTED: the three sources are a negligible share of observed loop lag"
    else:
        finding = "NOT REFUTED: the three sources are a material share of observed loop lag"

    return {
        "observed_loop_lag_occupancy": observed,
        "observed_loop_lag_occupancy_basis": lag["occupancy_observed_basis"],
        "sampler_usable": lag["sampler_usable"],
        "achieved_rate_hz": lag["achieved_rate_hz"],
        "minimum_rate_hz": lag["minimum_rate_hz"],
        "three_source_occupancy_injected": injected,
        "three_source_occupancy_accounted": accounted,
        "share_of_lag_injected": shares["injected"],
        "share_of_lag_accounted": accounted_share,
        "negligible_share_threshold": NEGLIGIBLE_SHARE,
        "verdict": finding,
        "note": (
            "accounted is the load-bearing number: it is CPU time charged to the "
            "process inside the three sites, divided by CPU time the thread "
            "actually consumed. injected extrapolates the microbenchmark per-call "
            "cost and therefore also counts time the sites did not block the loop "
            "(sockets, syscalls). A large gap between them is itself a finding: it "
            "means the three sites are called often but rarely block. The "
            "denominator is the grid occupancy, not sum(lag): see "
            "occupancy_naive_lag_sum_note."
        ),
    }


# --------------------------------------------------------------------------- #
# entrypoint
# --------------------------------------------------------------------------- #


def build_artifact(
    stamp_payload: Any,
    scans: dict[str, MicroResult],
    ceilings: dict[str, Ceiling],
    occupancy: dict[str, Any],
    verdicts: dict[str, Any],
    tracer_class: str,
    otel_sdk_present: bool,
    host: str,
) -> dict[str, Any]:
    per_source_out: dict[str, Any] = {}
    measured = occupancy["per_source"]
    for key, label in SOURCE_LABELS.items():
        result = scans[key]
        per_call = result.reportable_us()
        ceiling = ceilings[key]
        src = measured[key]
        per_source_out[key] = {
            "label": label,
            "per_call_us": per_call,
            "spread_ratio": result.spread_ratio,
            "inside_noise_floor": result.is_inside_noise,
            "batch_mins_us": result.batch_mins_us,
            "batch_medians_us": result.batch_medians_us,
            "ceiling_us": ceiling.value_us,
            "ceiling_basis": ceiling.label,
            "verdict": verdicts.get(key),
            "calls_observed_in_load": src["count"],
            "measured_wall_s_in_load": src["wall_s"],
            "measured_cpu_s_in_load": src["cpu_s"],
            "occupancy_injected_in_load": occupancy_from_counts(per_call, src["count"], occupancy["wall_s"]),
            "occupancy_accounted_in_load": (
                src["cpu_s"] / occupancy["process_cpu_s"]
                if occupancy["process_cpu_s"] > 0
                else None
            ),
        }

    return {
        "bead": "FastAPI_saas-1py8",
        "what": "L-8 event-loop occupancy attribution for the three named blocking sources",
        "protocol": "section 4.4: min-of-N batches, median-of-M, warm-up, gc.collect(), absolute ceiling",
        "measured_at": datetime.now(UTC).isoformat(),
        "host": host,
        "limits_relaxed": True,
        "relaxed_limits": RELAXED_LIMITS,
        "limits_relaxed_note": (
            "Phase 1 (the three per-call costs) ran with NO app and therefore at "
            "production limits; it is the phase-1 numbers alone that carry a "
            "production-limit claim. Phase 2 ran with the eight rate limits "
            "relaxed, because at production limits the probe's own requests are "
            "answered 429 by the limiter and 403 by auto-ban before reaching any "
            "of the three sites."
        ),
        "stamp": {
            "platform": stamp_payload.platform,
            "release": stamp_payload.release,
            "machine": stamp_payload.machine,
            "cpu_count": stamp_payload.cpu_count,
            "event_loop": stamp_payload.event_loop,
            "python": stamp_payload.python,
        },
        "stdout_redirected_to_devnull": True,
        "stdout_redirect_note": (
            "Both phases run with fd 1 pointed at /dev/null. structlog still runs "
            "every record through ConsoleRenderer, so the exc_info formatting "
            "this harness is measuring stays inside the measurement; what is "
            "excluded is the terminal write, which is not the application's work. "
            "Unredirected, the ~10 000 phase-1 renders alone cost ~620 s of CPU "
            "and buried the number they were measuring. stderr is never "
            "redirected."
        ),
        "tracer_class": tracer_class,
        "otel_sdk_present": otel_sdk_present,
        "per_source": per_source_out,
        "occupancy": occupancy,
        "cross_check": cross_check(occupancy),
    }


def bootstrap(env_path: str | None) -> None:
    from dotenv import load_dotenv

    load_dotenv(env_path or os.environ.get("L8_ENV", ""), override=True)
    # AFTER the dotenv load, so a developer's own .env cannot re-impose a limit
    # this probe would then trip and void its own run.
    os.environ.update(RELAXED_LIMITS)


@contextlib.contextmanager
def quiet_stdout() -> Iterator[None]:
    """Send fd 1 to /dev/null for the duration of the block.

    Both phases of this harness make the application emit rendered log records
    on stdout, and the ``exc_info`` record in particular renders a multi-line
    traceback through structlog's ConsoleRenderer. Phase 1 does that ~10 000
    times, and phase 2 once per probe request; writing those to a pipe costs
    tens of milliseconds each and swamps the measurement it sits inside (a
    100-request run burned 620 s of CPU almost entirely on terminal writes).

    Only fd 1 moves. structlog keeps formatting every record through the
    renderer, so the formatting cost -- which is the thing site 2 is named for
    -- stays inside the measurement; what is excluded is the write to the
    terminal, which is not the application's work. stderr is never redirected,
    so a real failure still surfaces its traceback.
    """
    sys.stdout.flush()
    saved = os.dup(1)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 1)
        yield
    finally:
        sys.stdout.flush()
        os.dup2(saved, 1)
        os.close(saved)
        os.close(devnull)


async def amain(args: argparse.Namespace) -> int:
    from opentelemetry import trace as otel_trace

    bootstrap(args.env)

    tracer = otel_trace.get_tracer("l8")
    tracer_class = type(tracer).__name__
    try:
        import opentelemetry.sdk  # type: ignore[import-not-found]  # noqa: F401

        sdk = True
    except ImportError:
        sdk = False

    install_instruments()

    exc = RuntimeError("l8 synthetic probe exception")
    exc_info = (type(exc), exc, exc.__traceback__)
    with quiet_stdout():
        scan = measure_header_scan()
        span = measure_otel_span()
        exc_result = measure_exc_info(exc, exc_info)
        render(scan)
        render(span)
        render(exc_result)

    scans: dict[str, MicroResult] = {
        "header_scan": scan,
        "otel_span": span,
        "exc_info": exc_result,
    }
    ceilings = {
        "header_scan": HEADER_SCAN_CEILING_US,
        "otel_span": SPAN_CEILING_US,
        "exc_info": EXC_INFO_CEILING_US,
    }
    verdicts: dict[str, Any] = {}
    for key, result in scans.items():
        try:
            verdicts[key] = verdict(result, ceilings[key]).message
        except ProtocolViolation as exc:  # pragma: no cover - defensive
            verdicts[key] = f"protocol violation: {exc}"

    with quiet_stdout():
        occupancy = await occupancy_run(args.requests, args.concurrency, args.tenant)

    artifact = build_artifact(
        stamp(), scans, ceilings, occupancy, verdicts, tracer_class, sdk, socket.gethostname()
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2, sort_keys=False), encoding="utf-8")
    print(f"\nwrote {out}")
    print(json.dumps(artifact["cross_check"], indent=2))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=None, help="dotenv file with POSTGRES_*/REDIS_* settings")
    parser.add_argument("--requests", type=int, default=2000)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--tenant", default=PROBE_TENANT, help="X-Tenant-ID slug of an active tenant")
    parser.add_argument("--out", default="reports/l8_loop_occupancy.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if platform.system() != "Linux":
        print("T-1 is representative only on Linux; refusing to run elsewhere.")
        return 2
    # T-1 refuses to certify a run that is not on uvloop, which is the whole
    # point of its representativeness gate. Installing the policy here means
    # the assertion inside occupancy_run is a real check, not a formality.
    try:
        import uvloop
    except ImportError:  # pragma: no cover - the gate below will say so
        pass
    else:
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    return asyncio.run(amain(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
