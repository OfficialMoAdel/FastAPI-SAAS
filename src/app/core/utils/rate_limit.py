from typing import Optional

from redis.asyncio import Redis

from src.app.core.logger import get_logger
from src.app.core.utils.lua_manager import get_lua_manager

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

    async def check_rate_limit(
        self, key: str, limit: int, period: int, *, fail_closed: bool = False
    ) -> tuple[bool, int]:
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
            # Use configurable fail-open/fail-closed behavior. C2: callers
            # guarding brute-force budgets pass fail_closed=True so a Redis
            # outage maps to limited even when the general RATE_LIMIT_FAIL_OPEN
            # operator default is True.
            from src.app.core.config import settings
            from src.app.core.monitoring.r1_unified_metrics import record_fail_open

            fail_open = settings.rate_limit.RATE_LIMIT_FAIL_OPEN and not fail_closed
            if fail_open:
                record_fail_open("rate_limit")
                return False, 0  # Allow request (fail-open)
            else:
                if fail_closed:
                    record_fail_open("rate_limit:fail_closed_budget")
                return True, limit + 1  # Block request (fail-closed)

        return False, current_count

    async def check_rate_limit_algorithm(
        self, key: str, limit: int, period: int, *, fail_closed: bool = False
    ) -> tuple[bool, int, int]:
        """Check rate limit using configured algorithm (GCRA or Fixed Window).

        Returns:
            Tuple of (is_limited, remaining, retry_after)

        The degradation signal is NOT part of this contract — use
        :meth:`check_rate_limit_algorithm_with_status` when the caller has to
        report that a different engine than the configured one enforced the
        limit (the response header and the fail-open counter both need it).
        """
        is_limited, remaining, retry_after, _degraded = await self.check_rate_limit_algorithm_with_status(
            key, limit, period, fail_closed=fail_closed
        )
        return is_limited, remaining, retry_after

    async def check_rate_limit_algorithm_with_status(
        self, key: str, limit: int, period: int, *, fail_closed: bool = False
    ) -> tuple[bool, int, int, bool]:
        """Check the rate limit and REPORT whether the engine degraded.

        A silent algorithm swap is worse than the outage it hides: the
        configured engine (GCRA) failed and the fixed window took over, so
        the bucket now allows the 2x boundary burst that GCRA exists to
        remove. That is counted (fail-open counter) and returned, so the
        caller can mark the verdict degraded and say so in the response.

        Returns:
            Tuple of (is_limited, remaining, retry_after, degraded) —
            ``degraded`` is True when the limit was NOT enforced by the
            configured algorithm.
        """
        from src.app.core.config import settings

        use_gcra = settings.rate_limit.RATE_LIMIT_ALGORITHM.lower() == "gcra"
        degraded = False

        if use_gcra:
            from src.app.core.utils.gcra_rate_limit import get_gcra_rate_limiter

            gcra = get_gcra_rate_limiter()
            if gcra:
                try:
                    result = await gcra.check_rate_limit(key, limit, period, fail_closed=fail_closed)
                    # R6 FIX: round UP so sub-second retry windows are not
                    # truncated to 0 (0 triggered the bogus fixed-window
                    # formula upstream, reporting e.g. 400ms as a full period).
                    import math

                    retry_after = int(math.ceil(result.retry_after)) if result.is_limited else 0
                    return result.is_limited, result.remaining, retry_after, False
                except Exception as e:
                    logger.warning("gcra_rate_limit_check_failed", error=str(e))
                    # The swap is a DEGRADATION, not a silent fallback: count
                    # it and tell the caller, so the response can say which
                    # engine actually enforced the limit.
                    from src.app.core.monitoring.r1_unified_metrics import record_fail_open

                    record_fail_open("rate_limit:gcra_degraded")
                    logger.error(
                        "gcra_degraded_to_fixed_window",
                        key=key,
                        error=str(e),
                    )
                    degraded = True
            else:
                # GCRA is configured but the limiter was never initialised
                # (Redis was absent at boot). The fixed window below will
                # enforce the limit, so this is the same degradation as the
                # failed-GCRA branch above — counted and reported, not silent.
                from src.app.core.monitoring.r1_unified_metrics import record_fail_open

                record_fail_open("rate_limit:gcra_uninitialized")
                logger.error("gcra_limiter_uninitialized_degrading_to_fixed_window", key=key)
                degraded = True

        # Fixed window fallback / default
        try:
            is_limited, current_count = await self.check_rate_limit(key, limit, period, fail_closed=fail_closed)
            remaining = max(0, limit - current_count)
            return is_limited, remaining, 0, degraded
        except Exception as fallback_e:
            logger.error("rate_limit_check_failed", error=str(fallback_e))
            # R1 FIX: RATE_LIMIT_FAIL_OPEN lives on settings.rate_limit; the old
            # getattr on the root settings object silently ignored operators.
            # C2: fail_closed=True forces limited regardless of the operator flag.
            is_limited = fail_closed or not settings.rate_limit.RATE_LIMIT_FAIL_OPEN
            remaining = 0 if is_limited else limit
            return is_limited, remaining, 0, degraded

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
