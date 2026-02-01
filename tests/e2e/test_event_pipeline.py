from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.app.core.events import EventBus


@pytest.mark.asyncio
async def test_event_publish_uses_parallel_enqueue():
    """Verify that multiple handlers are enqueued in parallel via asyncio.gather."""

    # Setup Event Bus
    bus = EventBus()

    # Create dummy handlers
    async def handler1(data):
        pass

    async def handler2(data):
        pass

    async def handler3(data):
        pass

    event_name = "test.pipeline.event"
    bus.subscribe(event_name)(handler1)
    bus.subscribe(event_name)(handler2)
    bus.subscribe(event_name)(handler3)

    # Mock ARQ Queue
    mock_queue = AsyncMock()

    # Set the queue on the bus
    bus._arq_queue = mock_queue

    # Publish event
    data = {"foo": "bar"}
    await bus.publish(event_name, data)

    # Verifications

    # 1. enqueue_job should be called 3 times (once per handler)
    assert mock_queue.enqueue_job.call_count == 3

    # 2. Verify each call is for "process_event_handler"
    for call in mock_queue.enqueue_job.call_args_list:
        args, kwargs = call
        assert args[0] == "process_event_handler"
        assert args[1] == event_name

    # 3. Verify handler names are in the calls
    handler_names = [call[0][3] for call in mock_queue.enqueue_job.call_args_list]
    assert "handler1" in handler_names
    assert "handler2" in handler_names
    assert "handler3" in handler_names


@pytest.mark.asyncio
async def test_event_publish_fallback_on_queue_failure(caplog):
    """Verify fallback to sync execution if ARQ enqueue fails."""
    import logging

    caplog.set_level(logging.ERROR)

    bus = EventBus()

    handler_called = False

    async def handler1(data):
        nonlocal handler_called
        handler_called = True

    bus.subscribe("test.fail")(handler1)

    mock_queue = AsyncMock()
    # Make enqueue_job fail
    mock_queue.enqueue_job.side_effect = Exception("ARQ connection failed")

    bus._arq_queue = mock_queue

    # Should not raise, but should log the error and fall back to sync
    await bus.publish("test.fail", {})

    # Handler should have been executed synchronously as fallback
    assert handler_called, "Handler should have been executed synchronously as fallback"
