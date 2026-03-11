from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.utils.rate_limit import RateLimiter


class TestRateLimiter:
    """Tests for RateLimiter utility."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset RateLimiter singleton before and after each test."""
        RateLimiter._instance = None
        yield
        RateLimiter._instance = None

    @pytest.mark.asyncio
    async def test_check_rate_limit_uses_lua_script(self):
        """Test that check_rate_limit uses evalsha (Lua script) for atomicity."""
        mock_redis = AsyncMock()
        mock_redis.evalsha.return_value = 1
        mock_redis.script_load.return_value = "sha"

        limiter = RateLimiter()
        limiter._rate_limit_script_sha = "sha"

        # We must patch get_client because conftest.py globally patches it to return FakeRedis
        with patch("src.app.core.utils.rate_limit.RateLimiter.get_client", return_value=mock_redis):
            result, count = await limiter.check_rate_limit("test_key", 10, 60)

        # Verify result
        assert result is False
        assert count == 1

        # Verify script execution: args = (sha, numkeys, key, [args])
        mock_redis.evalsha.assert_called_once()
        call_args = mock_redis.evalsha.call_args
        assert call_args[0][0] == "sha"
        assert call_args[0][1] == 1
        assert "ratelimit:test_key:" in call_args[0][2]

        mock_redis.incr.assert_not_called()
        mock_redis.expire.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_rate_limit_handles_noscript_error(self):
        """Test that check_rate_limit reloads script if NOSCRIPT error occurs."""
        from redis.exceptions import NoScriptError

        mock_redis = AsyncMock()
        mock_redis.evalsha.side_effect = [NoScriptError, 1]
        mock_redis.script_load.return_value = "new_sha"

        limiter = RateLimiter()
        limiter._rate_limit_script_sha = "old_sha"

        with patch("src.app.core.utils.rate_limit.RateLimiter.get_client", return_value=mock_redis):
            result, count = await limiter.check_rate_limit("test_key", 10, 60)

        assert result is False
        assert count == 1

        mock_redis.script_load.assert_called_once()
        assert mock_redis.evalsha.call_count == 2

    @pytest.mark.asyncio
    async def test_is_rate_limited_returns_false_if_under_limit(self):
        """Test is_rate_limited returns False (not limited) if count <= limit."""
        mock_redis = AsyncMock()
        mock_redis.evalsha.return_value = 5
        mock_redis.script_load.return_value = "sha"

        limiter = RateLimiter()
        limiter._rate_limit_script_sha = "sha"

        with patch("src.app.core.utils.rate_limit.RateLimiter.get_client", return_value=mock_redis):
            is_limited = await limiter.is_rate_limited(db=MagicMock(), user_id=1, path="/api", limit=10, period=60)

        assert is_limited is False

    @pytest.mark.asyncio
    async def test_is_rate_limited_returns_true_if_over_limit(self):
        """Test is_rate_limited returns True (limited) if count > limit."""
        mock_redis = AsyncMock()
        mock_redis.evalsha.return_value = 11
        mock_redis.script_load.return_value = "sha"

        limiter = RateLimiter()
        limiter._rate_limit_script_sha = "sha"

        with patch("src.app.core.utils.rate_limit.RateLimiter.get_client", return_value=mock_redis):
            is_limited = await limiter.is_rate_limited(db=MagicMock(), user_id=1, path="/api", limit=10, period=60)

        assert is_limited is True
