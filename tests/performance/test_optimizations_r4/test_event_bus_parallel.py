"""P1-4: Event Bus Parallel Enqueue Tests.

STATUS: VERIFIED ✅

This module tests the event bus parallel enqueue optimization using
asyncio.gather() instead of sequential handler execution.

Current Pattern (sequential):
    for handler in handlers:
        await self._arq_queue.enqueue_job(...)  # N RTTs total

Target Pattern (parallel):
    results = await asyncio.gather(
        *[enqueue_one(h) for h in handlers],
        return_exceptions=True
    )  # 1 RTT (parallel)
"""

from __future__ import annotations

import asyncio
import inspect
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.events import EventBus, EventPriority


class TestP14EventBusPrerequisites:
    """Pre-implementation tests for event bus."""

    def test_event_bus_exists(self) -> None:
        """Verify event bus infrastructure exists."""
        from src.app.core.events import EventBus

        assert EventBus is not None

    def test_event_handlers_registered(self) -> None:
        """Verify event handlers can be registered."""
        from src.app.core.events import EventBus

        bus = EventBus()
        # Basic API check
        assert hasattr(bus, "subscribe") or hasattr(bus, "register")


class TestP14ParallelEnqueueImplementation:
    """Tests verifying parallel enqueue is implemented correctly."""

    def test_publish_async_uses_asyncio_gather(self) -> None:
        """_publish_async should use asyncio.gather for parallel execution."""
        source = inspect.getsource(EventBus._publish_async)

        # Verify asyncio.gather is used
        assert "asyncio.gather" in source, "_publish_async should use asyncio.gather"
        assert "return_exceptions=True" in source, "Should handle exceptions gracefully"

    def test_publish_async_has_enqueue_handler_helper(self) -> None:
        """_publish_async should define enqueue_handler helper function."""
        source = inspect.getsource(EventBus._publish_async)

        # Verify helper function exists for parallel execution
        assert "enqueue_handler" in source, "Should have enqueue_handler helper"

    def test_publish_async_handles_fallback(self) -> None:
        """_publish_async should still have sync fallback for failures."""
        source = inspect.getsource(EventBus._publish_async)

        # Verify sync fallback exists
        assert "sync" in source.lower(), "Should have sync fallback"
        assert "handler(data)" in source, "Should call handler directly on failure"


class TestP14EventBusParallelEnqueue:
    """Tests to verify parallel enqueue after optimization."""

    @pytest.mark.asyncio
    async def test_parallel_enqueue_all_handlers(self) -> None:
        """All handlers should be enqueued in parallel via asyncio.gather."""
        bus = EventBus()

        # Create mock ARQ queue
        mock_queue = AsyncMock()
        mock_queue.enqueue_job = AsyncMock(return_value=None)
        bus._arq_queue = mock_queue

        # Create multiple handlers
        handler1 = AsyncMock(__name__="handler1", __module__="test")
        handler2 = AsyncMock(__name__="handler2", __module__="test")
        handler3 = AsyncMock(__name__="handler3", __module__="test")
        handlers = [handler1, handler2, handler3]

        # Mock circuit breaker as closed (healthy)
        with patch("src.app.core.events.arq_circuit") as mock_circuit:
            mock_circuit.is_closed = True
            mock_circuit.record_success = AsyncMock()

            await bus._publish_async(
                "test.event",
                {"key": "value"},
                handlers,
                EventPriority.NORMAL,
            )

        # Verify all handlers were enqueued
        assert mock_queue.enqueue_job.call_count == 3

    @pytest.mark.asyncio
    async def test_parallel_enqueue_handles_exceptions(self) -> None:
        """Exception in one handler should not prevent others."""
        bus = EventBus()

        # Create mock ARQ queue that fails on second call
        call_count = 0

        async def mock_enqueue(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise ConnectionError("Redis connection failed")
            return None

        mock_queue = MagicMock()
        mock_queue.enqueue_job = mock_enqueue
        bus._arq_queue = mock_queue

        # Create multiple handlers
        handler1 = AsyncMock(__name__="handler1", __module__="test")
        handler2 = AsyncMock(__name__="handler2", __module__="test")
        handler3 = AsyncMock(__name__="handler3", __module__="test")
        handlers = [handler1, handler2, handler3]

        # Mock circuit breaker
        with patch("src.app.core.events.arq_circuit") as mock_circuit:
            with patch("src.app.core.events.settings") as mock_settings:
                mock_settings.EVENT_BUS_USE_BATCH_ENQUEUE = False
                mock_circuit.is_closed = True
                mock_circuit.record_success = AsyncMock()
                mock_circuit.record_failure = AsyncMock()

                # Should not raise - exceptions handled gracefully
                await bus._publish_async(
                    "test.event",
                    {"key": "value"},
                    handlers,
                    EventPriority.NORMAL,
                )

        # All 3 were attempted (even though one failed)
        assert call_count == 3
        # Handler2 should have been called as sync fallback
        handler2.assert_called_once()

    @pytest.mark.asyncio
    async def test_parallel_enqueue_faster_than_sequential(self) -> None:
        """Parallel enqueue should be faster than sequential for multiple handlers."""
        bus = EventBus()

        # Create mock ARQ queue with simulated latency
        async def mock_enqueue_with_delay(*args, **kwargs):
            await asyncio.sleep(0.01)  # 10ms simulated RTT
            return None

        mock_queue = AsyncMock()
        mock_queue.enqueue_job = mock_enqueue_with_delay
        bus._arq_queue = mock_queue

        # Create 5 handlers
        handlers = [AsyncMock(__name__=f"handler{i}", __module__="test") for i in range(5)]

        with (
            patch("src.app.core.events.arq_circuit") as mock_circuit,
            patch("src.app.core.events.settings") as mock_settings,
        ):
            mock_circuit.is_closed = True
            mock_circuit.record_success = AsyncMock()
            mock_settings.EVENT_BUS_USE_BATCH_ENQUEUE = False

            start = time.perf_counter()
            await bus._publish_async(
                "test.event",
                {"key": "value"},
                handlers,
                EventPriority.NORMAL,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000

        # Sequential would take 5 * 10ms = 50ms
        # Parallel should take ~10ms (Max RTT instead of Sum RTT)
        # Allow CI jitter/Windows scheduler overhead, but keep strong bound.
        assert elapsed_ms < 40, f"Parallel enqueue took {elapsed_ms:.2f}ms, should be < 40ms"


class TestP14EventBusIsomorphism:
    """Isomorphism tests for event bus behavior."""

    @pytest.mark.asyncio
    async def test_all_handlers_invoked_on_circuit_open(self) -> None:
        """All handlers should still execute (sync) when circuit is open."""
        bus = EventBus()
        bus._arq_queue = MagicMock()  # Set queue but circuit is open

        # Create handlers
        handler1 = AsyncMock(__name__="handler1", __module__="test")
        handler2 = AsyncMock(__name__="handler2", __module__="test")
        handlers = [handler1, handler2]

        # Disable batch enqueue to test the parallel enqueue fallback path
        with (
            patch("src.app.core.events.arq_circuit") as mock_circuit,
            patch("src.app.core.events.settings") as mock_settings,
        ):
            mock_circuit.is_closed = False  # Circuit OPEN
            mock_settings.EVENT_BUS_USE_BATCH_ENQUEUE = False

            await bus._publish_async(
                "test.event",
                {"key": "value"},
                handlers,
                EventPriority.NORMAL,
            )

        # Both handlers should be called synchronously as fallback
        handler1.assert_called_once()
        handler2.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_arq_queue_falls_back_to_sync(self) -> None:
        """Without ARQ queue, should fall back to sync execution."""
        bus = EventBus()
        bus._arq_queue = None  # No queue

        handler = AsyncMock(__name__="handler", __module__="test")

        # Should call _publish_sync internally
        with patch.object(bus, "_publish_sync", new_callable=AsyncMock) as mock_sync:
            await bus._publish_async(
                "test.event",
                {"key": "value"},
                [handler],
                EventPriority.NORMAL,
            )

        mock_sync.assert_called_once()
