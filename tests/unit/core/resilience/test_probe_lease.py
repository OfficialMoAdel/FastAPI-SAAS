"""The HALF_OPEN probe slot must never be able to wedge a breaker.

The probe slot is claimed before a recovery probe and released when the
probe reports its outcome. A CANCELLED probe (a client disconnect, a request
timeout, a shutdown) never reports an outcome — and ``asyncio.CancelledError``
is a ``BaseException``, so the ``except Exception`` arm that records the
failure never runs. The slot stayed claimed for the life of the process, and
every later HALF_OPEN caller was rejected: for the DB breaker, 503 for ALL
traffic.

Three things close that:

* the decorator and ``db_circuit_guard`` release the slot on a BaseException;
* the claim is a LEASE — a claim older than the recovery window is stale
  (its owner died) and is reclaimed;
* ``probe_gate`` releases the slot on every exit path.
"""

import asyncio
import time

import pytest

from src.app.core.resilience.db_circuit_breaker import db_circuit_guard
from src.app.core.utils.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    circuit_breaker,
)


async def _half_open_breaker(name: str = "probe-test", recovery_timeout: float = 60.0) -> CircuitBreaker:
    """A breaker that is OPEN and whose recovery window has elapsed.

    The window is long (60s) so the PROBE LEASE stays fresh: a lease that
    expired instantly would let every caller reclaim the slot, which is a
    different behaviour (and a different test).
    """
    breaker = CircuitBreaker(name=name, failure_threshold=1, recovery_timeout=recovery_timeout)
    await breaker.record_failure(RuntimeError("down"))
    # The recovery window has elapsed.
    breaker._last_failure_time = time.time() - recovery_timeout - 1
    return breaker


class TestDecoratorDoesNotWedge:
    async def test_a_cancelled_probe_releases_the_slot(self) -> None:
        """The cancelled call must not leave the breaker unrecoverable."""
        breaker = await _half_open_breaker()
        assert breaker.state == CircuitState.HALF_OPEN

        @circuit_breaker(breaker)
        async def probe() -> None:
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await probe()

        assert breaker._probe_in_progress is False
        # The next HALF_OPEN caller is admitted — the breaker recovered.
        assert breaker.state == CircuitState.HALF_OPEN

    async def test_a_cancelled_probe_leaves_the_breaker_usable(self) -> None:
        """A second probe is admitted instead of being rejected forever."""
        breaker = await _half_open_breaker()
        calls = 0

        @circuit_breaker(breaker)
        async def probe() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await probe()

        await probe()

        assert calls == 2
        assert breaker._probe_in_progress is False


class TestDbCircuitGuardDoesNotWedge:
    async def test_a_cancelled_operation_releases_the_slot(self) -> None:
        """The DB breaker is the hot path — a wedge is 503 for all traffic."""
        from src.app.core.resilience import db_circuit_breaker as module

        breaker = module.db_circuit
        original = (breaker.failure_threshold, breaker.recovery_timeout)
        try:
            breaker.reset()
            breaker.failure_threshold = 1
            breaker.recovery_timeout = 0.0
            await breaker.record_failure(RuntimeError("down"))
            breaker._last_failure_time = 0.0
            assert breaker.state == CircuitState.HALF_OPEN

            with pytest.raises(asyncio.CancelledError):
                async with db_circuit_guard():
                    raise asyncio.CancelledError()

            assert breaker._probe_in_progress is False
        finally:
            # The breaker is a SHARED singleton: its tuning must be restored,
            # or a later test inherits a different threshold.
            breaker.failure_threshold, breaker.recovery_timeout = original
            breaker.reset()


class TestProbeLease:
    async def test_a_stale_claim_is_reclaimed(self) -> None:
        """A claim whose owner died must not block the next recovery window."""
        breaker = CircuitBreaker(name="lease-test", failure_threshold=1, recovery_timeout=0.05)
        await breaker.record_failure(RuntimeError("down"))
        breaker._last_failure_time = 0.0
        assert breaker.state == CircuitState.HALF_OPEN

        assert await breaker.allow_single_flight() is True  # claims the slot
        assert await breaker.allow_single_flight() is False  # the slot is taken

        await asyncio.sleep(0.06)  # the lease expires

        assert await breaker.allow_single_flight() is True  # reclaimed

    async def test_a_fresh_claim_is_not_reclaimed(self) -> None:
        breaker = CircuitBreaker(name="lease-test-2", failure_threshold=1, recovery_timeout=60.0)
        await breaker.record_failure(RuntimeError("down"))
        breaker._last_failure_time = 0.0

        assert await breaker.allow_single_flight() is True
        assert await breaker.allow_single_flight() is False


class TestProbeGate:
    async def test_it_admits_exactly_one_caller_in_half_open(self) -> None:
        """D7: `is_closed` is True in HALF_OPEN, so every caller passed."""
        breaker = await _half_open_breaker()
        admitted = 0

        async def caller() -> None:
            nonlocal admitted
            async with breaker.probe_gate() as allowed:
                if allowed:
                    admitted += 1
                    await asyncio.sleep(0.01)

        await asyncio.gather(caller(), caller(), caller())

        assert admitted == 1

    async def test_it_releases_the_slot_on_cancellation(self) -> None:
        """The release happens on every exit path, cancellation included."""
        breaker = await _half_open_breaker()

        with pytest.raises(asyncio.CancelledError):
            async with breaker.probe_gate() as allowed:
                assert allowed is True
                raise asyncio.CancelledError()

        assert breaker._probe_in_progress is False

    async def test_it_releases_the_slot_when_the_block_finishes(self) -> None:
        breaker = await _half_open_breaker()

        async with breaker.probe_gate() as allowed:
            assert allowed is True

        assert breaker._probe_in_progress is False

    async def test_a_closed_call_does_not_release_another_callers_probe(self) -> None:
        """Ownership comes from the claim, not from the state at exit."""
        breaker = CircuitBreaker(name="closed-gate", failure_threshold=1, recovery_timeout=60.0)
        assert breaker.state == CircuitState.CLOSED

        async with breaker.probe_gate() as allowed:
            assert allowed is True
            # The breaker opens and a DIFFERENT caller claims the probe while
            # this CLOSED-admitted block is still running.
            breaker._state = CircuitState.OPEN
            breaker._last_failure_time = 0.0
            assert breaker.state == CircuitState.HALF_OPEN
            assert await breaker.allow_single_flight() is True

        assert breaker._probe_in_progress is True

    async def test_it_admits_everything_while_closed(self) -> None:
        breaker = CircuitBreaker(name="closed-test", failure_threshold=1, recovery_timeout=60.0)

        async with breaker.probe_gate() as allowed:
            assert allowed is True
