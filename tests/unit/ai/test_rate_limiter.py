"""Unit tests for AI rate limiter with Lua script optimization.

Tests verify atomic single-call rate limiting via EVALSHA.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.app.modules.ai.rate_limiter import AIRateLimiter, RateLimitError


class TestAIRateLimiterLuaScript:
    """Tests for Lua script-based rate limiting."""

    @pytest.mark.asyncio
    async def test_check_rate_limit_uses_lua_script(self) -> None:
        """Should use EVALSHA for atomic rate limit check."""
        mock_redis = AsyncMock()
        mock_redis.script_load = AsyncMock(return_value="abc123sha")
        mock_redis.evalsha = AsyncMock(return_value=[1, 60])

        limiter = AIRateLimiter(mock_redis, max_requests=10, window_seconds=60)
        is_allowed, remaining, retry_after = await limiter.check_rate_limit(user_id=42)

        assert is_allowed is True
        assert remaining == 9
        assert retry_after == 60
        mock_redis.evalsha.assert_called_once()

    @pytest.mark.asyncio
    async def test_rate_limit_blocks_when_exceeded(self) -> None:
        """Should block requests when limit is exceeded."""
        mock_redis = AsyncMock()
        mock_redis.script_load = AsyncMock(return_value="abc123sha")
        mock_redis.evalsha = AsyncMock(return_value=[11, 45])  # 11 > 10 max

        limiter = AIRateLimiter(mock_redis, max_requests=10, window_seconds=60)
        is_allowed, remaining, retry_after = await limiter.check_rate_limit(user_id=42)

        assert is_allowed is False
        assert remaining == 0
        assert retry_after == 45

    @pytest.mark.asyncio
    async def test_script_sha_is_cached(self) -> None:
        """Should cache script SHA across calls."""
        mock_redis = AsyncMock()
        mock_redis.script_load = AsyncMock(return_value="cached_sha")
        mock_redis.evalsha = AsyncMock(return_value=[1, 60])

        # Reset class-level cache
        AIRateLimiter._script_sha = None

        limiter = AIRateLimiter(mock_redis)
        await limiter.check_rate_limit(user_id=1)
        await limiter.check_rate_limit(user_id=2)
        await limiter.check_rate_limit(user_id=3)

        # Script should only be loaded once
        assert mock_redis.script_load.call_count == 1
        assert mock_redis.evalsha.call_count == 3

    @pytest.mark.asyncio
    async def test_fallback_on_script_failure(self) -> None:
        """Should fall back to multi-call approach on script error."""
        from redis.exceptions import ResponseError

        mock_redis = AsyncMock()
        mock_redis.script_load = AsyncMock(return_value="sha")
        mock_redis.evalsha = AsyncMock(side_effect=ResponseError("ERR"))
        mock_redis.incr = AsyncMock(return_value=5)
        mock_redis.ttl = AsyncMock(return_value=30)

        # Reset cache
        AIRateLimiter._script_sha = None

        limiter = AIRateLimiter(mock_redis, max_requests=10)
        is_allowed, remaining, retry_after = await limiter.check_rate_limit(user_id=42)

        assert is_allowed is True
        assert remaining == 5
        mock_redis.incr.assert_called_once()
        mock_redis.ttl.assert_called_once()

    @pytest.mark.asyncio
    async def test_script_reload_on_noscripterror(self) -> None:
        """Should reload script when Redis flushes it."""
        from redis.exceptions import NoScriptError

        mock_redis = AsyncMock()
        mock_redis.script_load = AsyncMock(return_value="new_sha")
        # First call raises NoScriptError, second succeeds
        mock_redis.evalsha = AsyncMock(side_effect=[NoScriptError("NOSCRIPT"), [3, 55]])

        # Reset cache
        AIRateLimiter._script_sha = None

        limiter = AIRateLimiter(mock_redis, max_requests=10)
        is_allowed, remaining, retry_after = await limiter.check_rate_limit(user_id=42)

        assert is_allowed is True
        assert remaining == 7
        # Script loaded twice (initial + after NoScriptError)
        assert mock_redis.script_load.call_count == 2


class TestRateLimitError:
    """Tests for RateLimitError exception."""

    def test_error_has_retry_after(self) -> None:
        """Should include retry_after in exception."""
        error = RateLimitError(retry_after=30)
        assert error.retry_after == 30
        assert "30 seconds" in str(error)

    def test_custom_message(self) -> None:
        """Should support custom error message."""
        error = RateLimitError(retry_after=60, message="Custom limit message")
        assert error.message == "Custom limit message"
