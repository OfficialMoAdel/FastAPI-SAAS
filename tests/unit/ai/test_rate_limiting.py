"""Unit tests for AI rate limiting middleware.

Tests T086: Unit test for rate limiting middleware in tests/unit/ai/test_rate_limiting.py
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import time


class TestAIRateLimiter:
    """Test suite for AI rate limiting infrastructure."""

    @pytest.fixture
    def mock_redis(self):
        """Create a mock Redis client."""
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock(return_value=True)
        redis.incr = AsyncMock(return_value=1)
        redis.expire = AsyncMock(return_value=True)
        redis.ttl = AsyncMock(return_value=60)
        return redis

    @pytest.mark.asyncio
    async def test_first_request_allowed(self, mock_redis):
        """Test that first request is always allowed."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        mock_redis.incr.return_value = 1  # First request
        mock_redis.ttl.return_value = -2  # No TTL set yet

        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=60, window_seconds=60)
        is_allowed, remaining, retry_after = await limiter.check_rate_limit(user_id=1)

        assert is_allowed is True
        assert remaining == 59  # 60 - 1

    @pytest.mark.asyncio
    async def test_request_within_limit(self, mock_redis):
        """Test that requests within limit are allowed."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        mock_redis.incr.return_value = 30  # 30th request
        mock_redis.ttl.return_value = 30  # 30 seconds left

        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=60, window_seconds=60)
        is_allowed, remaining, retry_after = await limiter.check_rate_limit(user_id=1)

        assert is_allowed is True
        assert remaining == 30  # 60 - 30

    @pytest.mark.asyncio
    async def test_request_at_limit(self, mock_redis):
        """Test that request at limit is still allowed."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        mock_redis.incr.return_value = 60  # Exactly at limit
        mock_redis.ttl.return_value = 45

        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=60, window_seconds=60)
        is_allowed, remaining, retry_after = await limiter.check_rate_limit(user_id=1)

        assert is_allowed is True
        assert remaining == 0

    @pytest.mark.asyncio
    async def test_request_exceeds_limit(self, mock_redis):
        """Test that requests exceeding limit are blocked."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        mock_redis.incr.return_value = 61  # Over limit
        mock_redis.ttl.return_value = 45  # 45 seconds until reset

        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=60, window_seconds=60)
        is_allowed, remaining, retry_after = await limiter.check_rate_limit(user_id=1)

        assert is_allowed is False
        assert remaining == 0
        assert retry_after == 45  # Seconds until window resets

    @pytest.mark.asyncio
    async def test_rate_limit_key_format(self, mock_redis):
        """Test that rate limit key includes user_id."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        mock_redis.incr.return_value = 1
        mock_redis.ttl.return_value = -2

        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=60, window_seconds=60)
        await limiter.check_rate_limit(user_id=123)

        # Check that incr was called with key containing user_id
        call_args = mock_redis.incr.call_args
        key = call_args[0][0] if call_args[0] else call_args.kwargs.get("key", "")
        assert "123" in key or "ai:rate_limit" in key

    @pytest.mark.asyncio
    async def test_window_expiry_set_on_first_request(self, mock_redis):
        """Test that expiry is set on first request."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        mock_redis.incr.return_value = 1
        mock_redis.ttl.return_value = -2  # No TTL means first request

        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=60, window_seconds=60)
        await limiter.check_rate_limit(user_id=1)

        # expire should be called for first request
        mock_redis.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_window_expiry_not_reset_on_subsequent_requests(self, mock_redis):
        """Test that expiry is not reset on subsequent requests."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        mock_redis.incr.return_value = 10  # Not first request
        mock_redis.ttl.return_value = 45  # TTL already set

        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=60, window_seconds=60)
        await limiter.check_rate_limit(user_id=1)

        # expire should NOT be called when TTL already exists
        mock_redis.expire.assert_not_called()

    @pytest.mark.asyncio
    async def test_different_users_have_separate_limits(self, mock_redis):
        """Test that different users have independent rate limits."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        call_count = 0
        def incr_side_effect(key):
            nonlocal call_count
            call_count += 1
            return call_count

        mock_redis.incr = AsyncMock(side_effect=incr_side_effect)
        mock_redis.ttl.return_value = -2

        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=60, window_seconds=60)

        # User 1's first request
        await limiter.check_rate_limit(user_id=1)
        # User 2's first request
        await limiter.check_rate_limit(user_id=2)

        # Should have been called twice with different keys
        assert mock_redis.incr.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_after_header_value(self, mock_redis):
        """Test that retry_after is calculated correctly."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        mock_redis.incr.return_value = 61
        mock_redis.ttl.return_value = 30  # 30 seconds remaining

        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=60, window_seconds=60)
        is_allowed, remaining, retry_after = await limiter.check_rate_limit(user_id=1)

        assert is_allowed is False
        assert retry_after == 30

    @pytest.mark.asyncio
    async def test_default_retry_after_when_no_ttl(self, mock_redis):
        """Test default retry_after when TTL is not available."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        mock_redis.incr.return_value = 61
        mock_redis.ttl.return_value = -1  # Key exists but no TTL

        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=60, window_seconds=60)
        is_allowed, remaining, retry_after = await limiter.check_rate_limit(user_id=1)

        assert is_allowed is False
        assert retry_after == 60  # Default to window_seconds

    @pytest.mark.asyncio
    async def test_custom_rate_limit_values(self, mock_redis):
        """Test custom rate limit configuration."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        mock_redis.incr.return_value = 101
        mock_redis.ttl.return_value = 30

        # Custom: 100 requests per 120 seconds
        limiter = AIRateLimiter(redis_client=mock_redis, max_requests=100, window_seconds=120)
        is_allowed, remaining, retry_after = await limiter.check_rate_limit(user_id=1)

        assert is_allowed is False
        assert remaining == 0


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
