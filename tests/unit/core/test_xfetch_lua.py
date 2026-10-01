"""
Unit tests for XFetch GET+TTL Lua Script Optimization (P2-2).

Tests the atomic Lua script that combines GET and TTL in a single Redis roundtrip,
reducing XFetch cache read latency by ~50%.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestXFetchLuaOptimization:
    """Tests for XFetch Lua script optimization."""

    @pytest.fixture
    def mock_redis(self) -> AsyncMock:
        """Create mock Redis client."""
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=b'{"name": "test"}')
        redis.ttl = AsyncMock(return_value=1800)  # 30 minutes remaining
        return redis

    @pytest.fixture
    def mock_lua_manager(self) -> MagicMock:
        """Create mock LuaScriptManager."""
        manager = MagicMock()
        # Return [value, ttl] tuple
        manager.execute = AsyncMock(return_value=[b'{"name": "test"}', 1800])
        return manager

    @pytest.mark.asyncio
    async def test_lua_path_returns_cached_value(self, mock_redis: AsyncMock, mock_lua_manager: MagicMock) -> None:
        """Test Lua path returns cached value correctly."""
        from src.app.core.utils import cache

        with patch("src.app.core.utils.cache.settings") as mock_settings:
            mock_settings.r1_unified.XFETCH_USE_LUA_SCRIPT = True

            with patch("src.app.core.utils.cache.get_lua_manager", return_value=mock_lua_manager):
                with patch.object(cache, "client", mock_redis):

                    async def fetch_fn():
                        return {"name": "fresh"}

                    result = await cache.get_with_stampede_protection(
                        "test:key",
                        fetch_fn,
                        ttl=3600,
                    )

                    assert result == {"name": "test"}
                    mock_lua_manager.execute.assert_called_once()
                    # Original get/ttl should NOT be called
                    mock_redis.get.assert_not_called()
                    mock_redis.ttl.assert_not_called()

    @pytest.mark.asyncio
    async def test_lua_path_single_roundtrip(self, mock_redis: AsyncMock, mock_lua_manager: MagicMock) -> None:
        """Test that Lua path makes only 1 Redis call."""
        from src.app.core.utils import cache

        with patch("src.app.core.utils.cache.settings") as mock_settings:
            mock_settings.r1_unified.XFETCH_USE_LUA_SCRIPT = True

            with patch("src.app.core.utils.cache.get_lua_manager", return_value=mock_lua_manager):
                with patch.object(cache, "client", mock_redis):

                    async def fetch_fn():
                        return {"name": "fresh"}

                    await cache.get_with_stampede_protection(
                        "test:key",
                        fetch_fn,
                        ttl=3600,
                    )

                    # Verify Lua script called exactly once
                    assert mock_lua_manager.execute.call_count == 1

                    # Verify it was called with correct script name
                    call_args = mock_lua_manager.execute.call_args
                    assert call_args[0][1] == "xfetch_get_with_ttl"

    @pytest.mark.asyncio
    async def test_lua_cache_miss_triggers_fetch(self, mock_redis: AsyncMock, mock_lua_manager: MagicMock) -> None:
        """Test that Lua cache miss triggers fetch function."""
        from src.app.core.utils import cache

        with patch("src.app.core.utils.cache.settings") as mock_settings:
            mock_settings.r1_unified.XFETCH_USE_LUA_SCRIPT = True

            # Return None (cache miss)
            mock_lua_manager.execute.return_value = [None, -2]

            with patch("src.app.core.utils.cache.get_lua_manager", return_value=mock_lua_manager):
                with patch.object(cache, "client", mock_redis):
                    fetch_called = False

                    async def fetch_fn():
                        nonlocal fetch_called
                        fetch_called = True
                        return {"name": "fresh"}

                    with patch.object(cache, "_refresh_cache", AsyncMock(return_value={"name": "fresh"})):
                        result = await cache.get_with_stampede_protection(
                            "test:key",
                            fetch_fn,
                            ttl=3600,
                        )

                        # Should return fresh data
                        assert result == {"name": "fresh"}

    @pytest.mark.asyncio
    async def test_lua_fallback_on_error(self, mock_redis: AsyncMock, mock_lua_manager: MagicMock) -> None:
        """Test fallback to original implementation when Lua fails."""
        from src.app.core.utils import cache

        with patch("src.app.core.utils.cache.settings") as mock_settings:
            mock_settings.r1_unified.XFETCH_USE_LUA_SCRIPT = True

            # Lua script fails
            mock_lua_manager.execute.side_effect = Exception("Redis error")

            with patch("src.app.core.utils.cache.get_lua_manager", return_value=mock_lua_manager):
                with patch.object(cache, "client", mock_redis):

                    async def fetch_fn():
                        return {"name": "fresh"}

                    result = await cache.get_with_stampede_protection(
                        "test:key",
                        fetch_fn,
                        ttl=3600,
                    )

                    # Should fall back to original implementation
                    mock_redis.get.assert_called_once()
                    mock_redis.ttl.assert_called_once()
                    assert result == {"name": "test"}

    @pytest.mark.asyncio
    async def test_feature_flag_disabled_uses_original(
        self, mock_redis: AsyncMock, mock_lua_manager: MagicMock
    ) -> None:
        """Test that disabled feature flag uses original GET+TTL."""
        from src.app.core.utils import cache

        with patch("src.app.core.utils.cache.settings") as mock_settings:
            mock_settings.r1_unified.XFETCH_USE_LUA_SCRIPT = False

            with patch("src.app.core.utils.cache.get_lua_manager", return_value=mock_lua_manager):
                with patch.object(cache, "client", mock_redis):

                    async def fetch_fn():
                        return {"name": "fresh"}

                    await cache.get_with_stampede_protection(
                        "test:key",
                        fetch_fn,
                        ttl=3600,
                    )

                    # Lua manager should NOT be called
                    mock_lua_manager.execute.assert_not_called()

                    # Original GET + TTL should be called
                    mock_redis.get.assert_called_once()
                    mock_redis.ttl.assert_called_once()

    @pytest.mark.asyncio
    async def test_bytes_to_string_conversion(self, mock_redis: AsyncMock, mock_lua_manager: MagicMock) -> None:
        """Test that bytes are properly converted to string."""
        from src.app.core.utils import cache

        with patch("src.app.core.utils.cache.settings") as mock_settings:
            mock_settings.r1_unified.XFETCH_USE_LUA_SCRIPT = True

            # Return bytes (as Redis typically does)
            mock_lua_manager.execute.return_value = [b'{"data": "value"}', 1800]

            with patch("src.app.core.utils.cache.get_lua_manager", return_value=mock_lua_manager):
                with patch.object(cache, "client", mock_redis):

                    async def fetch_fn():
                        return {"data": "fresh"}

                    result = await cache.get_with_stampede_protection(
                        "test:key",
                        fetch_fn,
                        ttl=3600,
                    )

                    # Should properly deserialize JSON
                    assert result == {"data": "value"}

    @pytest.mark.asyncio
    async def test_no_client_calls_fetch_directly(self) -> None:
        """Test that missing Redis client calls fetch directly."""
        from src.app.core.utils import cache

        with patch.object(cache, "client", None):
            fetch_called = False

            async def fetch_fn():
                nonlocal fetch_called
                fetch_called = True
                return {"name": "fresh"}

            result = await cache.get_with_stampede_protection(
                "test:key",
                fetch_fn,
                ttl=3600,
            )

            assert fetch_called
            assert result == {"name": "fresh"}


class TestXFetchLuaScript:
    """Tests for the xfetch_get_with_ttl Lua script."""

    def test_lua_script_registered(self) -> None:
        """Test that xfetch_get_with_ttl script is registered."""
        from src.app.core.utils.lua_manager import get_lua_manager, reset_lua_manager

        reset_lua_manager()
        manager = get_lua_manager()

        assert "xfetch_get_with_ttl" in manager.get_script_names()

    def test_lua_script_source_correct(self) -> None:
        """Test Lua script source contains expected operations."""
        from src.app.core.utils.lua_manager import get_lua_manager, reset_lua_manager

        reset_lua_manager()
        manager = get_lua_manager()

        script = manager.get_script("xfetch_get_with_ttl")

        # Verify script uses GET and TTL
        source_lower = script.source.lower()
        assert "get" in source_lower
        assert "ttl" in source_lower

        # Verify it returns an array
        assert "return" in source_lower

    def test_lua_script_has_valid_sha1(self) -> None:
        """Test Lua script has valid SHA1 hash."""
        from src.app.core.utils.lua_manager import get_lua_manager, reset_lua_manager

        reset_lua_manager()
        manager = get_lua_manager()

        script = manager.get_script("xfetch_get_with_ttl")

        assert script.sha1 is not None
        assert len(script.sha1) == 40


class TestXFetchProbabilisticRefresh:
    """Tests for XFetch probabilistic refresh logic."""

    @pytest.fixture
    def mock_redis(self) -> AsyncMock:
        """Create mock Redis client."""
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=b'{"name": "test"}')
        redis.setex = AsyncMock()
        return redis

    @pytest.mark.asyncio
    async def test_early_refresh_probability_near_expiration(self, mock_redis: AsyncMock) -> None:
        """Test that probability is higher when TTL is low."""
        from src.app.core.utils import cache

        with patch("src.app.core.utils.cache.settings") as mock_settings:
            mock_settings.r1_unified.XFETCH_USE_LUA_SCRIPT = False

            with patch.object(cache, "client", mock_redis):
                # TTL = 60 seconds remaining out of 3600 (near expiration)
                mock_redis.ttl.return_value = 60

                async def fetch_fn():
                    return {"name": "fresh"}

                with patch("src.app.core.utils.cache.asyncio.create_task") as mock_task:
                    with patch("src.app.core.utils.cache.random.random", return_value=0.0001):
                        # Very low random value should trigger refresh
                        await cache.get_with_stampede_protection(
                            "test:key",
                            fetch_fn,
                            ttl=3600,
                            beta=1.0,
                        )

                        # Should trigger background refresh
                        mock_task.assert_called()

    @pytest.mark.asyncio
    async def test_no_early_refresh_when_fresh(self, mock_redis: AsyncMock) -> None:
        """Test that no early refresh when TTL is high."""
        from src.app.core.utils import cache

        with patch("src.app.core.utils.cache.settings") as mock_settings:
            mock_settings.r1_unified.XFETCH_USE_LUA_SCRIPT = False

            with patch.object(cache, "client", mock_redis):
                # TTL = 3500 seconds remaining out of 3600 (very fresh)
                mock_redis.ttl.return_value = 3500

                async def fetch_fn():
                    return {"name": "fresh"}

                with patch("src.app.core.utils.cache.asyncio.create_task") as mock_task:
                    with patch("src.app.core.utils.cache.random.random", return_value=0.5):
                        await cache.get_with_stampede_protection(
                            "test:key",
                            fetch_fn,
                            ttl=3600,
                            beta=1.0,
                        )

                        # Should NOT trigger background refresh (probability too low)
                        mock_task.assert_not_called()
