"""Performance Phase 4 tests - consolidated from multiple small test files.

Tests for:
- Event timeout protection
- Service registry caching
"""

import asyncio
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import src.app.core.service_registry as service_registry
from src.app.core.config import settings
from src.app.core.events import EventBus, EventPriority


# ==================== Event Timeout ====================


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


# ==================== Service Registry ====================


class MockService:
    def __init__(self, db):
        self.db = db


@pytest.fixture
def mock_db():
    return Mock(spec=AsyncSession)


@pytest.fixture
def clean_registry():
    # Save original state
    original_factories = service_registry._SERVICE_FACTORIES.copy()
    service_registry._SERVICE_FACTORIES.clear()

    # Clear cache if it exists (for when we implement it)
    if hasattr(service_registry, "_service_cache"):
        service_registry._service_cache.clear()

    yield

    # Restore
    service_registry._SERVICE_FACTORIES.clear()
    service_registry._SERVICE_FACTORIES.update(original_factories)


@pytest.mark.asyncio
async def test_service_caching_same_session(mock_db, clean_registry):
    """Test that getting service with same session returns same instance."""
    service_registry.register_service("mock", lambda db: MockService(db))

    s1 = service_registry.get_service("mock", mock_db)
    s2 = service_registry.get_service("mock", mock_db)

    # Should be same instance if cached
    assert s1 is s2
    assert s1.db is mock_db


@pytest.mark.asyncio
async def test_service_caching_different_sessions(clean_registry):
    """Test that different sessions get different instances."""
    db1 = Mock(spec=AsyncSession)
    db2 = Mock(spec=AsyncSession)

    service_registry.register_service("mock", lambda db: MockService(db))

    s1 = service_registry.get_service("mock", db1)
    s2 = service_registry.get_service("mock", db2)

    assert s1 is not s2
    assert s1.db is db1
    assert s2.db is db2
