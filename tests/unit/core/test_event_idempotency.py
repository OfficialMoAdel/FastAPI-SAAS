"""
Unit tests for Event Bus Idempotency Fix (P1-1).

Tests the atomic Lua script idempotency check that fixes the race condition
in the original GET + SETEX implementation.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestEventBusIdempotency:
    """Tests for EventBus._check_idempotency with Lua script."""

    @pytest.fixture
    def mock_redis(self) -> AsyncMock:
        """Create mock Redis client."""
        redis = AsyncMock()
        redis.set = AsyncMock(return_value=True)  # True = key set = new event
        return redis

    @pytest.fixture
    def mock_lua_manager(self) -> MagicMock:
        """Create mock LuaScriptManager."""
        manager = MagicMock()
        manager.execute = AsyncMock(return_value=1)  # 1 = key was set (not duplicate)
        return manager

    @pytest.mark.asyncio
    async def test_lua_idempotency_new_event(self, mock_redis: AsyncMock, mock_lua_manager: MagicMock) -> None:
        """Test Lua script returns False for new events (not duplicate)."""
        from src.app.core.events import EventBus

        with patch("src.app.core.events.settings") as mock_settings:
            mock_settings.EVENT_BUS_USE_LUA_IDEMPOTENCY = True
            mock_settings.EVENT_BUS_CONCURRENCY = 10

            with patch("src.app.core.events.get_lua_manager", return_value=mock_lua_manager):
                bus = EventBus()
                bus._redis_client = mock_redis

                # Lua returns 1 = key was set (new event)
                mock_lua_manager.execute.return_value = 1

                result = await bus._check_idempotency("event-123")

                assert result is False  # Not a duplicate
                mock_lua_manager.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_lua_idempotency_duplicate_event(self, mock_redis: AsyncMock, mock_lua_manager: MagicMock) -> None:
        """Test Lua script returns True for duplicate events."""
        from src.app.core.events import EventBus

        with patch("src.app.core.events.settings") as mock_settings:
            mock_settings.EVENT_BUS_USE_LUA_IDEMPOTENCY = True
            mock_settings.EVENT_BUS_CONCURRENCY = 10

            with patch("src.app.core.events.get_lua_manager", return_value=mock_lua_manager):
                bus = EventBus()
                bus._redis_client = mock_redis

                # Lua returns 0 = key already exists (duplicate)
                mock_lua_manager.execute.return_value = 0

                result = await bus._check_idempotency("event-123")

                assert result is True  # Is a duplicate

    @pytest.mark.asyncio
    async def test_lua_fallback_on_error(self, mock_redis: AsyncMock, mock_lua_manager: MagicMock) -> None:
        """Test fallback to original implementation when Lua fails."""
        from src.app.core.events import EventBus

        with patch("src.app.core.events.settings") as mock_settings:
            mock_settings.EVENT_BUS_USE_LUA_IDEMPOTENCY = True
            mock_settings.EVENT_BUS_CONCURRENCY = 10

            with patch("src.app.core.events.get_lua_manager", return_value=mock_lua_manager):
                bus = EventBus()
                bus._redis_client = mock_redis

                # Lua script fails
                mock_lua_manager.execute.side_effect = Exception("Redis error")

                # Original implementation should be used
                mock_redis.set.return_value = False  # Duplicate

                result = await bus._check_idempotency("event-123")

                assert result is True
                mock_redis.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_feature_flag_disabled_uses_original(
        self, mock_redis: AsyncMock, mock_lua_manager: MagicMock
    ) -> None:
        """Test that disabled feature flag uses original implementation."""
        from src.app.core.events import EventBus

        with patch("src.app.core.events.settings") as mock_settings:
            mock_settings.EVENT_BUS_USE_LUA_IDEMPOTENCY = False
            mock_settings.EVENT_BUS_CONCURRENCY = 10

            with patch("src.app.core.events.get_lua_manager", return_value=mock_lua_manager):
                bus = EventBus()
                bus._redis_client = mock_redis

                result = await bus._check_idempotency("event-123")

                # Lua manager should not be called
                mock_lua_manager.execute.assert_not_called()

                # Original implementation should be used
                mock_redis.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_redis_client_returns_false(self) -> None:
        """Test that missing Redis client returns False (can't verify)."""
        from src.app.core.events import EventBus

        bus = EventBus()
        bus._redis_client = None

        result = await bus._check_idempotency("event-123")

        assert result is False

    @pytest.mark.asyncio
    async def test_concurrent_events_with_lua(self, mock_redis: AsyncMock, mock_lua_manager: MagicMock) -> None:
        """Test that concurrent identical events are properly deduplicated.

        This is the core race condition fix test. With the Lua script,
        only one of N concurrent identical events should be processed.
        """
        from src.app.core.events import EventBus

        with patch("src.app.core.events.settings") as mock_settings:
            mock_settings.EVENT_BUS_USE_LUA_IDEMPOTENCY = True
            mock_settings.EVENT_BUS_CONCURRENCY = 10

            call_count = 0

            async def mock_execute(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                # First call returns 1 (set), subsequent return 0 (exists)
                if call_count == 1:
                    return 1
                return 0

            mock_lua_manager.execute = mock_execute

            with patch("src.app.core.events.get_lua_manager", return_value=mock_lua_manager):
                bus = EventBus()
                bus._redis_client = mock_redis

                # Simulate 10 concurrent identical events
                results = await asyncio.gather(*[bus._check_idempotency("same-event-id") for _ in range(10)])

                # Only first should be "not duplicate" (False)
                # Rest should be "duplicate" (True)
                false_count = sum(1 for r in results if r is False)
                true_count = sum(1 for r in results if r is True)

                assert false_count == 1, f"Expected 1 non-duplicate, got {false_count}"
                assert true_count == 9, f"Expected 9 duplicates, got {true_count}"


class TestIdempotencyLuaScript:
    """Tests for the idempotency_check Lua script itself."""

    @pytest.mark.asyncio
    async def test_lua_script_registered(self) -> None:
        """Test that idempotency_check script is registered."""
        from src.app.core.utils.lua_manager import get_lua_manager, reset_lua_manager

        reset_lua_manager()
        manager = get_lua_manager()

        assert "idempotency_check" in manager.get_script_names()

        script = manager.get_script("idempotency_check")
        assert script.sha1 is not None
        assert len(script.sha1) == 40

    @pytest.mark.asyncio
    async def test_lua_script_source(self) -> None:
        """Test Lua script source contains expected operations."""
        from src.app.core.utils.lua_manager import get_lua_manager, reset_lua_manager

        reset_lua_manager()
        manager = get_lua_manager()

        script = manager.get_script("idempotency_check")

        # Verify script uses atomic SETNX
        assert "SETNX" in script.source or "setnx" in script.source.lower()
        assert "EXPIRE" in script.source or "expire" in script.source.lower()
