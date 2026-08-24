"""Unit tests for GCRA Rate Limiter."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.core.utils.gcra_rate_limit import (
    GCRARateLimiter,
    GCRARateLimitResult,
)


class TestGCRARateLimiter:
    """Tests for GCRARateLimiter class."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client for GCRA tests."""
        redis = MagicMock()
        redis.script_load = AsyncMock(return_value="test_sha")
        redis.evalsha = AsyncMock(return_value=[1, 9, 0, 1000000])  # allowed, 9 remaining
        redis.delete = AsyncMock(return_value=1)
        return redis

    @pytest.fixture
    def gcra_limiter(self, mock_redis):
        """Create GCRA limiter with mock Redis."""
        return GCRARateLimiter(redis_client=mock_redis)

    @pytest.mark.asyncio
    async def test_check_rate_limit_allowed(self, gcra_limiter, mock_redis):
        """Should allow request when under limit."""
        result = await gcra_limiter.check_rate_limit("user:123", limit=10, period=60)

        assert result.is_limited is False
        assert result.remaining == 9
        assert result.retry_after == 0.0
        # Script loading is owned by LuaScriptManager; the limiter only evals.
        mock_redis.evalsha.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_rate_limit_blocked(self, mock_redis):
        """Should block request when over limit."""
        mock_redis.evalsha = AsyncMock(return_value=[0, 0, 5000, 1000000])  # blocked
        limiter = GCRARateLimiter(redis_client=mock_redis)

        result = await limiter.check_rate_limit("user:123", limit=10, period=60)

        assert result.is_limited is True
        assert result.remaining == 0
        assert result.retry_after == 5.0  # 5000ms / 1000

    @pytest.mark.asyncio
    async def test_check_rate_limit_no_redis(self):
        """Should allow all requests when Redis unavailable."""
        limiter = GCRARateLimiter(redis_client=None)

        result = await limiter.check_rate_limit("user:123", limit=10, period=60)

        assert result.is_limited is False
        assert result.remaining == 10  # Full limit

    @pytest.mark.asyncio
    async def test_check_rate_limit_redis_error(self, mock_redis):
        """Should fail open on Redis error."""
        mock_redis.evalsha = AsyncMock(side_effect=Exception("Redis error"))
        limiter = GCRARateLimiter(redis_client=mock_redis)

        result = await limiter.check_rate_limit("user:123", limit=10, period=60)

        assert result.is_limited is True  # Fail closed by default
        assert result.remaining == 0

    @pytest.mark.asyncio
    async def test_reset_key(self, gcra_limiter, mock_redis):
        """Should reset rate limit for key."""
        result = await gcra_limiter.reset("user:123")

        assert result is True
        mock_redis.delete.assert_called_with("gcra:user:123")

    @pytest.mark.asyncio
    async def test_reset_no_redis(self):
        """Should return True when no Redis for reset."""
        limiter = GCRARateLimiter(redis_client=None)

        result = await limiter.reset("user:123")

        assert result is True

    @pytest.mark.asyncio
    async def test_evalsha_per_call(self, gcra_limiter, mock_redis):
        """Should EVALSHA on every check (script loading owned by LuaScriptManager)."""
        await gcra_limiter.check_rate_limit("user:1", limit=10, period=60)
        await gcra_limiter.check_rate_limit("user:2", limit=10, period=60)
        await gcra_limiter.check_rate_limit("user:3", limit=10, period=60)

        assert mock_redis.evalsha.call_count == 3

    @pytest.mark.asyncio
    async def test_correct_key_format(self, gcra_limiter, mock_redis):
        """Should use correct key format with gcra: prefix."""
        await gcra_limiter.check_rate_limit("api:endpoint:GET:/users", limit=100, period=60)

        call_args = mock_redis.evalsha.call_args
        # Second positional arg after numkeys is the key
        key_arg = call_args[0][2]
        assert key_arg == "gcra:api:endpoint:GET:/users"


class TestGCRARateLimitResult:
    """Tests for GCRARateLimitResult NamedTuple."""

    def test_result_fields(self):
        """Should have correct fields."""
        result = GCRARateLimitResult(
            is_limited=True,
            remaining=0,
            retry_after=5.5,
            reset_at=1234567890.0,
        )

        assert result.is_limited is True
        assert result.remaining == 0
        assert result.retry_after == 5.5
        assert result.reset_at == 1234567890.0

    def test_result_is_namedtuple(self):
        """Should be accessible by index and name."""
        result = GCRARateLimitResult(
            is_limited=False,
            remaining=10,
            retry_after=0.0,
            reset_at=0.0,
        )

        assert result[0] is False  # is_limited
        assert result[1] == 10  # remaining
        assert result.is_limited is False
        assert result.remaining == 10
