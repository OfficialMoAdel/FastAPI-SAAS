from typing import Optional

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.logger import get_logger
from src.app.core.utils.lua_manager import get_lua_manager
from src.app.core.utils.path_utils import sanitize_path

logger = get_logger(__name__)


class RateLimiter:
    _instance: Optional["RateLimiter"] = None

    def __new__(cls) -> "RateLimiter":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_client(cls) -> Redis:
        from src.app.core.redis_provider import get_redis

        client = get_redis()
        if client is None:
            logger.error("Redis client is not initialized.")
            raise Exception("Redis client is not initialized.")
        return client

    async def check_rate_limit(self, key: str, limit: int, period: int) -> tuple[bool, int]:
        """
        Check if a generic key is rate limited using atomic Lua script.

        Args:
            key: Unique key for rate limiting
            limit: Maximum count allowed
            period: Time period in seconds

        Returns
        -------
            Tuple (is_limited, current_count)
        """
        # client = self.get_client()  <-- Moved inside try block
        # R5 FIX: the fixed-window boundary is computed inside the Lua script
        # from the Redis clock. Deriving window_start here from the app clock
        # let app/Redis clock skew create keys whose TTL belonged to a
        # different window.
        redis_key = f"ratelimit:{key}"

        try:
            client = self.get_client()
            # Atomic Lua script via the shared manager (EVALSHA + NOSCRIPT fallback)
            current_count = await get_lua_manager().execute(
                client,
                "rate_limit_fixed_window",
                keys=[redis_key],
                args=[str(period)],
            )

            if current_count > limit:
                return True, current_count

        except Exception as e:
            logger.exception("Error checking rate limit for key :", key=key, error=e)
            # Use configurable fail-open/fail-closed behavior
            from src.app.core.config import settings
            from src.app.core.monitoring.r1_unified_metrics import record_fail_open

            if settings.rate_limit.RATE_LIMIT_FAIL_OPEN:
                record_fail_open("rate_limit")
                return False, 0  # Allow request (fail-open)
            else:
                return True, limit + 1  # Block request (fail-closed)

        return False, current_count

    async def check_rate_limit_algorithm(self, key: str, limit: int, period: int) -> tuple[bool, int, int]:
        """Check rate limit using configured algorithm (GCRA or Fixed Window).

        Returns:
            Tuple of (is_limited, remaining, retry_after)
        """
        from src.app.core.config import settings

        use_gcra = settings.rate_limit.RATE_LIMIT_ALGORITHM.lower() == "gcra"

        if use_gcra:
            from src.app.core.utils.gcra_rate_limit import get_gcra_rate_limiter

            gcra = get_gcra_rate_limiter()
            if gcra:
                try:
                    result = await gcra.check_rate_limit(key, limit, period)
                    # R6 FIX: round UP so sub-second retry windows are not
                    # truncated to 0 (0 triggered the bogus fixed-window
                    # formula upstream, reporting e.g. 400ms as a full period).
                    import math

                    retry_after = int(math.ceil(result.retry_after)) if result.is_limited else 0
                    return result.is_limited, result.remaining, retry_after
                except Exception as e:
                    logger.warning("GCRA rate limit check failed: %s", e)
                    # Fallback to fixed window handled below

        # Fixed window fallback / default
        try:
            is_limited, current_count = await self.check_rate_limit(key, limit, period)
            remaining = max(0, limit - current_count)
            return is_limited, remaining, 0
        except Exception as fallback_e:
            logger.error("Rate limit check failed: %s", fallback_e)
            # R1 FIX: RATE_LIMIT_FAIL_OPEN lives on settings.rate_limit; the old
            # getattr on the root settings object silently ignored operators.
            is_limited = not settings.rate_limit.RATE_LIMIT_FAIL_OPEN
            remaining = 0 if is_limited else limit
            return is_limited, remaining, 0

    async def is_rate_limited(self, db: AsyncSession, user_id: int | str, path: str, limit: int, period: int) -> bool:
        sanitized_path = sanitize_path(path)

        # P2-7: Incorporate tenant isolation in rate limit key
        from src.app.core.tenancy.context import get_current_tenant

        tenant = get_current_tenant()
        tenant_id = tenant.tenant_id if tenant else None

        if tenant_id:
            key = f"tenant:{tenant_id}:{user_id}:{sanitized_path}"
        else:
            key = f"{user_id}:{sanitized_path}"

        is_limited, _, _ = await self.check_rate_limit_algorithm(key, limit, period)
        return is_limited

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance (test isolation)."""
        cls._instance = None
        logger.debug("RateLimiter singleton reset")


def get_rate_limiter() -> RateLimiter:
    """Get the singleton rate limiter instance.

    Returns:
        The global RateLimiter instance.
    """
    return RateLimiter()


def reset_rate_limiter() -> None:
    """Reset the rate limiter singleton for test isolation.

    Call this in test fixtures to ensure clean state between tests.
    """
    RateLimiter.reset()


# Module-level instance for backward compatibility
rate_limiter = RateLimiter()
