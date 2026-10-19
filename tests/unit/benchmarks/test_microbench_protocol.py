"""H-11 / P-20: the microbenchmark protocol, pinned so it cannot be skipped.

The protocol is `best_of(min-of-N, median-of-M)` plus a warm-up, a
`gc.collect()` before each timed batch, and an ABSOLUTE FIXED CEILING. It never
accepts `new < old`, because that form passes on noise: perf E1 records 1808 ms
and 4059 ms for the SAME inline arm on the SAME 12-CPU box, and perf C11 records
`/account/refresh` p50 at 19.96 ms and 66.83 ms in one pair and 34.79 ms and
4.88 ms in another. Intra-arm spread is +/-2x and run-to-run spread reaches 7.1x,
so a delta below ~2x is not a result at all.

These tests pin the parts of that which are code rather than prose:

- (b) a run with no warm-up is REFUSED, not silently accepted;
- (c) `new < old` is REFUSED, and a below-ceiling REGRESSION is reported as a
      FAILURE rather than passing on the fact that it beat the old arm;
- (d) the request-scale extension refuses fewer than MIN_REQUEST_REPETITIONS
      repetitions and reports BOTH median-of-medians and min-of-medians;
- (e) every emitted number carries the run's conditions, because a bare figure
      cannot be checked against anything.

`test_the_protocol_is_the_only_way_to_emit_a_number` is the static guard for (a):
it walks the microbenchmark files and fails if one has grown a timing loop that
does not route through this runner.
"""

from __future__ import annotations

import ast
import gc
import statistics
import time
from pathlib import Path

import pytest

from src.benchmarks import protocol
from src.benchmarks.protocol import (
    MIN_REQUEST_REPETITIONS,
    MIN_WARMUP,
    NOISE_FLOOR_RATIO,
    Ceiling,
    MicroResult,
    ProtocolViolation,
    RelativeComparisonRejected,
    RunStamp,
    measure,
    measure_request_scale,
    render,
    verdict,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

# The microbenchmark files that must route through the runner. Anything that
# times a code path and is NOT in this set has to justify itself in
# ``ALLOWED_RAW_TIMERS`` below, with a reason.
MICROBENCHMARK_FILES = (
    "tests/benchmarks/test_p2_micros.py",
    "tests/benchmarks/test_r5_micros.py",
    "tests/benchmarks/test_auth_perf.py",
    "tests/benchmarks/test_passkey_lookup.py",
    "tests/benchmarks/test_post_optimization.py",
    "tests/benchmarks/test_nrgi3_scan_cost.py",
    "tests/benchmarks/bench_r1_opus.py",
    "tests/benchmarks/bench_cache_invalidation.py",
)

#: Files permitted to call ``time.perf_counter()`` directly, each with the reason
#: it cannot route through :mod:`src.benchmarks.protocol`.
ALLOWED_RAW_TIMERS: dict[str, str] = {
    "tests/benchmarks/baseline_metrics.py": (
        "Collects samples and reports them itself; it is a sample store, not an estimator. "
        "Its percentile arithmetic already delegates to src/benchmarks/percentiles.py."
    ),
    "src/benchmarks/linux_mechanism_probe.py": (
        "A SAMPLE STORE, like baseline_metrics.py and middleware_profiler.py: it "
        "RECORDS the event-loop lag series and the GC pause histogram and reports "
        "them as collected, so it has no estimate to route through the protocol. "
        "Routing it through a per-call timer would measure the sampler rather than "
        "the loop - which is the failure T-2 exists to prevent. It also asserts its "
        "own achieved sampling rate against a fixed ceiling, and asserts the two "
        "Redis counts against each other, neither of which the protocol's "
        "best_of(min-of-N, median-of-M) estimator expresses."
    ),
    "tests/benchmarks/middleware_profiler.py": (
        "Collects ASGI middleware samples and reports them itself; same reason as "
        "baseline_metrics.py."
    ),
    "tests/performance/test_rls_performance.py": (
        "REQUEST SCALE. Migration deferred to its own bead: these measure end-to-end RLS "
        "behaviour and each site is a hand-placed probe, so routing them through a "
        "per-call timer would measure the harness rather than the request."
    ),
    "tests/performance/test_tenant_performance.py": "REQUEST SCALE; see test_rls_performance.py.",
    "tests/performance/test_rbac_perf.py": "REQUEST SCALE; see test_rls_performance.py.",
    "tests/performance/test_pool_behavior.py": "REQUEST SCALE; see test_rls_performance.py.",
    "tests/performance/benchmarks_r4.py": "REQUEST SCALE; see test_rls_performance.py.",
    "tests/performance/baseline_pre_rls.py": (
        "A frozen historical collector kept for comparison against the post-RLS baseline; it "
        "records samples rather than asserting a ceiling, so it is a sample store like "
        "baseline_metrics.py."
    ),
    "tests/performance/test_optimizations_r4/test_ai_streaming_json.py": (
        "Mostly source-grep correctness tests, with ONE timing test that asserts orjson is "
        "faster than stdlib json - a RELATIVE comparison, i.e. exactly the `new < old` form "
        "this bead bans. Converting it needs an absolute ceiling derived from a real Linux "
        "measurement, and none may be invented. Deferred, not exempt by preference."
    ),
    "tests/performance/test_optimizations_r4/test_cache_bytes.py": (
        "Mostly source-grep correctness tests, with ONE relative timing assertion "
        "(`avg_no_decode <= avg_decode * (1 + tolerance)`) - the banned `new < old` form. "
        "Same reason and same deferral as test_ai_streaming_json.py."
    ),
    "tests/performance/test_optimizations_r4/test_cache_warming.py": (
        "Async cache-warming and cache-lookup probes measured once each in milliseconds with "
        "no repetitions; this is request scale, not a microbenchmark. Its ceilings ARE already "
        "absolute. Deferred with test_rls_performance.py."
    ),
    "tests/performance/test_optimizations_r4/test_direct_dicts.py": (
        "Source-grep correctness tests; its only clock use is incidental and asserts nothing "
        "about timing."
    ),
    "tests/performance/test_optimizations_r4/test_event_bus_parallel.py": (
        "Source-grep correctness tests with an incidental clock read; no timing assertion. "
        "See test_direct_dicts.py."
    ),
    "src/benchmarks/census.py": (
        "OPERATOR HARNESS, not a test benchmark. census.py is imported by a test and must "
        "keep its own hooks; protocol adoption belongs to its own bead."
    ),
    "src/benchmarks/loop_occupancy.py": (
        "HALF AND HALF, and only the half that bypasses the protocol is listed here. Phase 1 "
        "of the L-8 harness DOES route through it: all three per-call costs go through "
        "measure(), are emitted by render() and are judged by verdict() against absolute "
        "Ceilings. Phase 2 is a SAMPLE STORE of the same kind as linux_mechanism_probe.py: "
        "it accumulates event-loop occupancy by summing perf_counter deltas across a whole "
        "request and dividing by the run's wall time, which is a quantity over the run rather "
        "than a per-call estimate, so best_of(min-of-N, median-of-M) does not express it. "
        "Timing a per-call probe there would measure the probe."
    ),
    "src/benchmarks/driver_count_io.py": "OPERATOR HARNESS; see census.py.",
    "src/benchmarks/prof_request_path.py": "OPERATOR HARNESS; see census.py.",
    "src/benchmarks/l12_pool_ratios.py": (
        "OPERATOR HARNESS measuring ARM THROUGHPUT, not a per-call cost. The quantity "
        "is scans/second of a whole arm (inline, thread pool, process pool), so the "
        "protocol's per-call estimator would have to be INVERTED to produce the number "
        "under test, and an inverted estimate is a derived quantity wearing a measured "
        "one's name. The process arm forks and runs work in a child process, which the "
        "protocol's single in-process callable cannot express at all. The harness also "
        "asserts the prefilter boundary BEFORE timing and refuses to report any ratio "
        "when it does not hold - a precondition with no place in the callable model. "
        "This is the L-12 driver recovered from /home/ansible/l12_pool_ratios.py; its "
        "recorded artefact is perf-results/RESULT-20261016-l12/."
    ),
    "src/benchmarks/l13_body_offload.py": (
        "OPERATOR HARNESS at request scale, and the protocol's request-scale runner "
        "cannot express what this measures. measure_request_scale drives ONE client "
        "sequentially; this harness issues each request from a FRESH loopback source IP "
        "while cycling 250 accounts, because the rate limiter keys on IP and the "
        "auto-ban would otherwise trip and truncate the sample. It also runs a KNEE "
        "SWEEP over seven concurrency levels before any comparison, which needs several "
        "requests in flight at once - sequential repetition cannot produce a knee. "
        "This is the L-13 driver recovered from /home/ansible/l13_body_offload.py; its "
        "recorded artefact is perf-results/RESULT-20261016-l13/."
    ),
}

_RUNNER_CALLS = frozenset({"measure", "measure_async", "measure_request_scale"})


def _noop() -> None:
    """A callable whose cost is small and constant, for exercising the runner."""


# --------------------------------------------------------------------------
# (b) the runner refuses an unsound configuration
# --------------------------------------------------------------------------


class TestTheRunnerRefusesUnsoundRuns:
    def test_a_run_with_no_warmup_is_refused(self):
        with pytest.raises(ProtocolViolation) as excinfo:
            measure(_noop, name="cold", iterations=10, warmup=0, repetitions=2)
        assert "warmup=0" in str(excinfo.value)
        assert "cold-start" in str(excinfo.value)

    def test_the_minimum_warmup_is_one_not_zero(self):
        assert MIN_WARMUP == 1
        measure(_noop, name="warm", iterations=5, warmup=MIN_WARMUP, repetitions=2)

    def test_a_run_with_no_iterations_is_refused(self):
        with pytest.raises(ProtocolViolation) as excinfo:
            measure(_noop, name="empty", iterations=0, warmup=1, repetitions=2)
        assert "cannot be timed" in str(excinfo.value)

    def test_a_run_with_no_repetition_is_refused(self):
        with pytest.raises(ProtocolViolation) as excinfo:
            measure(_noop, name="once", iterations=5, warmup=1, repetitions=0)
        assert "produces no median" in str(excinfo.value)

    def test_gc_runs_before_every_timed_batch(self, monkeypatch):
        """(b) the collector is not optional; it is what makes batch 1 like batch 5.

        Counted rather than inferred: a runner that collected only once, or not at
        all, would still return a plausible number and the difference would only
        show up as an unexplainable outlier weeks later.
        """
        calls: list[int] = []
        real_collect = gc.collect

        def counting_collect() -> int:
            calls.append(1)
            return real_collect()

        monkeypatch.setattr(protocol.gc, "collect", counting_collect)
        measure(_noop, name="gc", iterations=2, warmup=1, repetitions=4)
        assert len(calls) == 4, "gc.collect() must run once per timed batch, not once per run"

    def test_the_warmup_calls_happen_before_any_timed_call(self):
        """The warm-up runs exactly once, outside the timed loop.

        Exact-count arithmetic rather than an ordering probe. A runner that
        re-ran the warm-up inside every batch would record
        ``warmup * repetitions + iterations * repetitions`` = 14 calls instead
        of 10, so the total alone falsifies it.
        """
        calls: list[int] = []

        def record() -> None:
            calls.append(1)

        result = measure(record, name="order", iterations=3, warmup=4, repetitions=2)
        assert len(calls) == 4 + 3 * 2
        assert result.warmup == 4
        assert result.iterations == 3
        assert result.repetitions == 2

    def test_the_warmup_is_not_counted_in_the_samples(self):
        """A warm-up call that leaked into the samples would set the minimum.

        Counting calls cannot show this: the warm-up does run, so the call
        total is warm-up plus timed regardless. What it can show is WHICH calls
        are timed, so the function here is slow for its first ``warmup`` calls
        and fast afterwards — which is what cold start looks like. Those slow
        calls would become the per-batch minimum if they were timed, and the
        reported number would be the cold-start cost rather than the steady
        state. 2 ms is roughly four orders of magnitude above a no-op call, so
        the two outcomes cannot be confused.
        """
        calls = 0
        cold_cost_us = 2000.0

        def cold_then_warm() -> None:
            nonlocal calls
            calls += 1
            if calls <= 3:
                time.sleep(0.002)

        result = measure(cold_then_warm, name="cold", iterations=5, warmup=3, repetitions=3)
        assert calls == 3 + 5 * 3
        assert result.best_us < cold_cost_us / 10, (
            f"a warm-up call leaked into the timed samples: best_us={result.best_us} us, "
            f"which is the {cold_cost_us:.0f} us cold-start cost rather than the steady state"
        )

    def test_the_reported_figure_is_microseconds_and_not_seconds(self):
        """The runner reports MICROSECONDS. It used to report seconds.

        Every field it publishes is named and documented in microseconds -
        ``Ceiling.value_us``, ``best_us``, ``batch_mins_us``,
        ``reportable_us`` ("the figure a human should quote, in
        microseconds") - but the samples were raw ``time.perf_counter``
        deltas, and ``perf_counter`` is in seconds. Nothing converted them,
        so every figure this module published was understated by 10^6, and
        because ``verdict`` compares a measurement against a ``Ceiling``
        that IS in microseconds, every measurement passed its ceiling
        trivially. The module's central guarantee was silently defeated.

        The reason it survived is the shape of the assertions that existed:
        they compared a measurement against another measurement, or against
        a ceiling so far above the real cost that a value off by 10^6 still
        fit underneath it. This one pins the unit against a cost the test
        chooses, so a wrong unit cannot pass.

        ``time.sleep`` is used deliberately. Its duration is specified by
        the platform rather than by this code, so the assertion is a band
        and not an equality - the point under test is the order of
        magnitude, which a 10^6 error cannot land inside.
        """
        sleep_s = 0.002
        expected_us = sleep_s * 1_000_000.0

        result = measure(lambda: time.sleep(sleep_s), name="known-cost", iterations=5, warmup=2, repetitions=3)

        assert 100.0 < result.reportable_us() < 20.0 * expected_us, (
            f"a {expected_us:.0f} us sleep was reported as {result.reportable_us()} us. "
            "The runner is emitting seconds into fields documented and named in microseconds."
        )
        # The same figure must clear a ceiling that is genuinely in microseconds. A
        # 2 s ceiling is below the 2 ms cost, so this is the assertion the old
        # seconds-valued samples could not pass.
        assert not verdict(result, Ceiling(2_000.0, "a microsecond ceiling")).passed, (
            f"a {expected_us:.0f} us call passed a 2000 us ceiling, so the comparison is "
            "comparing a seconds-valued measurement against a microsecond-valued ceiling"
        )


# --------------------------------------------------------------------------
# the estimator: min-of-N, then median-of-M
# --------------------------------------------------------------------------


class TestTheEstimator:
    def test_the_headline_is_the_median_of_the_per_batch_minima(self):
        """Drives the aggregation with known minima.

        ``_build`` is the pure step that picks the headline number, so it can be
        driven exactly. Going through ``measure`` instead would only prove that
        whatever the runner did was returned to the caller - which is the class
        of assertion that cannot fail.
        """
        minima = (10.0, 100.0, 20.0, 90.0, 30.0)
        medians = (11.0, 101.0, 21.0, 91.0, 31.0)
        result = protocol._build("probe", "us", minima, medians, iterations=3, warmup=1, repetitions=5)
        assert result.best_us == statistics.median(minima)
        assert result.batch_mins_us == minima
        assert result.batch_medians_us == medians

    def test_the_headline_is_not_the_minimum_of_the_minima(self):
        """The loud distinction between min-of-N and the median-of-M wrapper.

        Taking ``min()`` over the batch minima would report the luckiest batch.
        On a loaded runner every batch can catch a spike, so the winner would be
        the batch that happened to run quietest - which is exactly the number a
        fixed ceiling must not be checked against.
        """
        minima = (10.0, 100.0, 20.0, 90.0, 30.0)
        result = protocol._build("probe", "us", minima, minima, iterations=1, warmup=1, repetitions=5)
        assert result.best_us == 30.0
        assert result.best_us != min(minima)

    def test_an_even_number_of_batches_averages_the_two_central_minima(self):
        minima = (10.0, 20.0, 30.0, 40.0)
        result = protocol._build("probe", "us", minima, minima, iterations=1, warmup=1, repetitions=4)
        assert result.best_us == 25.0

    def test_the_aggregated_minimum_is_never_larger_than_the_worst_batch(self):
        minima = (10.0, 100.0, 20.0, 90.0, 30.0)
        result = protocol._build("probe", "us", minima, minima, iterations=1, warmup=1, repetitions=5)
        assert result.best_us <= max(minima)

    def test_every_batch_minimum_is_retained_for_spread_inspection(self):
        result = measure(_noop, name="spread", iterations=4, warmup=2, repetitions=5)
        assert len(result.batch_mins_us) == 5
        assert len(result.batch_medians_us) == 5
        assert result.spread_ratio >= 1.0

    def test_the_batch_median_is_reported_separately_from_the_minimum(self):
        """A mean-based runner's number is kept so the two can be compared.

        The protocol's number is the minimum-derived one; keeping the median
        means a reader can see whether the two disagree, which is how a
        distribution with one slow call per batch announces itself.
        """
        result = measure(_noop, name="two", iterations=8, warmup=2, repetitions=3)
        for batch_min, batch_median in zip(result.batch_mins_us, result.batch_medians_us, strict=True):
            assert batch_min <= batch_median

    def test_spread_ratio_is_max_over_min_of_the_batch_minima(self):
        result = measure(_noop, name="ratio", iterations=3, warmup=1, repetitions=4)
        lowest = min(result.batch_mins_us)
        assert result.spread_ratio == pytest.approx(max(result.batch_mins_us) / lowest)

    def test_a_zero_minimum_reports_infinite_spread_rather_than_dividing(self):
        empty = MicroResult(
            name="zero",
            unit="us",
            best_us=0.0,
            batch_mins_us=(0.0, 1.0),
            batch_medians_us=(0.0, 1.0),
            iterations=1,
            repetitions=2,
            warmup=1,
        )
        assert empty.spread_ratio == float("inf")
        assert empty.is_inside_noise is False


# --------------------------------------------------------------------------
# (c) `new < old` is refused, and a regression cannot pass on it
# --------------------------------------------------------------------------


class TestRelativeComparisonsAreRefused:
    def test_a_bare_number_is_not_a_ceiling(self):
        with pytest.raises(RelativeComparisonRejected) as excinfo:
            verdict(_result(10.0), 100.0)  # type: ignore[arg-type]
        assert "not an absolute ceiling" in str(excinfo.value)
        assert "passes on noise" in str(excinfo.value)

    def test_a_measurement_cannot_be_converted_into_a_ceiling(self):
        """The escape hatch that would reintroduce `new < old` must not exist."""
        measured = _result(10.0)
        with pytest.raises((ProtocolViolation, AttributeError, TypeError)):
            Ceiling(value_us=measured)  # type: ignore[arg-type]

    def test_a_below_ceiling_regression_is_a_failure_not_a_pass(self):
        """(c) the load-bearing case.

        The old arm was 100 us. This run is 40 us -- it is FASTER than the old
        arm, so `new < old` would pass it. The fixed ceiling is 50 us, and 40 is
        under it. So a pure relative criterion and a ceiling agree here, which
        is the case that hides the bug.

        Inverted: make the old arm 30 us and the ceiling 25 us. The new arm is
        40 us, so `new < old` FAILS but the ceiling also fails -- still
        agreement.

        The disagreement that matters is a run that is worse than the old arm yet
        under the ceiling: `new < old` fails, ceiling passes. That is a
        deliberate pass, because the ceiling is the contract.

        The case this test pins is the reverse and is the real hazard: the
        ceiling is fixed at 25 us, the old arm was 40 us, and this run measures
        30 us. It is faster than the old arm and it passes `new < old`, and it
        is WORSE than the 25 us ceiling. A harness using `new < old` reports a
        pass on a run that violates its own ceiling.
        """
        ceiling = Ceiling(value_us=25.0, label="regression")
        result = _result(30.0)
        assert 30.0 < 40.0, "this scenario is only interesting while the new arm beats the old one"
        outcome = verdict(result, ceiling)
        assert outcome.passed is False
        assert "EXCEEDS" in outcome.message
        with pytest.raises(AssertionError):
            outcome.assert_passed()

    def test_a_run_under_the_ceiling_passes_regardless_of_the_old_arm(self):
        outcome = verdict(_result(10.0), Ceiling(value_us=25.0))
        assert outcome.passed is True
        outcome.assert_passed()

    def test_the_verdict_carries_both_numbers_and_the_spread(self):
        outcome = verdict(_result(30.0), Ceiling(value_us=25.0, label="cap"))
        assert outcome.measured_us == 30.0
        assert outcome.ceiling_us == 25.0
        assert outcome.name
        assert outcome.spread_ratio >= 0.0

    def test_the_message_states_the_overrun_ratio(self):
        outcome = verdict(_result(50.0), Ceiling(value_us=25.0))
        assert "2.00x" in outcome.message

    def test_the_message_notes_when_the_spread_is_inside_the_noise_floor(self):
        """A verdict that silently hid a noisy arm would invite a wrong A/B."""
        steady = MicroResult(
            name="steady",
            unit="us",
            best_us=10.0,
            batch_mins_us=(10.0, 10.5, 10.2),
            batch_medians_us=(10.0, 10.5, 10.2),
            iterations=1,
            repetitions=3,
            warmup=1,
        )
        assert steady.is_inside_noise is True
        outcome = verdict(steady, Ceiling(value_us=50.0))
        assert "inside the noise floor" in outcome.message

    def test_the_message_notes_when_the_spread_exceeds_the_noise_floor(self):
        noisy = MicroResult(
            name="noisy",
            unit="us",
            best_us=10.0,
            batch_mins_us=(10.0, 40.0),
            batch_medians_us=(10.0, 40.0),
            iterations=1,
            repetitions=2,
            warmup=1,
        )
        assert noisy.is_inside_noise is False
        outcome = verdict(noisy, Ceiling(value_us=50.0))
        assert f"exceeds the {NOISE_FLOOR_RATIO}x noise floor" in outcome.message

    def test_a_ceiling_must_be_finite(self):
        with pytest.raises(ProtocolViolation):
            Ceiling(value_us=float("inf"))
        with pytest.raises(ProtocolViolation):
            Ceiling(value_us=float("nan"))

    def test_a_ceiling_must_be_positive(self):
        with pytest.raises(ProtocolViolation):
            Ceiling(value_us=0.0)
        with pytest.raises(ProtocolViolation):
            Ceiling(value_us=-5.0)


# --------------------------------------------------------------------------
# (d) the request-scale extension
# --------------------------------------------------------------------------


class TestRequestScale:
    @pytest.mark.asyncio
    async def test_fewer_repetitions_than_the_minimum_are_refused(self):
        with pytest.raises(ProtocolViolation) as excinfo:
            await measure_request_scale(_async_noop, name="req", iterations=2, warmup=1, repetitions=4)
        assert "at least 5 repetitions" in str(excinfo.value)
        assert "measures the machine" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_the_minimum_is_five(self):
        assert MIN_REQUEST_REPETITIONS == 5
        result = await measure_request_scale(
            _async_noop, name="req", iterations=2, warmup=1, repetitions=MIN_REQUEST_REPETITIONS
        )
        assert result.repetitions == 5

    @pytest.mark.asyncio
    async def test_both_a_median_of_medians_and_a_min_of_medians_are_reported(self):
        """(d) one figure hides the spread; both are required."""
        result = await measure_request_scale(
            _async_noop, name="req", iterations=3, warmup=1, repetitions=MIN_REQUEST_REPETITIONS
        )
        assert result.median_of_medians_us > 0
        assert result.min_of_medians_us > 0
        assert len(result.per_repetition_medians_us) == MIN_REQUEST_REPETITIONS
        assert len(result.per_repetition_mins_us) == MIN_REQUEST_REPETITIONS

    @pytest.mark.asyncio
    async def test_the_median_of_medians_is_at_least_the_min_of_medians(self):
        result = await measure_request_scale(
            _async_noop, name="req", iterations=3, warmup=1, repetitions=MIN_REQUEST_REPETITIONS
        )
        assert result.median_of_medians_us >= result.min_of_medians_us

    @pytest.mark.asyncio
    async def test_the_reported_medians_are_actually_the_median_of_the_samples(self):
        """Recomputes the aggregation from the per-repetition figures."""
        result = await measure_request_scale(
            _async_noop, name="req", iterations=4, warmup=1, repetitions=MIN_REQUEST_REPETITIONS
        )
        assert result.median_of_medians_us == pytest.approx(statistics.median(result.per_repetition_medians_us))
        assert result.min_of_medians_us == pytest.approx(statistics.median(result.per_repetition_mins_us))

    @pytest.mark.asyncio
    async def test_the_request_scale_result_also_carries_the_run_stamp(self):
        result = await measure_request_scale(
            _async_noop, name="req", iterations=2, warmup=1, repetitions=MIN_REQUEST_REPETITIONS
        )
        assert isinstance(result.run, RunStamp)
        assert result.run.cpu_count is None or result.run.cpu_count >= 1


# --------------------------------------------------------------------------
# (e) every emitted number is stamped
# --------------------------------------------------------------------------


class TestEveryNumberIsStamped:
    def test_the_stamp_names_the_platform_the_machine_and_the_cpu_count(self):
        text = protocol.stamp().one_line()
        assert "platform=" in text
        assert "machine=" in text
        assert "cpu_count=" in text
        assert "event_loop=" in text
        assert "python=" in text

    def test_the_render_includes_the_stamp_the_counts_and_the_spread(self):
        result = measure(_noop, name="stamped", iterations=3, warmup=2, repetitions=3)
        text = render(result)
        assert result.run.one_line() in text
        # The counts are rendered as n=<iterations>x<repetitions>, so assert the
        # exact pair rather than the two fields separately: "n=3x3" pins both
        # numbers at once and a render that dropped either would fail.
        assert "n=3x3" in text
        assert "warmup=2" in text
        assert "spread" in text

    def test_the_render_prints_every_batch_minimum_and_median(self):
        result = measure(_noop, name="detail", iterations=3, warmup=1, repetitions=4)
        text = render(result)
        assert "batch_mins" in text
        assert "batch_medians" in text

    def test_the_render_of_a_request_scale_result_carries_the_stamp_too(self):
        stub = _request_result_stub()
        rendered = render(stub)
        assert "platform=" in rendered
        assert f"n={stub.iterations}x{stub.repetitions}" in rendered

    def test_the_event_loop_name_is_reported_even_with_no_running_loop(self):
        text = protocol._event_loop_name()
        assert isinstance(text, str)
        assert text

    def test_a_number_cannot_be_rendered_without_the_runner(self):
        """``render`` takes a result object, not a float.

        The signature is the enforcement: there is no path from a bare number to
        the output format, so a caller cannot skip the stamp by accident.
        """
        with pytest.raises((AttributeError, TypeError)):
            render(12.5)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# (a) the protocol is the only way a microbenchmark times code
# --------------------------------------------------------------------------


class TestTheProtocolIsTheOnlyWayToEmitANumber:
    def test_every_microbenchmark_file_exists(self):
        for relative in MICROBENCHMARK_FILES:
            assert (REPO_ROOT / relative).is_file(), f"{relative} does not exist"

    def test_every_microbenchmark_file_routes_through_the_runner(self):
        for relative in MICROBENCHMARK_FILES:
            assert _imports_the_runner(REPO_ROOT / relative), (
                f"{relative} times code but never imports src.benchmarks.protocol, so it cannot be "
                f"using the protocol. Add the import, or move the file to ALLOWED_RAW_TIMERS with a reason."
            )

    def test_the_routing_check_reads_the_import_not_the_prose(self):
        """A docstring naming the runner must not satisfy the rule above.

        This check used to be `"benchmarks.protocol" in source`, which a
        paragraph of prose satisfies: every migrated file documents the protocol
        by name, so deleting the import left the rule green. It is the same
        defect as a rule that cannot see its own subject — the negative test that
        found it is recorded on the bead.
        """
        for relative in MICROBENCHMARK_FILES:
            source = (REPO_ROOT / relative).read_text(encoding="utf-8")
            assert "src.benchmarks.protocol" in source or "benchmarks import protocol" in source, (
                f"{relative} no longer mentions the runner at all, so this test cannot tell you "
                "the AST rule is doing the work. Update both when the prose changes."
            )

    def test_the_routing_check_would_reject_a_file_that_only_mentions_the_runner(self):
        """The AST rule must FAIL on prose that names the runner without importing it.

        This is the negative test for the rule above, and it is why the rule had
        to move off the substring form: every migrated file's docstring names
        ``src.benchmarks.protocol``, so deleting the import left it green.
        """
        probe = REPO_ROOT / "tests" / "unit" / "benchmarks" / "_prose_only_probe.py"
        assert not probe.exists(), f"{probe.name} is a scratch file for one test and must not survive it"
        probe.write_text(
            '"""Timing via src.benchmarks.protocol."""\nimport time\n\n\ndef t() -> float:\n'
            "    start = time.perf_counter()\n    return time.perf_counter() - start\n",
            encoding="utf-8",
        )
        try:
            assert _calls_the_clock(probe), "the probe must call the clock, or the rule has nothing to catch"
            assert not _imports_the_runner(probe), (
                "the probe mentions the runner in prose only; if the AST rule accepts it, the rule "
                "is reading the docstring rather than the imports"
            )
        finally:
            probe.unlink()

    def test_the_file_list_covers_every_benchmark_module_in_the_tree(self):
        """A new benchmark file must be classified, not silently ignored.

        Without this the previous two tests would pass while a brand-new file
        grew its own hand-rolled timing loop.
        """
        classified = set(MICROBENCHMARK_FILES) | set(ALLOWED_RAW_TIMERS)
        unclassified: list[str] = []
        for relative in sorted(_benchmark_files()):
            if relative not in classified:
                unclassified.append(relative)
        assert not unclassified, (
            "these benchmark files are neither routed through the protocol nor listed in "
            f"ALLOWED_RAW_TIMERS with a reason: {unclassified}"
        )

    def test_every_allowed_raw_timer_carries_a_reason(self):
        for relative, reason in ALLOWED_RAW_TIMERS.items():
            assert reason.strip(), f"{relative} is allowed to bypass the protocol without saying why"
            assert len(reason) > 20, f"{relative}'s reason is too terse to be useful"

    def test_every_allowed_raw_timer_really_does_call_the_clock(self):
        """An allowlist entry that no longer times anything is dead weight.

        Removing the exemption is then the correct follow-up, and this test says
        so instead of letting the list rot.
        """
        for relative in ALLOWED_RAW_TIMERS:
            assert _calls_the_clock(REPO_ROOT / relative), (
                f"{relative} is allowlisted as timing code but no longer calls perf_counter; "
                "remove it from ALLOWED_RAW_TIMERS so the exemption cannot be inherited for free"
            )

    def test_no_allowlisted_file_is_also_routed_through_the_runner(self):
        """The two lists are disjoint, so a file cannot claim both statuses."""
        assert not set(MICROBENCHMARK_FILES) & set(ALLOWED_RAW_TIMERS)

    def test_the_runner_module_itself_is_not_classified_as_a_benchmark(self):
        assert "src/benchmarks/protocol.py" not in set(MICROBENCHMARK_FILES)
        assert "src/benchmarks/protocol.py" not in set(ALLOWED_RAW_TIMERS)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _result(measured_us: float, *, repetitions: int = 3) -> MicroResult:
    """A MicroResult with a known measured value and no timing involved."""
    return MicroResult(
        name="probe",
        unit="us",
        best_us=measured_us,
        batch_mins_us=tuple(measured_us for _ in range(repetitions)),
        batch_medians_us=tuple(measured_us for _ in range(repetitions)),
        iterations=1,
        repetitions=repetitions,
        warmup=1,
    )


def _request_result_stub() -> protocol.RequestResult:
    return protocol.RequestResult(
        name="probe",
        median_of_medians_us=10.0,
        min_of_medians_us=9.0,
        per_repetition_medians_us=(10.0, 11.0, 10.0, 12.0, 10.0),
        per_repetition_mins_us=(9.0, 10.0, 9.0, 11.0, 9.0),
        iterations=2,
        repetitions=5,
        warmup=1,
    )


async def _async_noop() -> None:
    """An awaitable-returning callable with a small, constant cost."""
    return None


def _benchmark_files() -> list[str]:
    """Every clock-calling ``*.py`` under the benchmark trees.

    Narrowed to files that ACTUALLY read a clock. An earlier version
    classified every ``*.py`` in these trees and produced 47 unclassified
    files: ``percentiles.py``, ``protocol.py``, ``run_manifest.py``,
    ``locustfile.py``, the fixtures, the whole ``test_optimizations_r4/``
    subtree. None of those can bypass the protocol, because none of them times
    anything, and a rule that lists them is a rule nobody can satisfy. This is
    the same over-broad-rule mistake as ``dict.keys()`` in H-6 and the
    ``SCAN`` substring in H-3: a rule that flags the innocent is a rule that
    gets deleted rather than obeyed.

    The runner itself (``src/benchmarks/protocol.py``) is skipped: it IS the
    clock caller, so classifying it as a benchmark that bypasses the protocol
    would be nonsense.

    The tree is still enumerated in full on every run, so a NEW file that grows
    its own timing loop is classified immediately.
    """
    runner = "src/benchmarks/protocol.py"
    found: list[str] = []
    for root in ("tests/benchmarks", "tests/performance", "tests/unit/benchmarks", "src/benchmarks"):
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if path.name == "__init__.py" or path.name == "conftest.py":
                continue
            if not _calls_the_clock(path):
                continue
            relative = path.relative_to(REPO_ROOT).as_posix()
            if relative == runner:
                continue
            found.append(relative)
    return found


def _calls_the_clock(path: Path) -> bool:
    """True if this file times something with the wall clock."""
    if path.suffix != ".py":
        return False
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr in {"perf_counter", "perf_counter_ns", "monotonic", "monotonic_ns"}:
            return True
    return False


def _imports_the_runner(path: Path) -> bool:
    """True if this file has a real IMPORT of src.benchmarks.protocol.

    Read off the import statements, not off the text. A substring check is
    satisfied by any docstring that mentions the runner by name, and every
    migrated file documents it, so the substring form could not tell a file
    that routes through the protocol from one that only talks about it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "") == "src.benchmarks":
            return any(alias.name == "protocol" for alias in node.names)
        if isinstance(node, ast.ImportFrom) and (node.module or "") == "src.benchmarks.protocol":
            return True
    return False
