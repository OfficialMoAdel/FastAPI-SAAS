"""AI Rate Limiting Infrastructure.

T087: Create rate limiting infrastructure in src/app/modules/ai/rate_limiter.py

Provides Redis-based rate limiting for AI endpoints with configurable limits.
Default: 60 requests per user per minute.

Optimized with Lua script for atomic single-call rate limit checks.
"""

import logging

from redis.asyncio import Redis
from redis.exceptions import NoScriptError, ResponseError

logger = logging.getLogger(__name__)


class RateLimitError(Exception):
    """Exception raised when rate limit is exceeded."""

    def __init__(self, retry_after: int, message: str | None = None):
        self.retry_after = retry_after
        self.message = message or f"Rate limit exceeded. Retry after {retry_after} seconds."
        super().__init__(self.message)


class AIRateLimiter:
    """Redis-based rate limiter for AI endpoints.

    Implements sliding window rate limiting using Redis INCR with expiration.
    Each user has their own rate limit bucket.

    Optimized with Lua script for atomic INCR + TTL + EXPIRE in single call.

    Args:
        redis_client: Redis async client instance
        max_requests: Maximum requests allowed per window (default: 60)
        window_seconds: Time window in seconds (default: 60)
        key_prefix: Redis key prefix (default: "ai:rate_limit")
    """

    # Lua script for atomic rate limit check (INCR + TTL + conditional EXPIRE)
    # Returns [count, ttl] - eliminates race condition between INCR and TTL
    _RATE_LIMIT_SCRIPT = """
    local count = redis.call('INCR', KEYS[1])
    local ttl = redis.call('TTL', KEYS[1])
    if ttl < 0 then
        redis.call('EXPIRE', KEYS[1], ARGV[1])
        ttl = tonumber(ARGV[1])
    end
    return {count, ttl}
    """

    _script_sha: str | None = None

    def __init__(
        self,
        redis_client: Redis,
        max_requests: int = 60,
        window_seconds: int = 60,
        key_prefix: str = "ai:rate_limit",
    ):
        self.redis = redis_client
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.key_prefix = key_prefix

    def _get_key(self, user_id: int) -> str:
        """Generate Redis key for user's rate limit bucket."""
        return f"{self.key_prefix}:{user_id}"

    async def _get_script_sha(self) -> str:
        """Get or load the Lua script SHA for EVALSHA."""
        if AIRateLimiter._script_sha is None:
            AIRateLimiter._script_sha = await self.redis.script_load(self._RATE_LIMIT_SCRIPT)
        return AIRateLimiter._script_sha

    async def _execute_rate_limit_script(self, key: str, window: int) -> tuple[int, int]:
        """Execute rate limit Lua script atomically.

        Args:
            key: Redis key for rate limit bucket
            window: Window size in seconds

        Returns:
            Tuple of (count, ttl)
        """
        sha = await self._get_script_sha()
        try:
            result = await self.redis.evalsha(sha, 1, key, str(window))  # type: ignore[misc]
            # Handle both real Redis (returns list) and FakeRedis (may return int)
            if isinstance(result, list | tuple) and len(result) >= 2:
                return (int(result[0]), int(result[1]))
            # FakeRedis doesn't execute Lua properly - fallback
            raise ResponseError("FakeRedis Lua fallback")
        except NoScriptError:
            # Script was flushed from Redis, reload and retry
            AIRateLimiter._script_sha = None
            return await self._execute_rate_limit_script(key, window)

    async def check_rate_limit(self, user_id: int) -> tuple[bool, int, int]:
        """Check if request is within rate limit.

        Uses atomic Lua script for single Redis call (INCR + TTL + EXPIRE).
        Falls back to multi-call approach on script failure.

        Args:
            user_id: The user making the request

        Returns:
            Tuple of (is_allowed, remaining_requests, retry_after_seconds)
            - is_allowed: True if request is within limit
            - remaining_requests: Number of requests remaining in window
            - retry_after_seconds: Seconds until window resets (only meaningful if blocked)
        """
        key = self._get_key(user_id)

        try:
            # Atomic single-call rate limit check
            current_count, ttl = await self._execute_rate_limit_script(key, self.window_seconds)
        except ResponseError as e:
            # Fallback to original multi-call approach on script failure
            logger.warning(f"Lua script failed, using fallback: {e}")
            current_count, ttl = await self._fallback_check(key)

        # Calculate remaining requests
        remaining = max(0, self.max_requests - current_count)

        # Determine if request is allowed
        is_allowed = current_count <= self.max_requests

        # Calculate retry_after (only meaningful when blocked)
        retry_after = ttl if ttl > 0 else self.window_seconds

        return is_allowed, remaining, retry_after

    async def _fallback_check(self, key: str) -> tuple[int, int]:
        """Fallback rate limit check using multiple Redis calls.

        Used when Lua script execution fails.
        """
        current_count = await self.redis.incr(key)
        ttl = await self.redis.ttl(key)

        if ttl < 0:
            await self.redis.expire(key, self.window_seconds)
            ttl = self.window_seconds

        return current_count, ttl

    async def get_current_usage(self, user_id: int) -> tuple[int, int]:
        """Get current usage without incrementing counter.

        Args:
            user_id: The user to check

        Returns:
            Tuple of (current_count, ttl)
        """
        key = self._get_key(user_id)

        count_str = await self.redis.get(key)
        current_count = int(count_str) if count_str else 0

        ttl = await self.redis.ttl(key)
        ttl = ttl if ttl > 0 else 0

        return current_count, ttl

    async def reset_limit(self, user_id: int) -> None:
        """Reset rate limit for a user (admin function).

        Args:
            user_id: The user to reset
        """
        key = self._get_key(user_id)
        await self.redis.delete(key)


# Singleton instance (initialized on first use)
_rate_limiter: AIRateLimiter | None = None


def get_ai_rate_limiter(redis_client: Redis | None = None) -> AIRateLimiter:
    """Get or create the AI rate limiter instance.

    Args:
        redis_client: Optional Redis client. If not provided, uses the global cache client.

    Returns:
        AIRateLimiter instance
    """
    global _rate_limiter

    if _rate_limiter is None:
        if redis_client is None:
            # Import here to avoid circular imports
            from src.app.core.utils.cache import client as cache_client

            redis_client = cache_client

        if redis_client is None:
            raise ValueError("Redis client is required for AIRateLimiter")
        _rate_limiter = AIRateLimiter(redis_client=redis_client)

    return _rate_limiter


async def check_ai_rate_limit(user_id: int) -> tuple[bool, int, int]:
    """Convenience function to check rate limit.

    Args:
        user_id: The user making the request

    Returns:
        Tuple of (is_allowed, remaining_requests, retry_after_seconds)

    Raises:
        RateLimitError: If rate limit is exceeded
    """
    limiter = get_ai_rate_limiter()
    is_allowed, remaining, retry_after = await limiter.check_rate_limit(user_id)

    if not is_allowed:
        raise RateLimitError(retry_after=retry_after)

    return is_allowed, remaining, retry_after
