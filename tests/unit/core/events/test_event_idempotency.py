"""Unit tests for P1-1: Event Bus Idempotency Race Condition Fix."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.events import EventBus


@pytest.mark.asyncio
async def test_lua_script_executes_successfully(mock_redis_client, mock_settings):
    """Test that Lua script executes without errors."""
    event_bus = EventBus()
    event_bus.set_redis_client(mock_redis_client)

    # Register handler first so event is actually published
    async def handler(data):
        pass

    event_bus.subscribe("test.event")(handler)

    # Mock lua_manager to return success
    with patch("src.app.core.events.get_lua_manager") as mock_lua_mgr:
        mock_manager = MagicMock()
        mock_manager.execute = AsyncMock(return_value=1)  # 1 = new key set
        mock_lua_mgr.return_value = mock_manager

        with patch("src.app.core.events.settings", mock_settings):
            # Publish event with event_id to trigger idempotency check
            await event_bus.publish("test.event", {"data": "test"}, event_id="test-id-1")

        # Verify Lua script was executed
        mock_manager.execute.assert_called_once()
        call_args = mock_manager.execute.call_args
        assert call_args[1]["keys"][0] == "event_bus:processed:test-id-1"


@pytest.mark.asyncio
async def test_concurrent_identical_events_processed_once(mock_redis_client, mock_settings):
    """H-6: sequential re-publishes of a processed event are skipped.

    Concurrent first-flight duplicates may ALL enqueue (peek-before-claim):
    the claim now happens after successful publish so a crash between check
    and enqueue can never permanently drop the event (at-least-once).
    Concurrent collapse is the handler's idempotency responsibility, same
    as ARQ redelivery."""
    event_bus = EventBus()
    event_bus.set_redis_client(mock_redis_client)

    # First publish marks the event idempotent (exists → duplicate).
    mock_redis_client.exists = AsyncMock(side_effect=[0, 1, 1])

    processed_count = 0

    async def handler(data):
        nonlocal processed_count
        processed_count += 1

    with patch("src.app.core.events.settings", mock_settings):
        event_bus.subscribe("test.event")(handler)
        # First publish: peek says new → processed, then marked.
        await event_bus.publish("test.event", {"id": "same-id"}, event_id="same-event-id")
        # Re-publishes after completion: peek says duplicate → skipped.
        await event_bus.publish("test.event", {"id": "same-id"}, event_id="same-event-id")
        await event_bus.publish("test.event", {"id": "same-id"}, event_id="same-event-id")

    assert processed_count == 1
    assert mock_redis_client.exists.await_count == 3


@pytest.mark.asyncio
async def test_different_events_processed_independently(mock_redis_client, mock_settings):
    """Test that different events are processed independently."""
    event_bus = EventBus()
    event_bus.set_redis_client(mock_redis_client)

    # Mock Lua script to always return 1 (new key)
    with patch("src.app.core.events.get_lua_manager") as mock_lua_mgr:
        mock_manager = MagicMock()
        mock_manager.execute = AsyncMock(return_value=1)  # Always new
        mock_lua_mgr.return_value = mock_manager

        processed_events = []

        async def handler(data):
            processed_events.append(data["id"])

        with patch("src.app.core.events.settings", mock_settings):
            event_bus.subscribe("test.event")(handler)

            # Publish different events
            await asyncio.gather(
                *[event_bus.publish("test.event", {"id": f"event-{i}"}, event_id=f"event-{i}") for i in range(5)]
            )

    # Wait for background tasks to complete
    await asyncio.sleep(0.1)

    # All events should be processed
    assert len(processed_events) == 5
    assert set(processed_events) == {f"event-{i}" for i in range(5)}


@pytest.mark.asyncio
async def test_idempotency_key_has_correct_format(mock_redis_client, mock_settings):
    """Test that idempotency keys use correct prefix and format."""
    event_bus = EventBus()
    event_bus.set_redis_client(mock_redis_client)

    # Register handler first
    async def handler(data):
        pass

    event_bus.subscribe("test.event")(handler)

    with patch("src.app.core.events.get_lua_manager") as mock_lua_mgr:
        mock_manager = MagicMock()
        mock_manager.execute = AsyncMock(return_value=1)
        mock_lua_mgr.return_value = mock_manager

        with patch("src.app.core.events.settings", mock_settings):
            await event_bus.publish("test.event", {"data": "test"}, event_id="my-event-123")

        # Verify key format
        call_args = mock_manager.execute.call_args
        key = call_args[1]["keys"][0]
        assert key == "event_bus:processed:my-event-123"
        # Verify TTL is passed
        ttl = call_args[1]["args"][0]
        assert ttl == "86400"  # 24 hours


@pytest.mark.asyncio
async def test_lua_returns_0_for_duplicate(mock_redis_client, mock_settings):
    """H-6: a peek-reported duplicate is skipped without processing."""
    event_bus = EventBus()
    event_bus.set_redis_client(mock_redis_client)

    # Peek says the event was already processed.
    mock_redis_client.exists = AsyncMock(return_value=1)

    processed = False

    async def handler(data):
        nonlocal processed
        processed = True

    with patch("src.app.core.events.settings", mock_settings):
        event_bus.subscribe("test.event")(handler)
        await event_bus.publish("test.event", {"id": "duplicate-id"}, event_id="dup-123")

    # Event should not be processed (duplicate)
    assert not processed


@pytest.mark.asyncio
async def test_lua_returns_1_for_new_event(mock_redis_client, mock_settings):
    """Test that Lua script returns 1 for new events."""
    event_bus = EventBus()
    event_bus.set_redis_client(mock_redis_client)

    # Mock Lua script to return 1 (new)
    with patch("src.app.core.events.get_lua_manager") as mock_lua_mgr:
        mock_manager = MagicMock()
        mock_manager.execute = AsyncMock(return_value=1)  # New
        mock_lua_mgr.return_value = mock_manager

        processed = False

        async def handler(data):
            nonlocal processed
            processed = True

        with patch("src.app.core.events.settings", mock_settings):
            event_bus.subscribe("test.event")(handler)
            await event_bus.publish("test.event", {"id": "new-id"}, event_id="new-123")

    # Wait for background tasks to complete
    await asyncio.sleep(0.1)

    # Event should be processed (new)
    assert processed


@pytest.mark.asyncio
async def test_processed_event_repeated_under_load_is_skipped(mock_redis_client, mock_settings):
    """H-6: after an event is published and marked, high-volume re-publishes
    are all skipped (read-only peek). Concurrent in-flight duplicates may
    enqueue more than once — handler idempotency covers that, mirroring ARQ
    at-least-once redelivery. The old 100-publish test asserted check-time
    SETNX collapse, which H-6 deliberately traded away for crash recovery."""
    event_bus = EventBus()
    event_bus.set_redis_client(mock_redis_client)

    # First peek: new (0). After the first publish completes and marks the
    # key, every subsequent peek reports duplicate (1).
    mock_redis_client.exists = AsyncMock(side_effect=[0] + [1] * 100)

    handler_calls = 0

    async def handler(data):
        nonlocal handler_calls
        handler_calls += 1

    with patch("src.app.core.events.settings", mock_settings):
        event_bus.subscribe("test.event")(handler)
        await event_bus.publish("test.event", {"id": "same-id"}, event_id="race-test")
        # Flood of re-publishes after completion — all duplicates.
        await asyncio.gather(
            *[event_bus.publish("test.event", {"id": "same-id"}, event_id="race-test") for _ in range(100)]
        )

    assert handler_calls == 1


@pytest.mark.asyncio
async def test_lua_manager_called_for_idempotency(mock_redis_client, mock_settings):
    """Test that Lua manager is called for idempotency checks."""
    event_bus = EventBus()
    event_bus.set_redis_client(mock_redis_client)

    # Register handler
    async def handler(data):
        pass

    event_bus.subscribe("test.event")(handler)

    with patch("src.app.core.events.get_lua_manager") as mock_lua_mgr:
        mock_manager = MagicMock()
        mock_manager.execute = AsyncMock(return_value=1)
        mock_lua_mgr.return_value = mock_manager

        with patch("src.app.core.events.settings", mock_settings):
            # Publish multiple events with different IDs
            await event_bus.publish("test.event", {"id": "1"}, event_id="event-1")
            await event_bus.publish("test.event", {"id": "2"}, event_id="event-2")
            await event_bus.publish("test.event", {"id": "3"}, event_id="event-3")

        # get_lua_manager should be called (at least once due to singleton)
        assert mock_lua_mgr.call_count >= 1
        # execute should be called for each event
        assert mock_manager.execute.call_count == 3


@pytest.mark.asyncio
async def test_redis_failure_uses_fallback():
    """H-6: peek fails open; Lua mark failure falls back to SETNX; the
    event is still processed (availability over duplicate suppression)."""
    event_bus = EventBus()

    # Peek raises (Redis down pre-publish) — must fail open.
    mock_redis = AsyncMock()
    mock_redis.exists = AsyncMock(side_effect=Exception("peek failed"))
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.incr = AsyncMock(return_value=1)
    event_bus.set_redis_client(mock_redis)

    # Lua manager raises on the post-publish mark.
    with patch("src.app.core.events.get_lua_manager") as mock_lua_mgr:
        mock_manager = MagicMock()
        mock_manager.execute = AsyncMock(side_effect=Exception("Lua script failed"))
        mock_lua_mgr.return_value = mock_manager

        processed = False

        async def handler(data):
            nonlocal processed
            processed = True

        mock_settings = MagicMock()
        mock_settings.EVENT_BUS_USE_LUA_IDEMPOTENCY = True
        mock_settings.EVENT_HANDLER_TIMEOUT_SECONDS = 30

        with patch("src.app.core.events.settings", mock_settings):
            event_bus.subscribe("test.event")(handler)

            # Event should still be processed (peek fail-open + mark fallback)
            await event_bus.publish("test.event", {"id": "test-id"}, event_id="fallback-test")

    await asyncio.sleep(0.1)

    assert processed
    # Fallback mark should use set (nx=True)
    mock_redis.set.assert_called_once()


@pytest.mark.asyncio
async def test_feature_flag_disables_lua_optimization(mock_redis_client):
    """H-6: with the Lua flag off, the post-publish mark uses SETNX, and
    the Lua manager is never invoked."""
    event_bus = EventBus()

    mock_redis = AsyncMock()
    mock_redis.exists = AsyncMock(return_value=0)
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.incr = AsyncMock(return_value=1)
    event_bus.set_redis_client(mock_redis)

    async def handler(data):
        pass

    event_bus.subscribe("test.event")(handler)

    mock_settings = MagicMock()
    mock_settings.EVENT_BUS_USE_LUA_IDEMPOTENCY = False
    mock_settings.EVENT_HANDLER_TIMEOUT_SECONDS = 30

    with patch("src.app.core.events.get_lua_manager") as mock_lua_mgr:
        mock_manager = MagicMock()
        mock_manager.execute = AsyncMock(return_value=1)
        mock_lua_mgr.return_value = mock_manager

        with patch("src.app.core.events.settings", mock_settings):
            await event_bus.publish("test.event", {"data": "test"}, event_id="flag-test")

        mock_lua_mgr.assert_not_called()

    # Post-publish mark used the SETNX fallback
    mock_redis.set.assert_called_once()
