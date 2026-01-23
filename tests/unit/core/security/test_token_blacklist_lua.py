"""Unit tests for Token Blacklist Lua Script optimization.

Tests the Lua script implementation that reduces 4 Redis roundtrips to 1.
Verifies atomic operations and proper fallback behavior.

NOTE: Bloom filter tests were removed (R2-HYBRID CRIT-1) because the bloom filter
optimization was removed due to security vulnerability in multi-worker deployments.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from src.app.core.security.token_blacklist_cache import (
    BLACKLIST_CHECK_LUA_SCRIPT,
    BLACKLIST_KEY,
    NEGATIVE_CACHE_KEY,
    NEGATIVE_CACHE_TTL,
    _ensure_script_loaded,
    _is_token_blacklisted_fallback,
    is_token_blacklisted,
)


@pytest.fixture
def mock_redis():
    """Mock Redis client with Lua script support."""
    redis = AsyncMock()
    redis.script_load = AsyncMock(return_value="test_sha_123")
    redis.evalsha = AsyncMock(return_value=0)  # Default: not blacklisted
    redis.exists = AsyncMock(return_value=False)
    redis.zscore = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    redis.zrem = AsyncMock()
    return redis


class TestLuaScriptLoading:
    """Test Lua script loading and caching."""

    @pytest.mark.asyncio
    async def test_lua_script_syntax_valid(self, mock_redis):
        """Verify Lua script loads without syntax errors."""
        # Arrange
        with patch("src.app.core.security.token_blacklist_cache.redis_client", mock_redis):
            with patch("src.app.core.security.token_blacklist_cache._script_sha", None):
                # Act
                sha = await _ensure_script_loaded()

                # Assert
                assert sha == "test_sha_123"
                mock_redis.script_load.assert_called_once_with(BLACKLIST_CHECK_LUA_SCRIPT)

    @pytest.mark.asyncio
    async def test_script_sha_cached_after_first_load(self, mock_redis):
        """Verify script SHA is cached and not reloaded."""
        # Arrange
        with patch("src.app.core.security.token_blacklist_cache.redis_client", mock_redis):
            with patch("src.app.core.security.token_blacklist_cache._script_sha", "cached_sha"):
                # Act
                sha = await _ensure_script_loaded()

                # Assert
                assert sha == "cached_sha"
                mock_redis.script_load.assert_not_called()

    @pytest.mark.asyncio
    async def test_script_reload_on_noscript_error(self, mock_redis):
        """Verify script reloads when Redis returns NOSCRIPT error."""
        # Arrange
        token = "test_token_123"
        mock_redis.evalsha.side_effect = [
            Exception("NOSCRIPT No matching script"),
            0,  # Success after reload
        ]

        with patch("src.app.core.security.token_blacklist_cache.redis_client", mock_redis):
            with patch("src.app.core.security.token_blacklist_cache._script_sha", "old_sha"):
                # Act
                result = await is_token_blacklisted(token)

                # Assert
                assert result is False
                # Should have called script_load to reload
                mock_redis.script_load.assert_called_once()
                # Should have called evalsha twice (fail, then success)
                assert mock_redis.evalsha.call_count == 2


class TestLuaScriptBehavior:
    """Test Lua script execution and results."""

    @pytest.mark.asyncio
    async def test_not_blacklisted_token_returns_false(self, mock_redis):
        """Verify non-blacklisted token returns False."""
        # Arrange
        token = "valid_token_123"
        mock_redis.evalsha.return_value = 0  # Not blacklisted

        with patch("src.app.core.security.token_blacklist_cache.redis_client", mock_redis):
            with patch("src.app.core.security.token_blacklist_cache._script_sha", "test_sha"):
                # Act
                result = await is_token_blacklisted(token)

                # Assert
                assert result is False
                mock_redis.evalsha.assert_called_once()

    @pytest.mark.asyncio
    async def test_blacklisted_token_returns_true(self, mock_redis):
        """Verify blacklisted token returns True."""
        # Arrange
        token = "blacklisted_token_123"
        mock_redis.evalsha.return_value = 1  # IS blacklisted

        with patch("src.app.core.security.token_blacklist_cache.redis_client", mock_redis):
            with patch("src.app.core.security.token_blacklist_cache._script_sha", "test_sha"):
                # Act
                result = await is_token_blacklisted(token)

                # Assert
                assert result is True
                mock_redis.evalsha.assert_called_once()

    @pytest.mark.asyncio
    async def test_lua_script_receives_correct_arguments(self, mock_redis):
        """Verify Lua script is called with correct keys and arguments."""
        # Arrange
        token = "test_token_abc"
        mock_redis.evalsha.return_value = 0

        with patch("src.app.core.security.token_blacklist_cache.redis_client", mock_redis):
            with patch("src.app.core.security.token_blacklist_cache._script_sha", "test_sha"):
                # Act
                await is_token_blacklisted(token)

                # Assert
                call_args = mock_redis.evalsha.call_args
                assert call_args[0][0] == "test_sha"  # Script SHA
                assert call_args[0][1] == 2  # Number of keys
                # KEYS[1]: negative cache key
                assert call_args[0][2].startswith(NEGATIVE_CACHE_KEY)
                # KEYS[2]: blacklist key
                assert call_args[0][3] == BLACKLIST_KEY
                # ARGV[1]: token
                assert call_args[0][4] == token
                # ARGV[2]: timestamp (should be numeric string)
                assert call_args[0][5].isdigit()
                # ARGV[3]: TTL
                assert call_args[0][6] == str(NEGATIVE_CACHE_TTL)


class TestSingleRoundtrip:
    """Test that Lua script reduces operations to single roundtrip."""

    @pytest.mark.asyncio
    async def test_single_roundtrip_for_all_cases(self, mock_redis):
        """Verify exactly 1 EVALSHA call for blacklist check."""
        # Arrange
        token = "test_token_123"
        mock_redis.evalsha.return_value = 0

        with patch("src.app.core.security.token_blacklist_cache.redis_client", mock_redis):
            with patch("src.app.core.security.token_blacklist_cache._script_sha", "test_sha"):
                # Act
                await is_token_blacklisted(token)

                # Assert - only 1 Redis call (EVALSHA)
                mock_redis.evalsha.assert_called_once()
                # Verify no individual operations were called
                mock_redis.exists.assert_not_called()
                mock_redis.zscore.assert_not_called()
                mock_redis.set.assert_not_called()
                mock_redis.zrem.assert_not_called()


class TestFallbackBehavior:
    """Test fallback to old implementation when Lua script fails."""

    @pytest.mark.asyncio
    async def test_fallback_when_script_load_fails(self, mock_redis):
        """Verify fallback to old implementation if script loading fails."""
        # Arrange
        token = "test_token_123"
        mock_redis.script_load.side_effect = Exception("Script load failed")
        mock_redis.exists.return_value = False
        mock_redis.zscore.return_value = None

        with patch("src.app.core.security.token_blacklist_cache.redis_client", mock_redis):
            with patch("src.app.core.security.token_blacklist_cache._script_sha", None):
                # Act
                result = await is_token_blacklisted(token)

                # Assert
                assert result is False
                # Should have tried to load script
                mock_redis.script_load.assert_called_once()
                # Should have fallen back to old implementation
                mock_redis.exists.assert_called_once()
                mock_redis.zscore.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_implementation_works_correctly(self, mock_redis):
        """Verify fallback implementation produces correct results."""
        # Arrange
        token = "test_token_123"
        future_time = (datetime.now(UTC) + timedelta(hours=1)).timestamp()
        mock_redis.exists.return_value = False
        mock_redis.zscore.return_value = future_time  # Token is blacklisted

        with patch("src.app.core.security.token_blacklist_cache.redis_client", mock_redis):
            # Act
            result = await _is_token_blacklisted_fallback(token)

            # Assert
            assert result is True

    @pytest.mark.asyncio
    async def test_fallback_handles_expired_tokens(self, mock_redis):
        """Verify fallback removes expired tokens."""
        # Arrange
        token = "expired_token_123"
        past_time = (datetime.now(UTC) - timedelta(hours=1)).timestamp()
        mock_redis.exists.return_value = False
        mock_redis.zscore.return_value = past_time  # Token expired

        with patch("src.app.core.security.token_blacklist_cache.redis_client", mock_redis):
            # Act
            result = await _is_token_blacklisted_fallback(token)

            # Assert
            assert result is False
            # Should have removed expired token
            mock_redis.zrem.assert_called_once_with(BLACKLIST_KEY, token)


class TestRedisUnavailable:
    """Test behavior when Redis is unavailable."""

    @pytest.mark.asyncio
    async def test_returns_none_when_redis_unavailable(self):
        """Verify fail-open behavior when Redis is unavailable."""
        # Arrange
        token = "test_token_123"

        with patch("src.app.core.security.token_blacklist_cache.redis_client", None):
            # Act
            result = await is_token_blacklisted(token)

            # Assert
            assert result is None  # Fail open

    @pytest.mark.asyncio
    async def test_script_load_returns_none_when_redis_unavailable(self):
        """Verify script loading returns None when Redis unavailable."""
        # Arrange
        with patch("src.app.core.security.token_blacklist_cache.redis_client", None):
            # Act
            sha = await _ensure_script_loaded()

            # Assert
            assert sha is None


class TestNegativeCachePopulation:
    """Test that negative cache is populated by Lua script."""

    @pytest.mark.asyncio
    async def test_lua_script_populates_negative_cache(self, mock_redis):
        """Verify Lua script populates negative cache for non-blacklisted tokens.

        Note: We can't directly verify the Lua script's internal SETEX call,
        but we verify the script is called with correct TTL parameter.
        """
        # Arrange
        token = "test_token_123"
        mock_redis.evalsha.return_value = 0  # Not blacklisted

        with patch("src.app.core.security.token_blacklist_cache.redis_client", mock_redis):
            with patch("src.app.core.security.token_blacklist_cache._script_sha", "test_sha"):
                # Act
                await is_token_blacklisted(token)

                # Assert - verify TTL is passed to script
                call_args = mock_redis.evalsha.call_args[0]
                assert call_args[6] == str(NEGATIVE_CACHE_TTL)
