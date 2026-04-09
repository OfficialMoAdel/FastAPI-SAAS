"""Unit tests for token blacklist Redis cache.

Tests verify O(1) lookup via Redis sorted sets with proper
fail-open behavior and cache warming.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.security.token_blacklist_cache import (
    add_token_to_blacklist,
    add_tokens_to_blacklist,
    is_token_blacklisted,
    remove_expired_tokens,
    warm_cache_from_db,
)


def _acm(pipe):
    """Make pipeline mock support 'async with' by returning itself on __aenter__."""

    async def __aenter__(*a):
        return pipe

    async def __aexit__(*a):
        return False

    pipe.__aenter__ = __aenter__
    pipe.__aexit__ = __aexit__
    return pipe


class TestIsTokenBlacklisted:
    """Tests for is_token_blacklisted function."""

    @pytest.mark.asyncio
    async def test_returns_none_when_redis_unavailable(self) -> None:
        """Should fail open (return None) when Redis is not configured and fail_open mode set."""
        with (
            patch("src.app.core.utils.cache.client", None),
            patch("src.app.core.config.settings.auth.TOKEN_CACHE_FAILURE_MODE", "fail_open"),
        ):
            result = await is_token_blacklisted("some-token")
            assert result is None

    @pytest.mark.asyncio
    async def test_returns_false_when_token_not_in_cache(self) -> None:
        """Should return False when token is not blacklisted (Lua script returns 0)."""
        mock_redis = AsyncMock()
        # Mock evalsha to return 0 (not blacklisted)
        mock_redis.evalsha = AsyncMock(return_value=0)
        # Mock script loading
        mock_redis.script = MagicMock()
        mock_redis.script.return_value.load = AsyncMock(return_value="script_sha")

        with (
            patch("src.app.core.utils.cache.client", mock_redis),
            patch("src.app.core.security.token_blacklist_cache._script_sha", "cached_sha"),
        ):
            result = await is_token_blacklisted("clean-token")
            assert result is False
            # Should call evalsha with the Lua script
            mock_redis.evalsha.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_true_when_token_blacklisted(self) -> None:
        """Should return True when token is in blacklist and not expired."""
        mock_redis = AsyncMock()
        # Negative cache miss
        mock_redis.exists = AsyncMock(return_value=0)
        # Score is expiry timestamp in future
        future_expiry = (datetime.now(UTC) + timedelta(hours=1)).timestamp()
        mock_redis.zscore = AsyncMock(return_value=future_expiry)

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await is_token_blacklisted("bad-token")
            assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_and_removes_expired_token(self) -> None:
        """Should return False when Lua script returns 0 (handles expiry internally)."""
        mock_redis = AsyncMock()
        # Lua script handles expiry logic internally, returns 0 for expired/not-blacklisted
        mock_redis.evalsha = AsyncMock(return_value=0)

        with (
            patch("src.app.core.utils.cache.client", mock_redis),
            patch("src.app.core.security.token_blacklist_cache._script_sha", "cached_sha"),
        ):
            result = await is_token_blacklisted("expired-token")
            assert result is False
            mock_redis.evalsha.assert_called_once()

    @pytest.mark.asyncio
    async def test_fails_closed_on_redis_error(self) -> None:
        """Should return True (fail closed) on Redis errors."""
        mock_redis = AsyncMock()
        # Error on evalsha (Lua script execution)
        mock_redis.evalsha = AsyncMock(side_effect=Exception("Connection error"))

        with (
            patch("src.app.core.utils.cache.client", mock_redis),
            patch("src.app.core.security.token_blacklist_cache._script_sha", "cached_sha"),
        ):
            result = await is_token_blacklisted("any-token")
            assert result is True


class TestAddTokenToBlacklist:
    """Tests for add_token_to_blacklist function."""

    @pytest.mark.asyncio
    async def test_adds_token_with_expiry_score(self) -> None:
        """Should add token to sorted set with expiry as score using pipeline."""
        mock_pipe = MagicMock()
        _acm(mock_pipe)
        mock_pipe.zadd = MagicMock()
        mock_pipe.delete = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1, 1])

        mock_redis = MagicMock()
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        expires_at = datetime.now(UTC) + timedelta(hours=1)

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await add_token_to_blacklist("token123", expires_at)
            assert result is True
            mock_redis.pipeline.assert_called_once()
            mock_pipe.zadd.assert_called_once()
            mock_pipe.delete.assert_called_once()
            mock_pipe.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_false_when_redis_unavailable(self) -> None:
        """Should return False when Redis is not configured."""
        with patch("src.app.core.utils.cache.client", None):
            result = await add_token_to_blacklist("token", datetime.now(UTC))
            assert result is False


class TestBulkOperations:
    """Tests for bulk cache operations."""

    @pytest.mark.asyncio
    async def test_bulk_add_tokens(self) -> None:
        """Should add multiple tokens in one operation."""
        # Mock pipeline for bulk add
        mock_pipe = MagicMock()
        _acm(mock_pipe)
        mock_pipe.zadd = MagicMock()  # zadd is sync in pipeline mode
        mock_pipe.delete = MagicMock()  # delete is sync in pipeline mode
        mock_pipe.execute = AsyncMock(return_value=[3, 1, 1, 1])  # [zadd_result, delete1, delete2, delete3]

        mock_redis = MagicMock()
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        tokens = [
            ("token1", datetime.now(UTC) + timedelta(hours=1)),
            ("token2", datetime.now(UTC) + timedelta(hours=2)),
            ("token3", datetime.now(UTC) + timedelta(hours=3)),
        ]

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await add_tokens_to_blacklist(tokens)
            assert result == 3
            mock_redis.pipeline.assert_called_once()
            mock_pipe.zadd.assert_called_once()
            mock_pipe.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_expired_tokens(self) -> None:
        """Should remove all expired tokens from cache."""
        mock_redis = AsyncMock()
        mock_redis.zremrangebyscore = AsyncMock(return_value=5)

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await remove_expired_tokens()
            assert result == 5


class TestCacheWarming:
    """Tests for cache warming from database."""

    @pytest.mark.asyncio
    async def test_warm_cache_filters_expired_tokens(self) -> None:
        """Should only add non-expired tokens to cache."""
        # Mock pipeline for warm_cache
        mock_pipe = MagicMock()
        _acm(mock_pipe)
        mock_pipe.zadd = MagicMock()
        mock_pipe.delete = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1])  # Only 1 active token

        mock_redis = MagicMock()
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        now = datetime.now(UTC)
        tokens = [
            ("expired", now - timedelta(hours=1)),  # Should be filtered out
            ("active", now + timedelta(hours=1)),  # Should be added
        ]

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await warm_cache_from_db(tokens)
            assert result == 1
            mock_redis.pipeline.assert_called_once()

    @pytest.mark.asyncio
    async def test_warm_cache_empty_list(self) -> None:
        """Should handle empty token list gracefully."""
        result = await warm_cache_from_db([])
        assert result == 0
