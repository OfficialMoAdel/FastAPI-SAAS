"""Unit tests for InvalidationChannel (Candidate 6).

Tests the unified cross-worker L1 cache invalidation channel:
- background listener start/stop lifecycle
- decoding string and bytes messages
- leak guard: guaranteed unsubscribe + close on stop/cancellation
- publisher helper
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.cache.invalidation_channel import InvalidationChannel


@pytest.mark.asyncio
async def test_invalidation_channel_publish():
    mock_redis = AsyncMock()
    channel = InvalidationChannel(channel="test:channel", on_message=MagicMock())

    with patch("src.app.core.cache.invalidation_channel.get_redis", return_value=mock_redis):
        await channel.publish("payload_123")
        mock_redis.publish.assert_awaited_once_with("test:channel", "payload_123")

        # Wildcard fallback when payload is None
        mock_redis.publish.reset_mock()
        await channel.publish(None)
        mock_redis.publish.assert_awaited_once_with("test:channel", "*")


@pytest.mark.asyncio
async def test_invalidation_channel_receives_and_decodes_messages():
    received = []

    def handle(msg: str):
        received.append(msg)

    channel = InvalidationChannel(channel="test:channel", on_message=handle)

    mock_pubsub = MagicMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.unsubscribe = AsyncMock()
    mock_pubsub.close = AsyncMock()

    # Async generator simulating pubsub messages
    async def mock_listen():
        yield {"type": "message", "data": "item_1"}
        yield {"type": "message", "data": b"item_2"}
        yield {"type": "other", "data": "ignored"}
        # Wait a bit before task termination
        await asyncio.sleep(0.01)

    mock_pubsub.listen = mock_listen

    mock_redis = MagicMock()
    mock_redis.pubsub.return_value = mock_pubsub

    with patch("src.app.core.cache.invalidation_channel.get_redis", return_value=mock_redis):
        channel.start()
        # Allow event loop to process messages
        await asyncio.sleep(0.05)
        await channel.stop()

    assert "item_1" in received
    assert "item_2" in received
    # Verified leak guard: unsubscribe and close called on exit
    mock_pubsub.unsubscribe.assert_awaited_once_with("test:channel")
    mock_pubsub.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalidation_channel_stop_idempotent():
    channel = InvalidationChannel(channel="test:channel", on_message=MagicMock())
    # Stopping without starting is a safe no-op
    await channel.stop()
    assert channel._task is None
