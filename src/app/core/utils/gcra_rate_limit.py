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

    async def _execute_gcra_script(
        self,
        rate_limit_key: str,
        now_ms: float,
        emission_interval_ms: float,
        limit: int,
        period_ms: float,
    ) -> tuple:
        """Execute the GCRA Lua script via the shared LuaScriptManager.

        The manager owns EVALSHA + NOSCRIPT fallback, so script reloads after
        Redis restarts are handled centrally.
        """
        if not self._redis:
            raise ValueError("Redis client not initialized")

        from src.app.core.utils.lua_manager import get_lua_manager

        # R2 FIX: burst tolerance must derive from the SAME (rounded) emission
        # interval the script uses, not from raw period_ms - otherwise capacity
        # drifts by limit * rounding_error in either direction.
        burst_tolerance_ms = int(emission_interval_ms) * int(limit)

        result = await get_lua_manager().execute(
            self._redis,
            "rate_limit_gcra",
            keys=[rate_limit_key],
            args=[
                str(float(now_ms)),
                str(float(emission_interval_ms)),
                str(int(limit)),
                str(float(burst_tolerance_ms)),
                str(float(period_ms)),
            ],
        )
        # Ensure result is a tuple/list
        if not isinstance(result, list | tuple):
            raise TypeError(f"Unexpected result type: {type(result)}, value: {result}")
        return tuple(result)  # type: ignore[return-value]

    async def check_rate_limit(
        self,
        key: str,
        limit: int,
        period: int,
        *,
        fail_closed: bool = False,
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
            # R9 FIX: honor the operator-configured failure mode instead of an
            # unconditional fail-open that diverged from fixed-window's
            # fail-closed default. C2: fail_closed=True forces limited even
            # when the general RATE_LIMIT_FAIL_OPEN=True.
            from src.app.core.config import settings

            fail_open = settings.rate_limit.RATE_LIMIT_FAIL_OPEN and not fail_closed
            return GCRARateLimitResult(
                is_limited=not fail_open,
                remaining=limit if fail_open else 0,
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

            # R1 FIX: RATE_LIMIT_FAIL_OPEN lives on settings.rate_limit; reading
            # it off the root settings object silently ignores operators.
            # C2: fail_closed=True forces limited regardless of the flag.
            fail_open = settings.rate_limit.RATE_LIMIT_FAIL_OPEN and not fail_closed
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
