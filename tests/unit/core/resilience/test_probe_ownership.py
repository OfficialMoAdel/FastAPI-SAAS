"""A HALF_OPEN probe lease belongs to the caller that claimed it.

The probe slot is a LEASE: ``try_acquire_probe()`` mints a token, and only that
token may release it. Two defects motivated this suite:

* ``db_circuit_guard`` cleared the probe flag unconditionally on every
  non-infrastructure exit, so a call admitted while CLOSED that later raised a
  business error wiped the claim of the caller that actually owned the probe. A
  second probe was then admitted against a recovering database — the exact
  single-flight guarantee the mechanism exists to provide.
* ``record_failure`` refreshed ``_last_failure_time`` and only THEN read the
  computed ``state`` property. ``state`` reports HALF_OPEN from ``OPEN`` plus an
  elapsed recovery window, so the refresh made the HALF_OPEN arm unreachable for
  any ``recovery_timeout > 0``: a failed probe logged ``opened`` instead of
  ``reopened``, and a straggler from a call admitted before the circuit opened
  restarted the recovery window it was already waiting on.

The transition LOG events are asserted by patching this module's ``logger``
rather than via ``caplog``: this project configures structlog through a
``QueueListener`` on a background thread, so pytest's stdlib log capture sees
no records.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from src.app.core.utils.circuit_breaker import CircuitBreaker, CircuitState

_METRIC = "src.app.core.monitoring.r1_unified_metrics.record_circuit_breaker_transition"
_LOGGER = "src.app.core.utils.circuit_breaker.logger"


def _breaker(name: str, *, threshold: int = 1, recovery: float = 60.0) -> CircuitBreaker:
    return CircuitBreaker(name=name, failure_threshold=threshold, recovery_timeout=recovery)


async def _half_open(name: str, *, threshold: int = 1, recovery: float = 60.0) -> CircuitBreaker:
    """A breaker that is OPEN and whose recovery window has elapsed."""
    breaker = _breaker(name, threshold=threshold, recovery=recovery)
    await breaker.record_failure(RuntimeError("down"))
    breaker._last_failure_time = time.time() - recovery - 1
    assert breaker.state is CircuitState.HALF_OPEN
    return breaker


def _warning_events(log: object) -> list[str]:
    """The event names passed to ``logger.warning`` (first positional arg)."""
    return [call.args[0] for call in log.warning.call_args_list if call.args]  # type: ignore[attr-defined]


class TestReleaseOwnership:
    """Only the lease owner releases the slot."""

    async def test_a_mismatched_token_releases_nothing(self) -> None:
        breaker = _breaker("owner-mismatch")
        token = breaker.try_acquire_probe()
        assert token is not None

        breaker.release_probe(token + 1)

        assert breaker._probe_in_progress is True

    async def test_the_owning_token_releases(self) -> None:
        breaker = _breaker("owner-match")
        token = breaker.try_acquire_probe()
        assert token is not None

        breaker.release_probe(token)

        assert breaker._probe_in_progress is False

    async def test_the_unconditional_form_still_releases(self) -> None:
        """``reset()`` / admin paths keep the no-argument escape hatch."""
        breaker = _breaker("owner-unconditional")
        breaker.try_acquire_probe()

        breaker.release_probe()

        assert breaker._probe_in_progress is False

    async def test_tokens_are_not_reused_after_reset(self) -> None:
        """A stale holder must not match a lease minted after an admin reset."""
        breaker = _breaker("owner-aba")
        stale = breaker.try_acquire_probe()
        assert stale is not None

        breaker.reset()
        fresh = breaker.try_acquire_probe()
        assert fresh is not None
        assert fresh != stale

        breaker.release_probe(stale)

        assert breaker._probe_in_progress is True, "the stale token released a newer lease"

    async def test_record_failure_without_a_token_keeps_another_callers_lease(self) -> None:
        breaker = _breaker("owner-failure")
        breaker.try_acquire_probe()

        await breaker.record_failure(RuntimeError("business error"), None)

        assert breaker._probe_in_progress is True

    async def test_record_success_without_a_token_keeps_another_callers_lease(self) -> None:
        breaker = _breaker("owner-success")
        breaker.try_acquire_probe()

        await breaker.record_success(None)

        assert breaker._probe_in_progress is True

    async def test_record_success_with_the_owning_token_releases(self) -> None:
        breaker = _breaker("owner-success-match")
        token = breaker.try_acquire_probe()
        assert token is not None

        await breaker.record_success(token)

        assert breaker._probe_in_progress is False

    async def test_clear_probe_async_with_the_owning_token_releases(self) -> None:
        breaker = _breaker("owner-clear")
        token = breaker.try_acquire_probe()
        assert token is not None

        await breaker.clear_probe_async(token)

        assert breaker._probe_in_progress is False

    async def test_clear_probe_async_with_a_stale_token_releases_nothing(self) -> None:
        breaker = _breaker("owner-clear-stale")
        token = breaker.try_acquire_probe()
        assert token is not None

        await breaker.clear_probe_async(token + 1)

        assert breaker._probe_in_progress is True

    async def test_probe_gate_releases_its_own_lease(self) -> None:
        breaker = await _half_open("owner-gate")

        async with breaker.probe_gate() as allowed:
            assert allowed is True

        assert breaker._probe_in_progress is False

    async def test_a_closed_call_does_not_release_another_callers_lease(self) -> None:
        breaker = await _half_open("owner-gate-closed")
        # Another caller claims the probe.
        other = breaker.try_acquire_probe()
        assert other is not None

        # This caller is admitted while CLOSED, so it owns nothing.
        breaker._state = CircuitState.CLOSED
        async with breaker.probe_gate() as allowed:
            assert allowed is True
            breaker._state = CircuitState.OPEN
            breaker._last_failure_time = 0.0

        assert breaker._probe_in_progress is True


class TestDbCircuitGuardOwnership:
    """The DB breaker is the hot path; a stolen lease there is a real outage."""

    async def test_a_business_error_does_not_release_a_lease_it_did_not_claim(self) -> None:
        from src.app.core.resilience import db_circuit_breaker as module

        breaker = module.db_circuit
        original = (breaker.failure_threshold, breaker.recovery_timeout)
        try:
            breaker.reset()
            assert breaker.state is CircuitState.CLOSED
            # Another caller (admitted while HALF_OPEN) owns the probe.
            token = breaker.try_acquire_probe()
            assert token is not None

            with pytest.raises(ValueError):
                async with module.db_circuit_guard():
                    raise ValueError("business error, not infrastructure")

            assert breaker._probe_in_progress is True, "a CLOSED-admitted call stole the probe lease"
        finally:
            breaker.release_probe()
            breaker.failure_threshold, breaker.recovery_timeout = original
            breaker.reset()

    async def test_the_probe_owner_releases_its_own_lease_on_success(self) -> None:
        from src.app.core.resilience import db_circuit_breaker as module

        breaker = module.db_circuit
        original = (breaker.failure_threshold, breaker.recovery_timeout)
        try:
            breaker.reset()
            breaker.failure_threshold = 1
            breaker.recovery_timeout = 60.0
            await breaker.record_failure(RuntimeError("down"))
            breaker._last_failure_time = time.time() - 61
            assert breaker.state is CircuitState.HALF_OPEN

            async with module.db_circuit_guard():
                pass

            assert breaker._probe_in_progress is False
        finally:
            breaker.release_probe()
            breaker.failure_threshold, breaker.recovery_timeout = original
            breaker.reset()

    async def test_a_cancelled_probe_releases_its_own_lease(self) -> None:
        from src.app.core.resilience import db_circuit_breaker as module

        breaker = module.db_circuit
        original = (breaker.failure_threshold, breaker.recovery_timeout)
        try:
            breaker.reset()
            breaker.failure_threshold = 1
            breaker.recovery_timeout = 60.0
            await breaker.record_failure(RuntimeError("down"))
            breaker._last_failure_time = time.time() - 61
            assert breaker.state is CircuitState.HALF_OPEN

            with pytest.raises(asyncio.CancelledError):
                async with module.db_circuit_guard():
                    raise asyncio.CancelledError()

            assert breaker._probe_in_progress is False
        finally:
            breaker.release_probe()
            breaker.failure_threshold, breaker.recovery_timeout = original
            breaker.reset()


class TestFailureTransitions:
    """``record_failure`` must choose its transition from the PRIOR state."""

    async def test_an_open_straggler_does_not_extend_the_recovery_window(self) -> None:
        breaker = _breaker("straggler", threshold=1, recovery=60.0)
        await breaker.record_failure(RuntimeError("down"))
        opened_at = breaker.last_failure_time
        assert breaker.state is CircuitState.OPEN

        await asyncio.sleep(0.01)
        await breaker.record_failure(RuntimeError("straggler from an in-flight call"))

        assert breaker.last_failure_time == opened_at, (
            "a failure from a call admitted before the circuit opened restarted the recovery window"
        )
        assert breaker.state is CircuitState.OPEN

    async def test_an_open_straggler_emits_no_transition(self) -> None:
        breaker = _breaker("straggler-metric", threshold=1, recovery=60.0)
        await breaker.record_failure(RuntimeError("down"))  # CLOSED -> OPEN

        with patch(_METRIC) as metric, patch(_LOGGER) as log:
            await breaker.record_failure(RuntimeError("straggler"))

        metric.assert_not_called()
        log.warning.assert_not_called()

    async def test_a_failed_probe_logs_reopened_not_opened(self) -> None:
        breaker = await _half_open("reopen-log")

        with patch(_LOGGER) as log:
            await breaker.record_failure(RuntimeError("probe failed"))

        events = _warning_events(log)
        assert "circuit_breaker_reopened" in events
        assert "circuit_breaker_opened" not in events

    async def test_a_failed_probe_emits_exactly_one_transition(self) -> None:
        breaker = await _half_open("reopen-metric")

        with patch(_METRIC) as metric:
            await breaker.record_failure(RuntimeError("probe failed"))

        metric.assert_called_once_with("open")

    async def test_a_failed_probe_starts_a_fresh_recovery_window(self) -> None:
        breaker = await _half_open("reopen-window")

        before = breaker.last_failure_time
        await breaker.record_failure(RuntimeError("probe failed"))

        assert breaker.last_failure_time > before
        assert breaker._success_count == 0

    async def test_the_threshold_breach_logs_opened(self) -> None:
        """Positive control: the CLOSED arm still reports ``opened``."""
        breaker = _breaker("opened-log", threshold=1, recovery=60.0)

        with patch(_LOGGER) as log:
            await breaker.record_failure(RuntimeError("down"))

        events = _warning_events(log)
        assert "circuit_breaker_opened" in events
        assert "circuit_breaker_reopened" not in events

    async def test_a_closed_success_still_resets_the_failure_count(self) -> None:
        """W3 must survive the state-capture refactor."""
        breaker = _breaker("closed-success", threshold=5, recovery=60.0)
        await breaker.record_failure(RuntimeError("transient"))
        assert breaker.failure_count == 1

        await breaker.record_success()

        assert breaker.failure_count == 0
        assert breaker.state is CircuitState.CLOSED
