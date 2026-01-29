from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.app.core.events import EventBus


@pytest.mark.asyncio
async def test_event_publish_uses_pipeline():
    """Verify that multiple handlers are enqueued using a Redis pipeline."""

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

    # Mock ARQ Queue and Redis
    mock_queue = Mock()
    mock_redis = AsyncMock()
    mock_pipeline = AsyncMock()

    # Configure mock chain: queue.redis.pipeline() -> mock_pipeline
    # redis.pipeline() is synchronous in redis-py, so we use Mock not AsyncMock for the method
    mock_redis.pipeline = Mock(return_value=mock_pipeline)
    mock_queue.redis = mock_redis

    # Set the queue on the bus
    bus._arq_queue = mock_queue

    # Publish event
    data = {"foo": "bar"}
    await bus.publish(event_name, data)

    # Verifications

    # 1. Pipeline should be created once
    mock_redis.pipeline.assert_called_once()

    # 2. rpush should be called 3 times on the pipeline (once per handler)
    assert mock_pipeline.rpush.call_count == 3

    # 3. execute should be called once on the pipeline
    mock_pipeline.execute.assert_awaited_once()

    # 4. Verify payloads (optional but good)
    # Check first call args
    args, _ = mock_pipeline.rpush.call_args_list[0]
    queue_name, payload = args
    assert "arq:queue" in queue_name
    assert b"process_event_handler" in payload
    assert b"handler1" in payload


@pytest.mark.asyncio
async def test_event_publish_fallback_on_pipeline_failure(caplog):
    """Verify fallback to sync execution if pipeline fails."""
    import logging

    caplog.set_level(logging.ERROR)

    bus = EventBus()

    async def handler1(data):
        pass

    bus.subscribe("test.fail")(handler1)

    mock_queue = Mock()
    mock_redis = AsyncMock()
    mock_pipeline = AsyncMock()

    # Make execute fail
    mock_pipeline.execute.side_effect = Exception("Redis went away")

    mock_redis.pipeline = Mock(return_value=mock_pipeline)
    mock_queue.redis = mock_redis
    bus._arq_queue = mock_queue

    # Mock _publish_sync to verify fallback
    with patch.object(bus, "_publish_sync", new_callable=AsyncMock) as mock_sync:
        await bus.publish("test.fail", {})

        # Verify sync fallback called
        mock_sync.assert_awaited_once()
