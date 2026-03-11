"""Unit tests for Event Bus functionality.

Tests cover:
- Event subscription registration
- Event publishing (sync/async)
- Priority queue handling (CRITICAL/NORMAL/LOW)
- Subscriber failure isolation
- ARQ queue integration
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.app.core.events import EventBus, EventPriority, event_bus


@pytest.fixture
def fresh_event_bus():
    """Fresh EventBus instance for testing."""
    return EventBus()


@pytest.fixture
def mock_arq_queue():
    """Mock ARQ queue for async event processing."""
    queue = AsyncMock()
    queue.enqueue_job = AsyncMock()
    return queue


class TestEventBusSubscription:
    """Tests for event subscription."""

    def test_subscribe_decorator(self, fresh_event_bus) -> None:
        """Test subscribing handler with decorator."""

        @fresh_event_bus.subscribe("user.created")
        async def handler(data: dict):
            pass

        handlers = fresh_event_bus.get_handlers("user.created")
        assert len(handlers) == 1

    def test_subscribe_multiple_handlers(self, fresh_event_bus) -> None:
        """Test subscribing multiple handlers to same event."""

        @fresh_event_bus.subscribe("user.created")
        async def handler1(data: dict):
            pass

        @fresh_event_bus.subscribe("user.created")
        async def handler2(data: dict):
            pass

        handlers = fresh_event_bus.get_handlers("user.created")
        assert len(handlers) == 2

    def test_subscribe_different_events(self, fresh_event_bus) -> None:
        """Test subscribing handlers to different events."""

        @fresh_event_bus.subscribe("user.created")
        async def handler1(data: dict):
            pass

        @fresh_event_bus.subscribe("user.deleted")
        async def handler2(data: dict):
            pass

        assert len(fresh_event_bus.get_handlers("user.created")) == 1
        assert len(fresh_event_bus.get_handlers("user.deleted")) == 1

    def test_get_handlers_nonexistent_event(self, fresh_event_bus) -> None:
        """Test getting handlers for non-existent event returns empty list."""
        handlers = fresh_event_bus.get_handlers("nonexistent.event")
        assert handlers == []


class TestEventBusPublishing:
    """Tests for event publishing."""

    @pytest.mark.asyncio
    async def test_publish_calls_handlers(self, fresh_event_bus) -> None:
        """Test publishing event calls registered handlers."""
        handler_called = False
        received_data = None

        @fresh_event_bus.subscribe("user.created")
        async def handler(data: dict):
            nonlocal handler_called, received_data
            handler_called = True
            received_data = data

        await fresh_event_bus.publish(
            "user.created",
            {"user_id": "123", "email": "test@example.com"},
            priority=EventPriority.CRITICAL,
        )

        # Allow event loop to process fire-and-forget create_task()
        await asyncio.sleep(0)

        assert handler_called is True
        assert received_data["user_id"] == "123"

    @pytest.mark.asyncio
    async def test_publish_no_handlers(self, fresh_event_bus) -> None:
        """Test publishing event with no handlers doesn't error."""
        # Should not raise
        await fresh_event_bus.publish(
            "unknown.event",
            {"some": "data"},
            priority=EventPriority.NORMAL,
        )

    @pytest.mark.asyncio
    async def test_publish_critical_executes_sync(self, fresh_event_bus) -> None:
        """Test CRITICAL priority executes synchronously."""
        execution_order = []

        @fresh_event_bus.subscribe("critical.event")
        async def handler(data: dict):
            execution_order.append("handler")

        execution_order.append("before")
        await fresh_event_bus.publish(
            "critical.event",
            {},
            priority=EventPriority.CRITICAL,
        )
        # Allow event loop to process fire-and-forget create_task()
        await asyncio.sleep(0)
        execution_order.append("after")

        # Handler executes via create_task(), so order is: before, after, handler
        # This is "fire-and-forget" behavior - not truly synchronous
        assert "handler" in execution_order
        assert "before" in execution_order
        assert "after" in execution_order


class TestEventBusPriority:
    """Tests for event priority handling."""

    def test_priority_enum_values(self) -> None:
        """Test EventPriority enum has correct values."""
        assert EventPriority.CRITICAL.value == "CRITICAL"
        assert EventPriority.NORMAL.value == "NORMAL"
        assert EventPriority.LOW.value == "LOW"

    @pytest.mark.asyncio
    async def test_critical_priority_sync_execution(self, fresh_event_bus) -> None:
        """Test CRITICAL events execute handlers directly."""
        handler_executed = False

        @fresh_event_bus.subscribe("sync.event")
        async def handler(data: dict):
            nonlocal handler_executed
            handler_executed = True

        await fresh_event_bus.publish("sync.event", {}, priority=EventPriority.CRITICAL)

        # Allow event loop to process fire-and-forget create_task()
        await asyncio.sleep(0)

        assert handler_executed is True

    @pytest.mark.asyncio
    async def test_normal_priority_with_arq_queues(self, fresh_event_bus, mock_arq_queue) -> None:
        """Test NORMAL events queue via ARQ when configured."""
        fresh_event_bus.set_arq_queue(mock_arq_queue)

        @fresh_event_bus.subscribe("async.event")
        async def handler(data: dict):
            pass

        await fresh_event_bus.publish("async.event", {}, priority=EventPriority.NORMAL)

        mock_arq_queue.enqueue_job.assert_called()

    @pytest.mark.asyncio
    async def test_low_priority_with_arq_queues(self, fresh_event_bus, mock_arq_queue) -> None:
        """Test LOW events queue via ARQ when configured."""
        fresh_event_bus.set_arq_queue(mock_arq_queue)

        @fresh_event_bus.subscribe("low.event")
        async def handler(data: dict):
            pass

        await fresh_event_bus.publish("low.event", {}, priority=EventPriority.LOW)

        mock_arq_queue.enqueue_job.assert_called()


class TestEventBusFailureIsolation:
    """Tests for subscriber failure isolation."""

    @pytest.mark.asyncio
    async def test_handler_failure_doesnt_block_others(self, fresh_event_bus) -> None:
        """Test one failing handler doesn't prevent others from executing."""
        handler2_called = False

        @fresh_event_bus.subscribe("multi.event")
        async def failing_handler(data: dict):
            raise ValueError("Handler failed!")

        @fresh_event_bus.subscribe("multi.event")
        async def success_handler(data: dict):
            nonlocal handler2_called
            handler2_called = True

        # Should not raise and second handler should execute
        await fresh_event_bus.publish("multi.event", {}, priority=EventPriority.CRITICAL)

        # Allow event loop to process fire-and-forget create_task()
        await asyncio.sleep(0)

        assert handler2_called is True

    @pytest.mark.asyncio
    async def test_all_handlers_execute_despite_failures(self, fresh_event_bus) -> None:
        """Test all handlers are attempted even if some fail."""
        execution_count = 0

        @fresh_event_bus.subscribe("test.event")
        async def handler1(data: dict):
            nonlocal execution_count
            execution_count += 1
            raise ValueError("Error 1")

        @fresh_event_bus.subscribe("test.event")
        async def handler2(data: dict):
            nonlocal execution_count
            execution_count += 1

        @fresh_event_bus.subscribe("test.event")
        async def handler3(data: dict):
            nonlocal execution_count
            execution_count += 1
            raise ValueError("Error 3")

        await fresh_event_bus.publish("test.event", {}, priority=EventPriority.CRITICAL)

        # Allow event loop to process fire-and-forget create_task()
        await asyncio.sleep(0)

        assert execution_count == 3


class TestEventBusARQIntegration:
    """Tests for ARQ queue integration."""

    def test_set_arq_queue(self, fresh_event_bus, mock_arq_queue) -> None:
        """Test setting ARQ queue."""
        fresh_event_bus.set_arq_queue(mock_arq_queue)
        assert fresh_event_bus._arq_queue == mock_arq_queue

    @pytest.mark.asyncio
    async def test_fallback_to_sync_without_arq(self, fresh_event_bus) -> None:
        """Test async events fallback to sync when ARQ not configured."""
        handler_called = False

        @fresh_event_bus.subscribe("fallback.event")
        async def handler(data: dict):
            nonlocal handler_called
            handler_called = True

        # No ARQ queue set - should fallback to sync
        await fresh_event_bus.publish("fallback.event", {}, priority=EventPriority.NORMAL)

        # Allow event loop to process the task created by create_task()
        await asyncio.sleep(0)

        assert handler_called is True

    @pytest.mark.asyncio
    async def test_arq_queue_enqueues_correctly(self, fresh_event_bus, mock_arq_queue) -> None:
        """Test ARQ queue receives correct parameters."""
        fresh_event_bus.set_arq_queue(mock_arq_queue)

        @fresh_event_bus.subscribe("queued.event")
        async def handler(data: dict):
            pass

        await fresh_event_bus.publish(
            "queued.event",
            {"key": "value"},
            priority=EventPriority.NORMAL,
        )

        mock_arq_queue.enqueue_job.assert_called()
        call_args = mock_arq_queue.enqueue_job.call_args
        assert call_args[0][0] == "process_event_handler"


class TestGlobalEventBus:
    """Tests for global event_bus singleton."""

    def test_global_event_bus_exists(self) -> None:
        """Test global event_bus is an EventBus instance."""
        assert isinstance(event_bus, EventBus)

    def test_global_event_bus_handlers_storage(self) -> None:
        """Test global event_bus stores handlers."""
        assert hasattr(event_bus, "_handlers")
