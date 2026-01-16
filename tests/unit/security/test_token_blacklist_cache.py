"""Unit tests for token blacklist Redis cache.

Tests verify O(1) lookup via Redis sorted sets with proper
fail-open behavior and cache warming.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.app.core.security.token_blacklist_cache import (
    add_token_to_blacklist,
    add_tokens_to_blacklist,
    is_token_blacklisted,
    remove_expired_tokens,
    warm_cache_from_db,
)


class TestIsTokenBlacklisted:
    """Tests for is_token_blacklisted function."""

    @pytest.mark.asyncio
    async def test_returns_none_when_redis_unavailable(self) -> None:
        """Should fail open (return None) when Redis is not configured."""
        with patch("src.app.core.security.token_blacklist_cache.redis_client", None):
            result = await is_token_blacklisted("some-token")
            assert result is None

    @pytest.mark.asyncio
    async def test_returns_false_when_token_not_in_cache(self) -> None:
        """Should return False when token is not blacklisted."""
        mock_redis = AsyncMock()
        # Negative cache miss (token not recently verified)
        mock_redis.exists = AsyncMock(return_value=0)
        # Token not in blacklist
        mock_redis.zscore = AsyncMock(return_value=None)
        # Set negative cache for future lookups
        mock_redis.set = AsyncMock(return_value=True)

        with patch("src.app.core.security.token_blacklist_cache.redis_client", mock_redis):
            result = await is_token_blacklisted("clean-token")
            assert result is False
            # Should check negative cache first, then zscore
            mock_redis.exists.assert_called_once()
            mock_redis.zscore.assert_called_once()
            # Should set negative cache after confirming not blacklisted
            mock_redis.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_true_when_token_blacklisted(self) -> None:
        """Should return True when token is in blacklist and not expired."""
        mock_redis = AsyncMock()
        # Negative cache miss
        mock_redis.exists = AsyncMock(return_value=0)
        # Score is expiry timestamp in future
        future_expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
        mock_redis.zscore = AsyncMock(return_value=future_expiry)

        with patch("src.app.core.security.token_blacklist_cache.redis_client", mock_redis):
            result = await is_token_blacklisted("bad-token")
            assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_and_removes_expired_token(self) -> None:
        """Should return False and remove token if it has expired."""
        mock_redis = AsyncMock()
        # Negative cache miss
        mock_redis.exists = AsyncMock(return_value=0)
        # Score is expiry timestamp in past
        past_expiry = (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
        mock_redis.zscore = AsyncMock(return_value=past_expiry)
        mock_redis.zrem = AsyncMock(return_value=1)

        with patch("src.app.core.security.token_blacklist_cache.redis_client", mock_redis):
            result = await is_token_blacklisted("expired-token")
            assert result is False
            mock_redis.zrem.assert_called_once()

    @pytest.mark.asyncio
    async def test_fails_open_on_redis_error(self) -> None:
        """Should return None (fail open) on Redis errors."""
        mock_redis = AsyncMock()
        # Error on exists check
        mock_redis.exists = AsyncMock(side_effect=Exception("Connection error"))

        with patch("src.app.core.security.token_blacklist_cache.redis_client", mock_redis):
            result = await is_token_blacklisted("any-token")
            assert result is None


class TestAddTokenToBlacklist:
    """Tests for add_token_to_blacklist function."""

    @pytest.mark.asyncio
    async def test_adds_token_with_expiry_score(self) -> None:
        """Should add token to sorted set with expiry as score."""
        mock_redis = AsyncMock()
        mock_redis.zadd = AsyncMock(return_value=1)

        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

        with patch("src.app.core.security.token_blacklist_cache.redis_client", mock_redis):
            result = await add_token_to_blacklist("token123", expires_at)
            assert result is True
            mock_redis.zadd.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_false_when_redis_unavailable(self) -> None:
        """Should return False when Redis is not configured."""
        with patch("src.app.core.security.token_blacklist_cache.redis_client", None):
            result = await add_token_to_blacklist("token", datetime.now(timezone.utc))
            assert result is False


class TestBulkOperations:
    """Tests for bulk cache operations."""

    @pytest.mark.asyncio
    async def test_bulk_add_tokens(self) -> None:
        """Should add multiple tokens in one operation."""
        mock_redis = AsyncMock()
        mock_redis.zadd = AsyncMock(return_value=3)

        tokens = [
            ("token1", datetime.now(timezone.utc) + timedelta(hours=1)),
            ("token2", datetime.now(timezone.utc) + timedelta(hours=2)),
            ("token3", datetime.now(timezone.utc) + timedelta(hours=3)),
        ]

        with patch("src.app.core.security.token_blacklist_cache.redis_client", mock_redis):
            result = await add_tokens_to_blacklist(tokens)
            assert result == 3

    @pytest.mark.asyncio
    async def test_remove_expired_tokens(self) -> None:
        """Should remove all expired tokens from cache."""
        mock_redis = AsyncMock()
        mock_redis.zremrangebyscore = AsyncMock(return_value=5)

        with patch("src.app.core.security.token_blacklist_cache.redis_client", mock_redis):
            result = await remove_expired_tokens()
            assert result == 5


class TestCacheWarming:
    """Tests for cache warming from database."""

    @pytest.mark.asyncio
    async def test_warm_cache_filters_expired_tokens(self) -> None:
        """Should only add non-expired tokens to cache."""
        mock_redis = AsyncMock()
        mock_redis.zadd = AsyncMock(return_value=1)

        now = datetime.now(timezone.utc)
        tokens = [
            ("expired", now - timedelta(hours=1)),  # Should be filtered out
            ("active", now + timedelta(hours=1)),  # Should be added
        ]

        with patch("src.app.core.security.token_blacklist_cache.redis_client", mock_redis):
            result = await warm_cache_from_db(tokens)
            assert result == 1

    @pytest.mark.asyncio
    async def test_warm_cache_empty_list(self) -> None:
        """Should handle empty token list gracefully."""
        result = await warm_cache_from_db([])
        assert result == 0
