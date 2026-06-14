"""GCRA (Generic Cell Rate Algorithm) Rate Limiter.

GCRA provides smoother rate limiting than Fixed Window by using
Theoretical Arrival Time (TAT) to track request spacing.

Benefits over Fixed Window:
- No boundary burst problem (2x limit at window edges)
- Smoother request distribution
- Better protection against traffic spikes
- Single Redis key per rate limit (no window suffixes)

Algorithm:
1. emission_interval = period / limit (time between allowed requests)
2. TAT = max(now, previous_TAT) + emission_interval
3. If TAT > now + period: REJECT (too far ahead)
4. Else: ALLOW, store new TAT
"""

import time
from typing import NamedTuple

from redis.asyncio import Redis

from src.app.core.logger import get_logger

logger = get_logger(__name__)


class GCRARateLimitResult(NamedTuple):
    """Result of a GCRA rate limit check."""

    is_limited: bool
    remaining: int
    retry_after: float  # Seconds until next request allowed
    reset_at: float  # Unix timestamp when limit resets


# Lua script for atomic GCRA operation
# Returns: [is_allowed (0/1), remaining, retry_after_ms, reset_at_ms]
GCRA_LUA_SCRIPT = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local emission_interval_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local period_ms = tonumber(ARGV[4])

-- burst_tolerance = period (allows burst up to limit within period)
local burst_tolerance_ms = period_ms

-- Get current TAT (Theoretical Arrival Time)
local tat_ms = tonumber(redis.call('GET', key)) or now_ms

-- Calculate new TAT
local new_tat_ms = math.max(now_ms, tat_ms) + emission_interval_ms

-- Check if request is allowed
-- If new_tat > now + burst_tolerance, reject
local allow_at_ms = new_tat_ms - burst_tolerance_ms

if allow_at_ms > now_ms then
    -- Request not allowed yet
    local retry_after_ms = allow_at_ms - now_ms
    -- remaining = 0 when blocked
    return {0, 0, retry_after_ms, new_tat_ms}
else
    -- Request allowed, update TAT
    redis.call('SET', key, new_tat_ms)
    -- Expire key after period + emission_interval (cleanup). PEXPIRE requires an integer.
    redis.call('PEXPIRE', key, math.ceil(period_ms + emission_interval_ms))

    -- Calculate remaining requests
    -- remaining = how many more requests fit before new_tat > now + burst_tolerance
    local remaining = 0
    if emission_interval_ms > 0 then
        remaining = math.floor((now_ms + burst_tolerance_ms - new_tat_ms) / emission_interval_ms)
    end
    remaining = math.max(0, math.min(remaining, limit - 1))

    return {1, remaining, 0, new_tat_ms}
end
"""


class GCRARateLimiter:
    """GCRA-based rate limiter using Redis.

    Provides smoother rate limiting than Fixed Window algorithm.
    Uses atomic Lua script for thread-safe operation.
    """

    def __init__(self, redis_client: Redis | None = None):
        """Initialize GCRA rate limiter.

        Args:
            redis_client: Async Redis client. If None, rate limiting is bypassed.
        """
        self._redis = redis_client
        self._script_sha: str | None = None

    async def _ensure_script_loaded(self) -> str:
        """Load Lua script if not already loaded."""
        if self._script_sha is None and self._redis:
            self._script_sha = await self._redis.script_load(GCRA_LUA_SCRIPT)  # type: ignore[misc]
        return self._script_sha or ""

    async def _execute_gcra_script(
        self,
        rate_limit_key: str,
        now_ms: float,
        emission_interval_ms: float,
        limit: int,
        period_ms: float,
    ) -> tuple:
        """Execute GCRA script with automatic script reload on failure.

        Handles the case where script SHA becomes invalid (e.g., Redis restart,
        new connection, or script eviction).
        """
        if not self._redis:
            raise ValueError("Redis client not initialized")

        script_sha = await self._ensure_script_loaded()

        try:
            result = await self._redis.evalsha(  # type: ignore[misc,union-attr]
                script_sha,
                1,  # Number of keys
                rate_limit_key,
                str(float(now_ms)),
                str(float(emission_interval_ms)),
                str(int(limit)),
                str(float(period_ms)),
            )
            # Ensure result is a tuple/list
            if not isinstance(result, list | tuple):
                raise TypeError(f"Unexpected result type: {type(result)}, value: {result}")
            return tuple(result)  # type: ignore[return-value]
        except Exception as e:
            # Script might have been evicted or SHA is invalid
            # Clear cached SHA and reload
            error_msg = str(e).lower()
            if "script" in error_msg or "sha" in error_msg or "not found" in error_msg:
                logger.warning("GCRA script SHA invalid, reloading", error=e)
                self._script_sha = None
                script_sha = await self._ensure_script_loaded()

                # Retry once
                result = await self._redis.evalsha(  # type: ignore[misc,union-attr]
                    script_sha,
                    1,
                    rate_limit_key,
                    str(float(now_ms)),
                    str(float(emission_interval_ms)),
                    str(int(limit)),
                    str(float(period_ms)),
                )
                if not isinstance(result, list | tuple):
                    raise TypeError(f"Unexpected result type after reload: {type(result)}")
                return tuple(result)  # type: ignore[return-value]
            raise

    async def check_rate_limit(
        self,
        key: str,
        limit: int,
        period: int,
    ) -> GCRARateLimitResult:
        """Check if request is allowed under GCRA rate limit.

        Args:
            key: Rate limit key (e.g., "user:123" or "ip:1.2.3.4")
            limit: Maximum requests allowed per period
            period: Time period in seconds

        Returns:
            GCRARateLimitResult with is_limited, remaining, retry_after, reset_at
        """
        if not self._redis:
            # No Redis = no rate limiting
            return GCRARateLimitResult(
                is_limited=False,
                remaining=limit,
                retry_after=0.0,
                reset_at=0.0,
            )

        try:
            if limit <= 0:
                return GCRARateLimitResult(
                    is_limited=True,
                    remaining=0,
                    retry_after=float(period),
                    reset_at=time.time() + period,
                )

            # Calculate emission interval (time between requests)
            period_ms = period * 1000
            emission_interval_ms = max(1, round(period_ms / limit)) if limit > 0 else 0
            now_ms = time.time() * 1000

            # Execute atomic GCRA check
            rate_limit_key = f"gcra:{key}"
            result = await self._execute_gcra_script(
                rate_limit_key,
                int(now_ms),
                int(emission_interval_ms),
                limit,
                int(period_ms),
            )

            is_allowed, remaining, retry_after_ms, reset_at_ms = result

            return GCRARateLimitResult(
                is_limited=int(is_allowed) == 0,
                remaining=int(float(remaining)),
                retry_after=float(retry_after_ms) / 1000.0,
                reset_at=float(reset_at_ms) / 1000.0,
            )

        except Exception as e:
            logger.warning("GCRA rate limit check failed", error=e)

            # Use configured fail-open behavior
            from src.app.core.config import settings

            fail_open = getattr(settings, "RATE_LIMIT_FAIL_OPEN", False)
            is_limited = not fail_open

            return GCRARateLimitResult(
                is_limited=is_limited,
                remaining=limit if fail_open else 0,
                retry_after=0.0,
                reset_at=0.0,
            )

    async def reset(self, key: str) -> bool:
        """Reset rate limit for a key.

        Args:
            key: Rate limit key to reset

        Returns:
            True if reset successful
        """
        if not self._redis:
            return True

        try:
            await self._redis.delete(f"gcra:{key}")
            return True
        except Exception as e:
            logger.warning("GCRA reset failed", error=e)
            return False


# Global instance (initialized with Redis client at startup)
gcra_rate_limiter: GCRARateLimiter | None = None


def get_gcra_rate_limiter() -> GCRARateLimiter | None:
    """Get the global GCRA rate limiter instance."""
    return gcra_rate_limiter


def init_gcra_rate_limiter(redis_client: Redis) -> GCRARateLimiter:
    """Initialize the global GCRA rate limiter.

    Args:
        redis_client: Async Redis client

    Returns:
        Initialized GCRARateLimiter instance
    """
    global gcra_rate_limiter
    gcra_rate_limiter = GCRARateLimiter(redis_client)
    return gcra_rate_limiter


def reset_gcra_rate_limiter() -> None:
    """Reset the global GCRA rate limiter for test isolation.

    Call this in test fixtures to ensure clean state between tests.
    """
    global gcra_rate_limiter
    gcra_rate_limiter = None
    logger.debug("GCRA rate limiter reset")
