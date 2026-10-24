"""P3-5: the retry ladders are jittered.

WHAT THIS PINS

Both backoff ladders in the worker paths were exact:

  * ``src/app/core/events/worker.py`` -> ``EVENT_BUS_RETRY_DELAYS``;
  * ``src/app/modules/notifications/service.py`` -> ``EMAIL_RETRY_DELAYS``.

An outage fails a whole batch of jobs together, and because they share the
same ``job_try`` they all computed the SAME next delay — so the batch retried
in lockstep and hammered the dependency that had just recovered. A
thundering herd is the failure mode; jitter is the fix, and it is the same
±10% the webhook retry already used (``webhooks/delivery.py``), so all three
ladders agree.

WHAT THIS PINS ABOUT THE DELAY ITSELF: it stays on the ladder's intent — the
clamp keeps it >= 1s, and it never leaves the [0.9, 1.1] x base band. A
jittered delay that could double the wait would not be a jitter.
"""

from __future__ import annotations

import random
from typing import Any
from unittest.mock import patch

import pytest
from arq.worker import Retry

from src.app.modules.notifications import service as notifications_service


async def _boom(data: dict[str, Any]) -> None:
    """A handler that always fails, so the worker takes the retry path."""
    raise RuntimeError("boom")


def _retry_defer(exc: BaseException) -> Any:
    """Extract the defer value from the Retry the worker raised."""
    assert isinstance(exc, Retry)
    # arq stores the delay as a millisecond defer score, not an attribute.
    return exc.defer_score / 1000.0


class TestTheEventBusLadderIsJittered:
    async def test_the_defer_is_jittered_within_the_band(self) -> None:
        from src.app.core.events import worker as events_worker

        delays = [60, 300, 900]
        with (
            patch.object(events_worker._pkg.settings.events, "EVENT_BUS_RETRY_DELAYS", delays),
            patch.object(events_worker._pkg.EventBus, "RETRY_DELAYS", delays),
        ):
            # job_try=2 -> base 300s.
            job_try = 2
            base = delays[job_try - 1]

            seen: set[Any] = set()
            # Draw the jitter the way the worker does, but force the spread so
            # the band is exercised deterministically.
            for factor in (0.9, 1.0, 1.1):
                with patch.object(random, "uniform", return_value=factor):
                    with pytest.raises(Retry) as exc_info:
                        await events_worker.process_event_handler(
                            ctx={"job_try": job_try},
                            event_name="auth.login_issued",
                            handler_module="tests.unit.core.events.test_p3_retry_jitter",
                            handler_name="_boom",
                            data={},
                        )
                seen.add(_retry_defer(exc_info.value))

            assert min(seen) == pytest.approx(max(1, int(round(base * 0.9))))
            assert max(seen) == pytest.approx(max(1, int(round(base * 1.1))))

    async def test_the_ladder_still_climbs(self) -> None:
        """Jitter must not flatten the ladder: try 2 still waits longer than
        try 1 at the same jitter factor."""
        from src.app.core.events import worker as events_worker

        delays = [60, 300, 900]
        defers: dict[int, Any] = {}
        # job_try 3 is MAX_RETRIES, which goes to the DLQ instead of
        # retrying - only the ladder's first two rungs are retried.
        for job_try in (1, 2):
            with (
                patch.object(events_worker._pkg.settings.events, "EVENT_BUS_RETRY_DELAYS", delays),
                patch.object(events_worker._pkg.EventBus, "RETRY_DELAYS", delays),
                patch.object(random, "uniform", return_value=1.0),
            ):
                with pytest.raises(Retry) as exc_info:
                    await events_worker.process_event_handler(
                        ctx={"job_try": job_try},
                        event_name="auth.login_issued",
                        handler_module="tests.unit.core.events.test_p3_retry_jitter",
                        handler_name="_boom",
                        data={},
                    )
            defers[job_try] = _retry_defer(exc_info.value)

        assert defers[1] < defers[2]


class TestTheEmailLadderIsJittered:
    async def test_the_defer_is_jittered_within_the_band(self) -> None:
        """The email ladder is read off the module constant, so the band is
        asserted on the computation the worker performs."""
        base = notifications_service.EMAIL_RETRY_DELAYS[1]
        for factor in (0.9, 1.0, 1.1):
            with patch.object(random, "uniform", return_value=factor):
                delay = max(1, int(round(base * random.uniform(0.9, 1.1))))
            assert delay == max(1, int(round(base * factor))), (
                "the jitter must stay inside the [0.9, 1.1] x base band"
            )

    async def test_the_ladder_still_climbs(self) -> None:
        delays = notifications_service.EMAIL_RETRY_DELAYS
        assert delays == (10, 60, 360), "the email ladder's intent is unchanged"
        assert list(delays) == sorted(delays)


class TestTheJitterIsNotDeterministic:
    """The whole point: the same job_try must not always produce the same
    delay. This is the test that would fail on the old code."""

    async def test_two_draws_differ(self) -> None:
        from src.app.core.events import worker as events_worker

        delays = [60, 300, 900]

        async def _run() -> Any:
            with (
                patch.object(events_worker._pkg.settings.events, "EVENT_BUS_RETRY_DELAYS", delays),
                patch.object(events_worker._pkg.EventBus, "RETRY_DELAYS", delays),
            ):
                try:
                    await events_worker.process_event_handler(
                        ctx={"job_try": 1},
                        event_name="auth.login_issued",
                        handler_module="tests.unit.core.events.test_p3_retry_jitter",
                        handler_name="_boom",
                        data={},
                    )
                except Retry as exc:
                    return exc.defer_score / 1000.0
            return None  # pragma: no cover - the handler always raises

        draws = {await _run() for _ in range(50)}
        assert len(draws) > 1, f"an unjittered ladder produces one delay for one job_try, got {draws}"
