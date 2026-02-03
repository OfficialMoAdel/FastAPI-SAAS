import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from src.app.core.events import EventBus
from src.app.core.config import settings


@pytest.mark.asyncio
async def test_event_semaphore_concurrency():
    """Verify max concurrent handlers."""
    # We can't easily test exact concurrency count without a slow handler and time measurement
    # or inspecting the semaphore internals.

    # Mock settings to ensure known limit
    # Note: EVENT_BUS_CONCURRENCY is in the root settings or EventSettings mixin,
    # inherited by settings. It is NOT in r1_unified.
    with patch.object(settings, "EVENT_BUS_CONCURRENCY", 10):
        bus = EventBus()
        # Verify semaphore is initialized with correct limit
        assert bus._concurrency_semaphore._value == 10

    # Test release on exception
    bus = EventBus()
    # Force ARQ disabled to ensure sync path is used
    bus._arq_queue = None

    # Mock the semaphore with MagicMock and explicit AsyncMocks for context manager
    mock_sem = MagicMock()
    mock_enter = AsyncMock()
    mock_exit = AsyncMock()
    mock_sem.__aenter__ = mock_enter
    mock_sem.__aexit__ = mock_exit

    bus._concurrency_semaphore = mock_sem

    # Force publish_sync to be used (which uses semaphore)
    # We need a handler that raises exception
    handler = AsyncMock(side_effect=ValueError("Boom"))

    bus.subscribe("TestEvent")(handler)

    # First call fails (in background)
    try:
        await bus.publish("TestEvent", {"data": 1})
        await asyncio.sleep(0.1)
    except ValueError:
        pass

    # Verify semaphore was entered and exited
    mock_enter.assert_called()
    mock_exit.assert_called()


@pytest.mark.asyncio
async def test_event_semaphore_no_deadlock():
    """Verify semaphore releases even if handler fails."""
    bus = EventBus()
    # Use real semaphore
    bus._concurrency_semaphore = asyncio.Semaphore(1)

    async def failing_handler(event):
        raise ValueError("Fail")

    bus.subscribe("TestEvent")(failing_handler)

    # First call fails (in background)
    try:
        await bus.publish("TestEvent", {"data": 1})
        await asyncio.sleep(0.1)
    except ValueError:
        pass

    # Second call should succeed (acquire lock), proving it was released
    assert not bus._concurrency_semaphore.locked()
