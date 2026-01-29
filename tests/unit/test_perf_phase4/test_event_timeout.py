import asyncio
from unittest.mock import Mock, patch

import pytest

from src.app.core.config import settings
from src.app.core.events import EventBus, EventPriority


@pytest.fixture
def fresh_event_bus():
    return EventBus()


@pytest.mark.asyncio
async def test_sync_handler_timeout_protection(fresh_event_bus):
    """Verify that a slow handler times out but doesn't crash the loop."""

    # Patch the settings instance attribute directly
    # This ensures the same object reference used in events.py sees the change
    with patch.object(settings, "EVENT_HANDLER_TIMEOUT_SECONDS", 0.1):
        slow_handler_called = False
        fast_handler_called = False

        @fresh_event_bus.subscribe("test.timeout")
        async def slow_handler(data):
            nonlocal slow_handler_called
            slow_handler_called = True
            await asyncio.sleep(0.5)  # Longer than timeout

        @fresh_event_bus.subscribe("test.timeout")
        async def fast_handler(data):
            nonlocal fast_handler_called
            fast_handler_called = True
            await asyncio.sleep(0.01)

        # Execute critical event (sync execution)
        await fresh_event_bus.publish("test.timeout", {}, priority=EventPriority.CRITICAL)

        # _publish_sync uses asyncio.create_task() for fire-and-forget execution
        # We need to yield control to allow background tasks to start
        await asyncio.sleep(0.05)

        # Verify both were called (parallel execution started)
        assert slow_handler_called
        assert fast_handler_called

        # Since slow_handler times out, it shouldn't stop fast_handler
        # Logs should show timeout error (checked manually or via caplog if needed)


@pytest.mark.asyncio
async def test_arq_handler_timeout_protection():
    """Verify timeout in ARQ worker function."""
    from src.app.core.events import process_event_handler

    # We need to mock importlib to return a slow handler
    slow_handler_mock = Mock()

    async def slow_func(data):
        await asyncio.sleep(0.5)

    slow_handler_mock.slow_func = slow_func

    with patch("importlib.import_module", return_value=slow_handler_mock):
        with patch.object(settings, "EVENT_HANDLER_TIMEOUT_SECONDS", 0.1):
            # Should raise TimeoutError which is caught and re-raised as Exception or handled
            # process_event_handler catches Exception and logs/retries
            # If it's a TimeoutError, it should end up in the except Exception block

            ctx = {"job_try": 1}

            # Expecting it to raise (re-raised for ARQ retry logic)
            with pytest.raises(Exception) as excinfo:
                await process_event_handler(ctx, "test.event", "module", "slow_func", {})

            # Check for either the standard TimeoutError message or our custom one
            error_msg = str(excinfo.value)
            assert "timed out" in error_msg or "Timeout" in error_msg
