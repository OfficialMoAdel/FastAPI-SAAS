"""T-1..T-8: the Phase-0 telemetry instruments must be correct before their output is trusted.

Each test here pins a property whose violation would produce a NUMBER THAT LOOKS
FINE and is not: a sampler that inflates what it measures, a void condition that
annotates instead of refusing, a counter blind to the traffic it exists to count,
a manifest that defaults what it could not read.

The cost-ceiling tests are not "is it fast". Each asserts the instrument stays
under an ABSOLUTE ceiling declared in the module, which is the requirement - a
``new < old`` comparison would pass on noise, and intra-arm variance in this
repository is already +/-2x.
"""

from __future__ import annotations

import asyncio
import gc

import pytest

from src.app.core.monitoring.gc_pause import (
    OBSERVER_COST_CEILING_US,
    GCPauseObserver,
    assert_series_are_disjoint,
    measure_observer_cost,
)
from src.app.core.monitoring.loop_lag import (
    MINIMUM_RATE_HZ,
    SAMPLE_INTERVAL_SECONDS,
    LoopLagSampler,
    assert_sampler_cost,
    assert_validated_on_production_loop,
    measure_sampler_cost,
)
from src.app.core.monitoring.request_timer import (
    RESPONSE_TIME_HEADER,
    TIMER_COST_CEILING_US,
    RequestTimer,
    assert_excluded_from_own_timing,
    assert_series_not_merged,
    measure_disabled_cost,
    measure_enabled_cost,
)
from src.app.core.monitoring.telemetry import (
    Ceiling,
    CeilingExceeded,
    SeriesStore,
    non_2xx_keys,
    status_class,
)

# -- T-1: event-loop lag sampler ---------------------------------------------


class TestLoopLagSampler:
    async def test_samples_are_recorded_at_the_requested_interval(self) -> None:
        sampler = LoopLagSampler(enabled=True, interval=0.005)
        sampler.start()
        await asyncio.sleep(0.15)
        await sampler.stop()

        assert len(sampler) > 0, "the sampler recorded nothing at all"
        export = sampler.export()
        assert export["requested_interval_s"] == 0.005
        assert export["series"]["count"] == len(sampler)

    async def test_achieved_rate_is_measured_not_assumed(self) -> None:
        """The rate must come from a clock, not from the requested interval.

        A sampler that reported ``1 / interval`` would claim 200 Hz for a run that
        actually achieved 63 Hz on this host, and the artefact would carry a
        requirement it never met.
        """
        sampler = LoopLagSampler(enabled=True, interval=SAMPLE_INTERVAL_SECONDS)
        sampler.start()
        await asyncio.sleep(0.2)
        await sampler.stop()

        export = sampler.export()
        measured = export["achieved_rate_hz"]
        assert measured >= 0.0
        # The verdict is derived from the measured figure and must agree with it.
        assert export["meets_minimum_rate"] == (measured >= MINIMUM_RATE_HZ)

    async def test_disabled_sampler_records_nothing_and_never_starts_work(self) -> None:
        sampler = LoopLagSampler(enabled=False)
        sampler.start()
        await asyncio.sleep(0.05)
        await sampler.stop()

        assert len(sampler) == 0
        assert sampler.export()["enabled"] is False

    async def test_sampler_never_blocks_the_loop(self) -> None:
        """The load-bearing requirement: a slow consumer must not slow the loop.

        The sampler yields via ``asyncio.sleep`` every iteration. If it awaited
        anything else, or held a lock the request path wants, this test is what
        would catch it - and the failure it prevents is the sampler corrupting the
        baseline it exists to measure.
        """
        sampler = LoopLagSampler(enabled=True, interval=0.005)

        ticks = 0

        async def consumer() -> None:
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.002)

        consumer_task = asyncio.create_task(consumer())
        sampler.start()
        await asyncio.sleep(0.12)
        await sampler.stop()
        consumer_task.cancel()

        # The consumer kept running at its own rate while the sampler sampled.
        # A sampler that blocked the loop would show a tick count near zero. The
        # bar is deliberately LOW: this host's asyncio has ~15 ms timer
        # granularity, so a consumer asking for 2 ms ticks gets a handful in
        # 120 ms. The property under test is "the consumer was not starved", and
        # starvation looks like single digits, not like a missed tick or two.
        assert ticks >= 3, f"consumer only ticked {ticks} times; the sampler blocked it"
        assert len(sampler) > 0

    def test_sampler_cost_is_under_its_declared_ceiling(self) -> None:
        measured = measure_sampler_cost()
        assert measured["measured_us"] <= measured["ceiling_us"]

    def test_sampler_cost_ceiling_is_absolute_not_relative(self) -> None:
        """A ceiling that can be exceeded must raise, and must not be adjustable."""
        with pytest.raises(CeilingExceeded):
            assert_sampler_cost(1e9)

    def test_export_records_the_loop_implementation(self) -> None:
        """A lag series without its loop identity is two different numbers."""
        export = LoopLagSampler(enabled=False).export()
        assert "loop_module" in export
        assert export["representativeness"] in ("representative", "laboratory-only")

    def test_asyncio_run_cannot_be_reported_as_uvloop_validation(self) -> None:
        """Acceptance criterion (d): a lab run is not a validation run."""
        export = LoopLagSampler(enabled=False).export()
        if not export["loop_module"].startswith("uvloop"):
            with pytest.raises(AssertionError, match="uvloop"):
                assert_validated_on_production_loop(export)

    def test_uvloop_export_is_accepted_as_validation(self) -> None:
        export = {"loop_module": "uvloop.loop", "representativeness": "representative"}
        assert assert_validated_on_production_loop(export) is export


# -- T-2: GC pause ------------------------------------------------------------


class TestGCPauseObserver:
    def test_a_real_collection_is_counted_and_histogrammed(self) -> None:
        observer = GCPauseObserver(enabled=True)
        with observer:
            gc.collect()

        export = observer.export()
        assert export["gc_pause_collection_count"] >= 1
        # A count alone is not enough - the acceptance criterion is a histogram.
        assert export["gc_pause_histogram_ms"], "no pause-time histogram was emitted"
        assert export["gc_pause_bounds_ms"]

    def test_pause_time_histogram_and_loop_lag_cannot_be_conflated(self) -> None:
        """Acceptance criterion (c): separate series, provably."""
        gc_export = GCPauseObserver(enabled=True).export()
        lag_export = LoopLagSampler(enabled=False).export()
        assert_series_are_disjoint(gc_export, lag_export)

    def test_series_sharing_a_key_is_rejected(self) -> None:
        """A shared key would let a GC pause be read as loop lag."""
        with pytest.raises(AssertionError, match="share keys|same instrument"):
            assert_series_are_disjoint(
                {"instrument": "x", "gc_pause_a": 1, "shared_key": 2},
                {"instrument": "x", "loop_lag_a": 1, "shared_key": 3},
            )

    def test_disabled_observer_is_invisible_to_gc_callbacks(self) -> None:
        """A disabled observer must not register - that is global state."""
        before = list(gc.callbacks)
        observer = GCPauseObserver(enabled=False)
        observer.install()
        assert list(gc.callbacks) == before
        assert observer._installed is False

    def test_uninstall_restores_the_previous_callback_list(self) -> None:
        """Leaving a patched callback behind changes every later gc client."""
        sentinel_before = list(gc.callbacks)
        observer = GCPauseObserver(enabled=True)
        observer.install()
        assert gc.callbacks != sentinel_before
        observer.uninstall()
        assert list(gc.callbacks) == sentinel_before

    def test_observer_cost_is_under_its_ceiling(self) -> None:
        measured = measure_observer_cost()
        assert measured["measured_us"] <= OBSERVER_COST_CEILING_US

    def test_event_loop_independence_is_recorded(self) -> None:
        """GC behaviour does not change under uvloop; the magnitudes do."""
        assert GCPauseObserver(enabled=True).export()["event_loop_independent"] is True


# -- T-3: server-side request timer -------------------------------------------


async def _sleeping_app(scope, receive, send, *, delay: float = 0.005) -> None:
    await asyncio.sleep(delay)
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


class TestRequestTimer:
    async def test_header_is_emitted_when_enabled(self) -> None:
        timer = RequestTimer(_sleeping_app, enabled=True)
        captured: dict = {}

        async def send(message: dict) -> None:
            captured[message["type"]] = message

        await timer({"type": "http"}, None, send)
        headers = dict(captured["http.response.start"]["headers"])
        assert RESPONSE_TIME_HEADER.encode().lower() in headers
        assert float(headers[RESPONSE_TIME_HEADER.encode().lower()]) >= 0.0

    async def test_no_header_and_no_work_when_disabled(self) -> None:
        """Acceptance criterion (d): the disabled path adds nothing."""
        timer = RequestTimer(_sleeping_app, enabled=False)
        captured: dict = {}

        async def send(message: dict) -> None:
            captured[message["type"]] = message

        await timer({"type": "http"}, None, send)
        assert captured["http.response.start"]["headers"] == []
        assert timer.export()["server_sample_count"] == 0

    async def test_reported_time_excludes_the_timers_own_cost(self) -> None:
        """Acceptance criterion (b): the timer's own cost is NOT in its figure.

        Measured by running the SAME response N times and comparing the
        distribution with and without the timer, over MANY repetitions. A single
        pair cannot do this: this host's asyncio has ~15 ms timer granularity, so
        one timed run and one pass-through differ by milliseconds of scheduler
        noise - which is three orders of magnitude larger than the ~2 us of timer
        work, and would drown any signal.

        Averaging N=40 pairs does not remove the granularity (it is a floor, not
        variance, so it shifts both arms equally) but it does stop a single unlucky
        scheduling decision from deciding the result. The bar is then set at 1 ms:
        comfortably above the ~2 us the timer actually costs, and far below the
        ~15 ms granularity, so a timer that folded its own work into the reported
        value by a whole order of magnitude would still fail.
        """
        samples = 40
        timer = RequestTimer(_sleeping_app, enabled=True)

        timed: list[float] = []
        baseline: list[float] = []

        for _ in range(samples):
            captured: dict = {}

            async def send(message: dict) -> None:
                captured[message["type"]] = message

            timer.reset()
            await timer({"type": "http"}, None, send)
            timed.append(timer.export()["server_summary_ms"]["mean"])

            started = asyncio.get_event_loop().time()
            await _sleeping_app({"type": "http"}, None, send)
            baseline.append((asyncio.get_event_loop().time() - started) * 1000.0)

        timed_mean = sum(timed) / len(timed)
        baseline_mean = sum(baseline) / len(baseline)

        assert_excluded_from_own_timing(timed_mean, baseline_mean, tolerance_us=1000.0)

    def test_timer_cost_is_bounded_below_the_exclusion_bar(self) -> None:
        """The two bounds must be consistent, or the test above proves nothing.

        If the timer's measured cost were anywhere near the 1 ms exclusion bar,
        a genuine inclusion would be indistinguishable from noise. Pinning the
        relationship is what makes the exclusion test meaningful rather than
        merely passing.
        """
        measured = measure_enabled_cost()["measured_us"]
        assert measured < 1000.0 / 10, (
            f"the timer costs {measured} us, which is within a factor of ten of the "
            f"1000 us exclusion bar; the exclusion test could no longer distinguish "
            f"self-inclusion from scheduler noise"
        )

    async def test_server_and_client_series_stay_distinct(self) -> None:
        """Acceptance criterion (c): never averaged or substituted."""
        export = RequestTimer(_sleeping_app, enabled=False).export()
        assert_series_not_merged(export)
        assert any(key.startswith("server_") for key in export)

    def test_mixing_client_into_a_server_key_is_rejected(self) -> None:
        with pytest.raises(AssertionError, match="mixes client and server"):
            assert_series_not_merged({"server_x": 1, "client_server_y": 2})

    def test_enabled_and_disabled_costs_are_under_the_ceiling(self) -> None:
        assert measure_enabled_cost()["measured_us"] <= TIMER_COST_CEILING_US
        assert measure_disabled_cost()["measured_us"] <= TIMER_COST_CEILING_US


# -- the shared substrate ----------------------------------------------------


class TestTelemetrySubstrate:
    def test_series_store_is_bounded_and_reports_what_it_dropped(self) -> None:
        """A sampler that silently stops looks exactly like one with no events."""
        store = SeriesStore(maxlen=4)
        for value in range(10):
            store.record(float(value))

        assert len(store) == 4
        assert store.dropped == 6

    def test_wrapped_series_export_in_time_order(self) -> None:
        """Once the ring wraps, slot order is arbitrary - and unusable."""
        store = SeriesStore(maxlen=3)
        for value in range(6):
            store.record(float(value))

        offsets = [sample["offset_s"] for sample in store.export()["samples"]]
        assert offsets == sorted(offsets), "a wrapped ring buffer exported out of time order"

    def test_reset_clears_the_dropped_count_and_the_cursor(self) -> None:
        store = SeriesStore(maxlen=2)
        for value in range(5):
            store.record(float(value))
        store.reset()
        assert len(store) == 0
        assert store.dropped == 0

    def test_zero_and_false_are_present_values_not_holes(self) -> None:
        """A disabled AUTO_BAN_ENABLED is a configuration, not a missing field."""
        from src.app.core.monitoring.app_config_manifest import _is_absent

        assert _is_absent(0) is False
        assert _is_absent(False) is False
        assert _is_absent(None) is True
        assert _is_absent("") is True
        assert _is_absent({}) is True

    def test_status_classification_drives_the_void_rule(self) -> None:
        assert status_class(200) == "2xx"
        assert status_class(204) == "2xx"
        assert status_class(301) == "3xx"
        assert status_class(401) == "4xx"
        assert status_class(429) == "4xx"
        assert status_class(503) == "5xx"

    def test_non_2xx_keys_are_sorted_and_skip_zero_counts(self) -> None:
        offenders = non_2xx_keys({"2xx": 10, "5xx": 1, "4xx": 0})
        assert offenders == ["5xx"], "a zero-count class must not void a phase"

    def test_ceiling_comparison_is_absolute_not_new_less_old(self) -> None:
        ceiling = Ceiling(10.0, "test")
        ceiling.check(9.9)
        with pytest.raises(CeilingExceeded, match="absolute"):
            ceiling.check(10.1)
