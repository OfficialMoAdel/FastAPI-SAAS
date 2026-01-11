"""Performance benchmarks for AI rate limiting operations.

Tests verify rate limiting meets P95 latency requirements.
Uses simple timing assertions (no pytest-benchmark dependency).
"""

import time
from unittest.mock import AsyncMock

import pytest

# Performance thresholds in seconds
THRESHOLDS = {
    "rate_limit_lua": 0.005,  # 5ms for Lua script
    "rate_limit_fallback": 0.005,  # 5ms for fallback
    "key_generation": 0.0001,  # 0.1ms for key gen
}


class TestAIRateLimiterPerformance:
    """Benchmark AI rate limiter operations."""

    @pytest.mark.asyncio
    async def test_rate_limit_check_lua_script_performance(self, mock_redis) -> None:
        """Lua script rate limit check should be very fast."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        AIRateLimiter._script_sha = "cached_sha"
        limiter = AIRateLimiter(mock_redis, max_requests=60, window_seconds=60)

        start = time.perf_counter()
        for _ in range(100):
            result = await limiter.check_rate_limit(user_id=12345)
        elapsed = (time.perf_counter() - start) / 100

        is_allowed, remaining, retry_after = result
        assert is_allowed is True
        assert remaining == 59
        assert elapsed < THRESHOLDS["rate_limit_lua"], f"Too slow: {elapsed * 1000:.2f}ms"

    @pytest.mark.asyncio
    async def test_rate_limit_check_fallback_performance(self, mock_redis) -> None:
        """Fallback rate limit check should still be fast."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        limiter = AIRateLimiter(mock_redis, max_requests=60, window_seconds=60)

        start = time.perf_counter()
        for _ in range(100):
            result = await limiter._fallback_check("ai:rate_limit:12345")
        elapsed = (time.perf_counter() - start) / 100

        count, ttl = result
        assert count == 1
        assert ttl == 60
        assert elapsed < THRESHOLDS["rate_limit_fallback"], f"Too slow: {elapsed * 1000:.2f}ms"


class TestRateLimitKeyGeneration:
    """Benchmark rate limit key generation."""

    def test_key_generation_performance(self, mock_redis) -> None:
        """Key generation should be instant."""
        from src.app.modules.ai.rate_limiter import AIRateLimiter

        limiter = AIRateLimiter(mock_redis)

        start = time.perf_counter()
        for _ in range(1000):
            result = limiter._get_key(user_id=999999)
        elapsed = (time.perf_counter() - start) / 1000

        assert result == "ai:rate_limit:999999"
        assert elapsed < THRESHOLDS["key_generation"], f"Too slow: {elapsed * 1000:.3f}ms"
