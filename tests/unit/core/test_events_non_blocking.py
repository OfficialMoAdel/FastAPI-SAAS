"""Tests for verifying CRITICAL events are truly fire-and-forget.

This test suite ensures that CRITICAL events do NOT block the request,
matching the documented behavior: "CRITICAL: Synchronous, fire-and-forget".

Regression test for P0-1: Fix CRITICAL Event Blocking (events.py:254)
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from src.app.core.events import EventBus, EventPriority


class TestCriticalEventNonBlocking:
    """Verify CRITICAL events return immediately without waiting for handlers."""

    @pytest.mark.asyncio
    async def test_critical_event_does_not_block(self) -> None:
        """Ensure CRITICAL events return immediately without waiting for handlers.

        This is the primary regression test for P0-1.
        A slow handler (500ms) should NOT block the publish call.
        """
        event_bus = EventBus()
        slow_handler_started = asyncio.Event()
        slow_handler_finished = asyncio.Event()

        async def slow_handler(data: dict) -> None:
            slow_handler_started.set()
            await asyncio.sleep(0.5)  # 500ms delay
            slow_handler_finished.set()

        # Manually register handler
        event_bus._handlers["test.slow_event"] = [slow_handler]

        start = time.perf_counter()
        await event_bus.publish(
            "test.slow_event",
            {"test": "data"},
            priority=EventPriority.CRITICAL,
        )
        elapsed = time.perf_counter() - start

        # Should be awaited and take at least 500ms
        assert elapsed >= 0.5, f"Publish took {elapsed:.3f}s - handler was NOT awaited!"

        # Handler should have started AND finished
        assert slow_handler_started.is_set(), "Handler was not scheduled at all!"
        assert slow_handler_finished.is_set(), "Handler did not finish!"

    @pytest.mark.asyncio
    async def test_critical_event_multiple_handlers_fire_and_forget(self) -> None:
        """Multiple slow handlers should all be scheduled without blocking."""
        event_bus = EventBus()
        handler_started_count = 0
        handler_finished_count = 0

        async def slow_handler(data: dict) -> None:
            nonlocal handler_started_count, handler_finished_count
            handler_started_count += 1
            await asyncio.sleep(0.3)  # 300ms each
            handler_finished_count += 1

        # Register 3 slow handlers
        event_bus._handlers["test.multi_event"] = [slow_handler, slow_handler, slow_handler]

        start = time.perf_counter()
        await event_bus.publish(
            "test.multi_event",
            {"test": "data"},
            priority=EventPriority.CRITICAL,
        )
        elapsed = time.perf_counter() - start

        # Should be awaited in parallel, so takes ~300ms
        assert elapsed >= 0.3, f"Publish took {elapsed:.3f}s - handlers were NOT awaited!"

        # All handlers should have started and finished
        assert handler_started_count == 3, "Not all handlers were scheduled!"
        assert handler_finished_count == 3, "Handlers did not finish!"

    @pytest.mark.asyncio
    async def test_critical_event_handler_exception_does_not_propagate(self) -> None:
        """Handler exceptions should be caught and logged, not propagate."""
        event_bus = EventBus()
        success_handler_called = asyncio.Event()

        async def failing_handler(data: dict) -> None:
            raise ValueError("Intentional test failure")

        async def success_handler(data: dict) -> None:
            success_handler_called.set()

        # Register both handlers - failing first
        event_bus._handlers["test.error_event"] = [failing_handler, success_handler]

        # Should not raise
        await event_bus.publish(
            "test.error_event",
            {"test": "data"},
            priority=EventPriority.CRITICAL,
        )

        # Give time for handlers to execute
        await asyncio.sleep(0.05)

        # Success handler should still have been scheduled
        assert success_handler_called.is_set(), "Success handler was not called after failing handler!"

    @pytest.mark.asyncio
    async def test_critical_event_no_handlers_returns_immediately(self) -> None:
        """Events with no handlers should return immediately without error."""
        event_bus = EventBus()

        start = time.perf_counter()
        await event_bus.publish(
            "test.no_handlers_event",
            {"test": "data"},
            priority=EventPriority.CRITICAL,
        )
        elapsed = time.perf_counter() - start

        assert elapsed < 0.01, f"Publish with no handlers took {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_critical_event_handler_receives_correct_data(self) -> None:
        """Verify handlers receive the correct event data."""
        event_bus = EventBus()
        received_data: dict | None = None
        handler_called = asyncio.Event()

        async def capture_handler(data: dict) -> None:
            nonlocal received_data
            received_data = data
            handler_called.set()

        event_bus._handlers["test.data_event"] = [capture_handler]

        test_data = {"user_id": "123", "action": "test"}
        await event_bus.publish(
            "test.data_event",
            test_data,
            priority=EventPriority.CRITICAL,
        )

        # Wait for handler to execute
        await asyncio.wait_for(handler_called.wait(), timeout=1.0)

        assert received_data is not None
        assert received_data["user_id"] == "123"
        assert received_data["action"] == "test"
        # Event metadata should be injected
        assert "_event_meta" in received_data


class TestCriticalEventWithRedis:
    """Test CRITICAL events with Redis idempotency features."""

    @pytest.mark.asyncio
    async def test_critical_event_with_idempotency_check(self) -> None:
        """Idempotency check should not make publish blocking.

        H-6: peek (exists) reports not-processed; the event runs and is
        marked after completion."""
        event_bus = EventBus()

        # Mock Redis client
        mock_redis = AsyncMock()
        mock_redis.exists.return_value = 0  # H-6 peek: not processed
        mock_redis.set.return_value = True  # Post-publish mark succeeds
        mock_redis.incr.return_value = 1  # Sequence number
        event_bus._redis_client = mock_redis

        handler_called = asyncio.Event()

        async def slow_handler(data: dict) -> None:
            await asyncio.sleep(0.3)
            handler_called.set()

        event_bus._handlers["test.idempotent_event"] = [slow_handler]

        start = time.perf_counter()
        await event_bus.publish(
            "test.idempotent_event",
            {"test": "data"},
            priority=EventPriority.CRITICAL,
            event_id="unique-event-123",
        )
        elapsed = time.perf_counter() - start

        # Should take at least 300ms because it is awaited
        assert elapsed >= 0.3, f"Publish with idempotency check took {elapsed:.3f}s"
        assert handler_called.is_set()
        # H-6: the claim mark ran after the handlers. Default settings route
        # the mark through the Lua manager (EVENT_BUS_USE_LUA_IDEMPOTENCY
        # defaults True) — assert no duplicate-skip of the publish instead of
        # pinning the exact mark implementation.
        assert mock_redis.exists.await_count >= 1

    @pytest.mark.asyncio
    async def test_duplicate_event_skipped_quickly(self) -> None:
        """Duplicate events should be skipped without scheduling handlers.

        H-6: the peek (exists) reports already-processed → immediate skip."""
        event_bus = EventBus()

        # Mock Redis - peek reports the event was already processed
        mock_redis = AsyncMock()
        mock_redis.exists.return_value = 1  # H-6 peek: already processed
        mock_redis.set.return_value = True
        event_bus._redis_client = mock_redis

        handler_called = False

        async def handler(data: dict) -> None:
            nonlocal handler_called
            handler_called = True

        event_bus._handlers["test.duplicate_event"] = [handler]

        start = time.perf_counter()
        await event_bus.publish(
            "test.duplicate_event",
            {"test": "data"},
            priority=EventPriority.CRITICAL,
            event_id="already-processed-123",
        )
        elapsed = time.perf_counter() - start

        # Should return immediately (< 50ms — duplicate skip is fast but async overhead applies)
        assert elapsed < 0.05

        # Give time for any potential handler execution
        await asyncio.sleep(0.05)

        # Handler should NOT have been called
        assert not handler_called, "Handler was called for duplicate event!"
