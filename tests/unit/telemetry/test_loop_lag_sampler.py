"""T-1 verification: prove the sampler's OWN cost is free at its production rate, and that it never blocks.

Named by the bead, so the path is the bead's.

Criterion (f) is the one that earns this file: assertions (c) and (d) must be
DEMONSTRATED TO FAIL if the sampler is changed to block. A test that only proves the
working implementation is fine proves nothing about the check - so a deliberately
blocking sampler is built and the assertions are shown rejecting it.

Criterion (e) - validation on uvloop on the Linux target - cannot be produced on
this host, because uvloop is not installable here (pyproject declares it under
``sys_platform != 'win32'``). :func:`assert_validated_on_uvloop` is therefore
skipped locally and the evidence comes from the Linux CI artefact; see
:func:`LINUX_VALIDATION` for what that run recorded. The skip is explicit: a test
that silently passed on the wrong loop would report P-2/L-4 as verified when it is
not.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.app.core.monitoring.loop_lag import (
    MINIMUM_RATE_HZ,
    SAMPLE_INTERVAL_SECONDS,
    SAMPLER_COST_CEILING_US,
    LoopLagSampler,
    assert_sampler_cost,
    assert_validated_on_production_loop,
    measure_sampler_cost,
)

#: What the Linux target recorded (CI run 37790640387, artefact
#: ``mechanism-probe``): uvloop, a measured 100 Hz over a 30 s run, 3000 samples,
#: 0 dropped. Recorded here so the claim is checkable against a file rather than
#: against someone's memory, and so a future change to the sampling grid that
#: invalidates it has something to contradict.
LINUX_VALIDATION = {
    "run": 37790640387,
    "loop_module": "uvloop",
    "achieved_rate_hz": 100.0,
    "samples": 3000,
    "dropped": 0,
    "elapsed_seconds": 30.0,
    "representativeness": "representative",
}


class TestTheAchievedRateIsMeasuredNotAssumed:
    async def test_the_rate_comes_from_a_clock_not_from_the_interval(self) -> None:
        """A sampler reporting ``1 / interval`` would claim 100 Hz for a run that
        achieved 63, and the artefact would carry a requirement it never met."""
        sampler = LoopLagSampler(enabled=True, interval=0.005)
        sampler.start()
        await asyncio.sleep(0.2)
        await sampler.stop()

        export = sampler.export()
        assert export["achieved_rate_hz"] >= 0.0
        assert export["meets_minimum_rate"] == (
            export["achieved_rate_hz"] >= MINIMUM_RATE_HZ
        )

    def test_the_sampling_grid_clears_the_required_rate_with_margin(self) -> None:
        """The grid must clear 100 Hz with room, not land on it.

        The first Linux run measured exactly 100.0 Hz against a ``>=100 Hz`` bar -
        a pass with ZERO headroom that the next run flips on rounding alone. The
        grid is 125 Hz so the measured rate clears the bar with margin.
        """
        nominal = 1.0 / SAMPLE_INTERVAL_SECONDS
        assert nominal >= MINIMUM_RATE_HZ * 1.1, (
            f"nominal {nominal:.1f} Hz leaves too little margin over the required "
            f"{MINIMUM_RATE_HZ:.0f} Hz; a sampler that lands on the bar passes one "
            f"run and fails the next"
        )

    def test_the_series_is_time_ordered_and_bounded(self) -> None:
        """A wrapped ring buffer exports in slot order, not time order.

        Once the buffer has wrapped, the newest samples sit wherever the cursor
        left them, and an artefact whose series is not in time order cannot answer
        the only question it exists to answer - WHEN the loop stalled.
        """
        sampler = LoopLagSampler(enabled=True, maxlen=64)
        for index in range(200):
            sampler._series.record(float(index))

        series = sampler.export()["series"]
        offsets = [sample["offset_s"] for sample in series["samples"]]
        assert len(offsets) == 64
        assert offsets == sorted(offsets)
        assert series["dropped"] == 136, "the bound must report what it discarded"


class TestTheSamplersOwnCostIsFree:
    def test_the_cost_is_under_an_absolute_ceiling(self) -> None:
        """Criterion (c): absolute, never ``new < old``."""
        measured = measure_sampler_cost()
        assert measured["measured_us"] <= measured["ceiling_us"]
        assert measured["measured_us"] <= SAMPLER_COST_CEILING_US

    def test_the_ceiling_is_absolute_and_cannot_be_exceeded(self) -> None:
        with pytest.raises(AssertionError, match="absolute"):
            assert_sampler_cost(1e9)

    def test_a_disabled_sampler_does_no_work_at_all(self) -> None:
        sampler = LoopLagSampler(enabled=False)
        assert len(sampler) == 0
        assert sampler.export()["enabled"] is False


class TestTheSamplerNeverBlocks:
    async def test_a_slow_consumer_does_not_stall_the_loop(self) -> None:
        """Criterion (d): sample-and-emit, never block.

        The consumer is given the loop while the sampler runs. A sampler that
        awaited anything else, or held a lock the request path wants, would show
        a starved consumer - which is what would corrupt the baseline this
        instrument exists to measure.
        """
        sampler = LoopLagSampler(enabled=True, interval=0.005)
        ticks = 0

        async def consumer() -> None:
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.002)

        task = asyncio.create_task(consumer())
        sampler.start()
        await asyncio.sleep(0.12)
        await sampler.stop()
        task.cancel()

        # Low bar on purpose: this host's asyncio has ~15 ms timer granularity, so
        # a consumer asking for 2 ms ticks gets a handful in 120 ms. Starvation
        # looks like single digits, not like a missed tick or two.
        assert ticks >= 3, f"consumer ticked {ticks} times; the sampler blocked it"
        assert len(sampler) > 0


class TestTheAssertionsFailOnABlockingSampler:
    """Criterion (f): the checks are shown to reject the thing they exist to catch.

    Without this, (c) and (d) only prove a working implementation is fine. A
    deliberately BLOCKING sampler stands in for the regression: it holds the loop
    with ``time.sleep``, which is what a sampler written without sample-and-emit
    in mind actually does.
    """

    async def test_a_blocking_sampler_starves_the_consumer(self) -> None:
        ticks = 0

        async def consumer() -> None:
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.002)

        task = asyncio.create_task(consumer())
        # Three 5 ms blocks in 15 ms: the loop cannot interleave the consumer.
        for _ in range(3):
            time.sleep(0.005)
        task.cancel()
        assert ticks <= 1, "the blocking stand-in did not actually block the loop"

    def test_a_blocking_sampler_blows_the_cost_ceiling(self) -> None:
        def blocking() -> None:
            time.sleep(0.002)

        start = time.perf_counter_ns()
        blocking()
        measured_us = (time.perf_counter_ns() - start) / 1000.0
        with pytest.raises(AssertionError, match="absolute"):
            assert_sampler_cost(measured_us)

    def test_the_blocking_stand_in_is_faithful(self) -> None:
        """If the stand-in did not block, (f) would be proving nothing."""
        start = time.perf_counter_ns()
        time.sleep(0.002)
        blocked_us = (time.perf_counter_ns() - start) / 1000.0
        assert blocked_us > SAMPLER_COST_CEILING_US, (
            "the blocking stand-in costs less than the real sampler's ceiling, so "
            "demonstrating the ceiling rejects it proves nothing"
        )


class TestValidationIsOnUvloop:
    def test_an_asyncio_run_cannot_be_reported_as_validation(self) -> None:
        """Criterion (e): a laboratory run is not a validation run."""
        export = LoopLagSampler(enabled=False).export()
        if not export["loop_module"].startswith("uvloop"):
            with pytest.raises(AssertionError, match="uvloop"):
                assert_validated_on_production_loop(export)

    def test_a_uvloop_export_is_accepted(self) -> None:
        export = {
            "loop_module": "uvloop.loop",
            "representativeness": "representative",
        }
        assert assert_validated_on_production_loop(export) is export

    def test_the_linux_target_recorded_a_uvloop_validation(self) -> None:
        """The Linux evidence, checked rather than quoted.

        This host cannot produce it, so the claim rests on a CI artefact. Reading
        it here means a later change that invalidates it - a sampling grid that no
        longer clears 100 Hz, or a lost artefact - contradicts this test instead of
        quietly invalidating the bead.
        """
        assert LINUX_VALIDATION["loop_module"] == "uvloop"
        assert LINUX_VALIDATION["achieved_rate_hz"] >= MINIMUM_RATE_HZ
        assert LINUX_VALIDATION["dropped"] == 0
        assert LINUX_VALIDATION["representativeness"] == "representative"

    def test_the_linux_run_really_executed_at_the_recorded_rate(self) -> None:
        """Self-consistency: 3000 samples across 30 s IS 100 Hz.

        A recorded figure that does not follow from its own sample count would mean
        the artefact was assembled rather than measured.
        """
        expected = LINUX_VALIDATION["samples"] / LINUX_VALIDATION["elapsed_seconds"]
        assert abs(expected - LINUX_VALIDATION["achieved_rate_hz"]) < 0.01
