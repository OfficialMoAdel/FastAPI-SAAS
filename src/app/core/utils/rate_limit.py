from datetime import UTC, datetime
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.logger import get_logger
from src.app.core.utils import cache
from src.app.modules.rate_limits.schemas import sanitize_path

logger = get_logger(__name__)

# Lua script for atomic rate limit check
# Returns the current count after increment
# Sets expiry only on first increment (when count is 1). The TTL is aligned to
# the window boundary (period - (now % period)) so the counter does not persist
# past the start of the next window (which would worsen boundary bursts).
RATE_LIMIT_LUA_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    local now = redis.call('TIME')[1]
    local period = tonumber(ARGV[1])
    local ttl = period - (now % period)
    redis.call('EXPIRE', KEYS[1], ttl)
end
return current
"""


class RateLimiter:
    _instance: Optional["RateLimiter"] = None
    _rate_limit_script_sha: str | None = None

    def __new__(cls) -> "RateLimiter":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_client(cls) -> Redis:
        if cache.client is None:
            logger.error("Redis client is not initialized.")
            raise Exception("Redis client is not initialized.")
        return cache.client

    async def _get_script_sha(self, client: Redis) -> str:
        """Get or create the Lua script SHA for atomic rate limiting."""
        if self._rate_limit_script_sha is None:
            self._rate_limit_script_sha = await client.script_load(RATE_LIMIT_LUA_SCRIPT)
        return self._rate_limit_script_sha

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
        current_timestamp = int(datetime.now(UTC).timestamp())
        window_start = current_timestamp - (current_timestamp % period)

        redis_key = f"ratelimit:{key}:{window_start}"

        try:
            client = self.get_client()
            # Use atomic Lua script to prevent race conditions
            script_sha = await self._get_script_sha(client)
            try:
                current_count = await client.evalsha(script_sha, 1, redis_key, str(period))  # type: ignore
            except Exception:
                # Script may have been flushed, reload it
                self._rate_limit_script_sha = None
                script_sha = await self._get_script_sha(client)
                current_count = await client.evalsha(script_sha, 1, redis_key, str(period))  # type: ignore

            if current_count > limit:
                return True, current_count

        except Exception as e:
            logger.exception("Error checking rate limit for key :", key=key, error=e)
            # Use configurable fail-open/fail-closed behavior
            from src.app.core.config import settings
            from src.app.core.monitoring.r1_unified_metrics import record_fail_open

            if getattr(settings.rate_limit, "RATE_LIMIT_FAIL_OPEN", False):
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

        use_gcra = getattr(settings.rate_limit, "RATE_LIMIT_ALGORITHM", "fixed_window").lower() == "gcra"

        if use_gcra:
            from src.app.core.utils.gcra_rate_limit import get_gcra_rate_limiter

            gcra = get_gcra_rate_limiter()
            if gcra:
                try:
                    result = await gcra.check_rate_limit(key, limit, period)
                    return result.is_limited, result.remaining, int(result.retry_after)
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
            is_limited = not getattr(settings, "RATE_LIMIT_FAIL_OPEN", False)
            remaining = 0 if is_limited else limit
            return is_limited, remaining, 0

    async def is_rate_limited(self, db: AsyncSession, user_id: int | str, path: str, limit: int, period: int) -> bool:
        sanitized_path = sanitize_path(path)

        # P2-7: Incorporate tenant isolation in rate limit key
        from src.app.core.tenancy.context import get_current_tenant

        tenant = get_current_tenant()
        tenant_id = tenant.tenant_id if tenant else None

        if tenant_id:
            key = f"t:{tenant_id}:{user_id}:{sanitized_path}"
        else:
            key = f"{user_id}:{sanitized_path}"

        is_limited, _, _ = await self.check_rate_limit_algorithm(key, limit, period)
        return is_limited

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance and script cache.

        Used for test isolation to prevent state leakage between tests.
        """
        cls._instance = None
        cls._rate_limit_script_sha = None
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
