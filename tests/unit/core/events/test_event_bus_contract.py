"""Contracts the event bus must keep: settings ownership, failure accounting,
and the degradation policy.

These pin the behaviours that were wrong before the Cluster A fixes:

- the event-bus knobs are read from the ``events`` settings component (the
  flat ``settings.EVENT_BUS_*`` form resolved only through the config layer's
  delegation ``__getattr__``, which no static analyser can see through);
- a malformed knob value falls back to the documented default instead of
  silently becoming a concurrency of 1 or an EMPTY retry schedule;
- one handler failure produces exactly ONE DLQ entry, and a failure that
  escapes ``except Exception`` is not reported as success.
"""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.app.core.events import EventBus, EventPriority
from src.app.core.events import bus as bus_module


@pytest.fixture
def fresh_event_bus() -> EventBus:
    """A bus with no Redis client and no ARQ queue."""
    return EventBus()


class TestSettingsOwnership:
    """The knobs live on `settings.events` and must be read from there."""

    def test_knobs_are_read_from_the_events_component(self) -> None:
        """A3: `settings.events.*` is the read path, not the flat delegation."""
        with patch("src.app.core.events.settings") as mock_settings:
            mock_settings.events.EVENT_BUS_CONCURRENCY = 7
            mock_settings.events.EVENT_BUS_RETRY_DELAYS = [1, 2, 3]

            bus = EventBus()

            assert bus.RETRY_DELAYS == [1, 2, 3]
            assert bus_module._concurrency_limit() == 7

    def test_malformed_concurrency_falls_back_to_the_default(self) -> None:
        """A non-int bound must not become a concurrency of 1.

        The bound is read once per instance, so a malformed value would
        serialise every CRITICAL handler behind the semaphore for the life of
        the process.
        """
        with patch("src.app.core.events.settings") as mock_settings:
            mock_settings.events.EVENT_BUS_CONCURRENCY = "not-a-number"

            assert bus_module._concurrency_limit() == bus_module.DEFAULT_EVENT_BUS_CONCURRENCY

    def test_empty_retry_schedule_falls_back_to_the_class_default(self) -> None:
        """The schedule is indexed by the retry counter.

        An empty schedule would raise IndexError on the first retry instead of
        backing off, so it falls back to the documented class default.
        """
        with patch("src.app.core.events.settings") as mock_settings:
            mock_settings.events.EVENT_BUS_RETRY_DELAYS = []

            bus = EventBus()

            assert bus.RETRY_DELAYS == EventBus.RETRY_DELAYS


class TestFailureAccounting:
    """One failure, one DLQ entry — and no failure reported as a success."""

    async def test_failing_handler_produces_exactly_one_dlq_entry(self, fresh_event_bus: EventBus) -> None:
        """A6/A7: the handler's own `except Exception` records the failure.

        The gather loop must not record it a second time — two DLQ entries
        for one failure, and the same handler on both of them.
        """
        fresh_event_bus.add_to_dlq = AsyncMock()

        @fresh_event_bus.subscribe("evt.once")
        async def boom(data: dict[str, Any]) -> None:
            raise ValueError("boom")

        await fresh_event_bus.publish("evt.once", {}, priority=EventPriority.CRITICAL)

        assert fresh_event_bus.add_to_dlq.await_count == 1
        assert fresh_event_bus.add_to_dlq.await_args.kwargs["error"] == "boom"

    async def test_base_exception_escape_is_not_reported_as_success(self, fresh_event_bus: EventBus) -> None:
        """A failure that escapes `except Exception` is still a failure.

        `asyncio.CancelledError` is a BaseException, so a cancelled handler
        escapes `_call_handler`'s `except Exception`. Reporting that publish as
        a success would mark the event idempotent and the handler would never
        run again — a silent loss.
        """

        class FatalHandlerError(BaseException):
            """The shape a cancelled handler has: not an Exception."""

        fresh_event_bus.add_to_dlq = AsyncMock()
        fresh_event_bus._mark_idempotent = AsyncMock()

        @fresh_event_bus.subscribe("evt.fatal")
        async def fatal(data: dict[str, Any]) -> None:
            raise FatalHandlerError("cancelled")

        await fresh_event_bus.publish(
            "evt.fatal",
            {},
            priority=EventPriority.CRITICAL,
            event_id="evt-fatal-1",
        )

        assert fresh_event_bus.add_to_dlq.await_count == 1
        assert "FatalHandlerError" in fresh_event_bus.add_to_dlq.await_args.kwargs["error"]
        fresh_event_bus._mark_idempotent.assert_not_awaited()


class TestDegradationPolicy:
    """When ARQ is unavailable the handlers run inline — and are not dropped."""

    async def test_no_arq_queue_runs_the_handlers_inline(self, fresh_event_bus: EventBus) -> None:
        """The documented degradation: inline execution, never a silent drop."""
        ran: list[str] = []

        @fresh_event_bus.subscribe("evt.degraded")
        async def handler(data: dict[str, Any]) -> None:
            ran.append("ran")

        await fresh_event_bus.publish("evt.degraded", {}, priority=EventPriority.NORMAL)

        assert ran == ["ran"]

    async def test_degraded_failure_is_dlq_routed(self, fresh_event_bus: EventBus) -> None:
        """A handler that fails during the degradation reaches the DLQ.

        The DLQ lives in Redis, which is why the bus degrades to inline
        execution instead of returning: DLQ-and-return would lose the event
        exactly when Redis is down.
        """
        fresh_event_bus.add_to_dlq = AsyncMock()

        @fresh_event_bus.subscribe("evt.degraded.boom")
        async def boom(data: dict[str, Any]) -> None:
            raise ValueError("boom")

        await fresh_event_bus.publish("evt.degraded.boom", {}, priority=EventPriority.NORMAL)

        assert fresh_event_bus.add_to_dlq.await_count == 1
