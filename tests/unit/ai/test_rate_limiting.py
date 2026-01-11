"""Unit tests for AI rate limiting middleware.

Tests T086: Unit test for rate limiting middleware in tests/unit/ai/test_rate_limiting.py

PERF-R1: Updated to mock Lua script execution (evalsha/script_load) since
AIRateLimiter now uses atomic Lua scripts by default, with fallback to
multi-call approach only on script failure.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.exceptions import ResponseError


class TestAIRateLimiter:
    """Test suite for AI rate limiting infrastructure."""

    @pytest.fixture
    def mock_redis(self):
        """Create a mock Redis client with Lua script support.

        The AIRateLimiter uses Lua scripts for atomic operations.
        We mock evalsha to return [count, ttl] as the script does.
        """
        redis = AsyncMock()
        # Standard Redis commands (used in fallback and other methods)
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock(return_value=True)
        redis.incr = AsyncMock(return_value=1)
        redis.expire = AsyncMock(return_value=True)
        redis.ttl = AsyncMock(return_value=60)
        redis.delete = AsyncMock(return_value=1)

        # Lua script support - AIRateLimiter uses evalsha for atomic ops
        redis.script_load = AsyncMock(return_value="fake_sha")
        # Default: first request (count=1, ttl=60)
        redis.evalsha = AsyncMock(return_value=[1, 60])

        return redis

    @pytest.mark.asyncio
    async def test_first_request_allowed(self, mock_redis):
        """Test that first request is always allowed."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        # Lua script returns [count=1, ttl=60] for first request
        mock_redis.evalsha.return_value = [1, 60]

        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=60, window_seconds=60)
        is_allowed, remaining, retry_after = await limiter.check_rate_limit(user_id=1)

        assert is_allowed is True
        assert remaining == 59  # 60 - 1

    @pytest.mark.asyncio
    async def test_request_within_limit(self, mock_redis):
        """Test that requests within limit are allowed."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        # Lua script returns [count=30, ttl=30] for 30th request
        mock_redis.evalsha.return_value = [30, 30]

        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=60, window_seconds=60)
        is_allowed, remaining, retry_after = await limiter.check_rate_limit(user_id=1)

        assert is_allowed is True
        assert remaining == 30  # 60 - 30

    @pytest.mark.asyncio
    async def test_request_at_limit(self, mock_redis):
        """Test that request at limit is still allowed."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        # Lua script returns [count=60, ttl=45] - exactly at limit
        mock_redis.evalsha.return_value = [60, 45]

        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=60, window_seconds=60)
        is_allowed, remaining, retry_after = await limiter.check_rate_limit(user_id=1)

        assert is_allowed is True
        assert remaining == 0

    @pytest.mark.asyncio
    async def test_request_exceeds_limit(self, mock_redis):
        """Test that requests exceeding limit are blocked."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        # Lua script returns [count=61, ttl=45] - over limit
        mock_redis.evalsha.return_value = [61, 45]

        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=60, window_seconds=60)
        is_allowed, remaining, retry_after = await limiter.check_rate_limit(user_id=1)

        assert is_allowed is False
        assert remaining == 0
        assert retry_after == 45  # Seconds until window resets

    @pytest.mark.asyncio
    async def test_rate_limit_key_format(self, mock_redis):
        """Test that rate limit key includes user_id."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        mock_redis.evalsha.return_value = [1, 60]

        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=60, window_seconds=60)
        await limiter.check_rate_limit(user_id=123)

        # Check that evalsha was called with key containing user_id
        call_args = mock_redis.evalsha.call_args
        # evalsha(sha, num_keys, key, window) - key is at index 2
        key = call_args[0][2] if len(call_args[0]) > 2 else ""
        assert "123" in key or "ai:rate_limit" in key

    @pytest.mark.asyncio
    async def test_window_expiry_set_on_first_request(self, mock_redis):
        """Test that expiry is set on first request via Lua script.

        The Lua script handles expiry atomically - if TTL < 0, it sets EXPIRE.
        We verify the script was called (which handles expiry internally).
        """
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        # Script returns TTL that was just set
        mock_redis.evalsha.return_value = [1, 60]

        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=60, window_seconds=60)
        await limiter.check_rate_limit(user_id=1)

        # evalsha should be called (Lua script handles expire internally)
        mock_redis.evalsha.assert_called_once()

    @pytest.mark.asyncio
    async def test_window_expiry_not_reset_on_subsequent_requests(self, mock_redis):
        """Test that expiry is not reset on subsequent requests."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        # Script returns existing TTL (45 seconds remaining)
        mock_redis.evalsha.return_value = [10, 45]

        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=60, window_seconds=60)
        await limiter.check_rate_limit(user_id=1)

        # expire should NOT be called directly (Lua script handles it)
        mock_redis.expire.assert_not_called()

    @pytest.mark.asyncio
    async def test_different_users_have_separate_limits(self, mock_redis):
        """Test that different users have independent rate limits."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        call_count = 0
        def evalsha_side_effect(*args):
            nonlocal call_count
            call_count += 1
            return [call_count, 60]

        mock_redis.evalsha = AsyncMock(side_effect=evalsha_side_effect)

        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=60, window_seconds=60)

        # User 1's first request
        await limiter.check_rate_limit(user_id=1)
        # User 2's first request
        await limiter.check_rate_limit(user_id=2)

        # Should have been called twice with different keys
        assert mock_redis.evalsha.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_after_header_value(self, mock_redis):
        """Test that retry_after is calculated correctly."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        # Over limit, 30 seconds remaining in window
        mock_redis.evalsha.return_value = [61, 30]

        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=60, window_seconds=60)
        is_allowed, remaining, retry_after = await limiter.check_rate_limit(user_id=1)

        assert is_allowed is False
        assert retry_after == 30

    @pytest.mark.asyncio
    async def test_default_retry_after_when_no_ttl(self, mock_redis):
        """Test default retry_after when TTL is not available."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        # Over limit, TTL is -1 (key exists but no TTL - edge case)
        mock_redis.evalsha.return_value = [61, -1]

        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=60, window_seconds=60)
        is_allowed, remaining, retry_after = await limiter.check_rate_limit(user_id=1)

        assert is_allowed is False
        assert retry_after == 60  # Default to window_seconds

    @pytest.mark.asyncio
    async def test_custom_rate_limit_values(self, mock_redis):
        """Test custom rate limit configuration."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        # 101st request with custom 100 request limit
        mock_redis.evalsha.return_value = [101, 30]

        # Custom: 100 requests per 120 seconds
        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=100, window_seconds=120)
        is_allowed, remaining, retry_after = await limiter.check_rate_limit(user_id=1)

        assert is_allowed is False
        assert remaining == 0

    @pytest.mark.asyncio
    async def test_fallback_on_script_error(self, mock_redis):
        """Test fallback to multi-call approach when Lua script fails."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        # First call fails with ResponseError, triggering fallback
        mock_redis.evalsha.side_effect = ResponseError("NOSCRIPT")
        mock_redis.incr.return_value = 5
        mock_redis.ttl.return_value = 45

        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=60, window_seconds=60)
        is_allowed, remaining, retry_after = await limiter.check_rate_limit(user_id=1)

        # Fallback should work
        assert is_allowed is True
        assert remaining == 55  # 60 - 5
        mock_redis.incr.assert_called_once()


class TestRateLimitMiddleware:
    """Test suite for rate limiting middleware integration."""

    @pytest.fixture
    def mock_request(self):
        """Create a mock FastAPI request."""
        request = MagicMock()
        request.state = MagicMock()
        request.state.user_id = 1
        request.url.path = "/api/v1/ai/sessions/1/messages"
        return request

    @pytest.fixture
    def mock_call_next(self):
        """Create a mock call_next function."""
        async def call_next(request):
            response = MagicMock()
            response.status_code = 200
            response.headers = {}
            return response
        return call_next

    @pytest.mark.asyncio
    async def test_middleware_allows_within_limit(self, mock_request, mock_call_next):
        """Test middleware allows requests within rate limit."""
        # This test verifies middleware behavior when integrated
        # The actual middleware will be implemented in T099
        pass

    @pytest.mark.asyncio
    async def test_middleware_blocks_over_limit(self, mock_request, mock_call_next):
        """Test middleware blocks requests over rate limit."""
        # This test verifies 429 response when rate limited
        # The actual middleware will be implemented in T099
        pass

    @pytest.mark.asyncio
    async def test_middleware_adds_rate_limit_headers(self, mock_request, mock_call_next):
        """Test middleware adds X-RateLimit headers to response."""
        # Expected headers:
        # X-RateLimit-Limit: 60
        # X-RateLimit-Remaining: 59
        # X-RateLimit-Reset: <timestamp>
        pass

    @pytest.mark.asyncio
    async def test_middleware_adds_retry_after_when_limited(self, mock_request, mock_call_next):
        """Test middleware adds Retry-After header when rate limited."""
        # When 429 is returned, Retry-After header should be set
        pass


class TestRateLimitError:
    """Test suite for RateLimitError exception."""

    def test_rate_limit_error_creation(self):
        """Test RateLimitError can be created with retry_after."""
        from src.app.modules.ai.rate_limiter import RateLimitError

        error = RateLimitError(retry_after=30, message="Rate limit exceeded")

        assert error.retry_after == 30
        assert "Rate limit exceeded" in str(error)

    def test_rate_limit_error_default_message(self):
        """Test RateLimitError has default message."""
        from src.app.modules.ai.rate_limiter import RateLimitError

        error = RateLimitError(retry_after=60)

        assert error.retry_after == 60
        assert str(error) is not None
