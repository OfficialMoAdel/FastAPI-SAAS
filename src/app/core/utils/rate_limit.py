from datetime import UTC, datetime
from typing import Optional

from redis.asyncio import ConnectionPool, Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logger import logging
from ...modules.rate_limits.schemas import sanitize_path

logger = logging.getLogger(__name__)


class RateLimiter:
    _instance: Optional["RateLimiter"] = None
    pool: ConnectionPool | None = None
    client: Redis | None = None

    def __new__(cls) -> "RateLimiter":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def initialize(cls, redis_url: str) -> None:
        instance = cls()
        if instance.pool is None:
            instance.pool = ConnectionPool.from_url(redis_url)
            instance.client = Redis(connection_pool=instance.pool)

    @classmethod
    def get_client(cls) -> Redis:
        instance = cls()
        if instance.client is None:
            logger.error("Redis client is not initialized.")
            raise Exception("Redis client is not initialized.")
        return instance.client

    async def check_rate_limit(self, key: str, limit: int, period: int) -> tuple[bool, int]:
        """
        Check if a generic key is rate limited.

        Args:
            key: Unique key for rate limiting
            limit: Maximum count allowed
            period: Time period in seconds

        Returns
        -------
            Tuple (is_limited, current_count)
        """
        client = self.get_client()
        current_timestamp = int(datetime.now(UTC).timestamp())
        window_start = current_timestamp - (current_timestamp % period)

        redis_key = f"ratelimit:{key}:{window_start}"

        try:
            current_count = await client.incr(redis_key)
            if current_count == 1:
                await client.expire(redis_key, period)

            if current_count > limit:
                return True, current_count

        except Exception as e:
            logger.exception(f"Error checking rate limit for key {key}: {e}")
            # Use configurable fail-open/fail-closed behavior
            from ...core.config import settings
            if settings.RATE_LIMIT_FAIL_OPEN:
                return False, 0  # Allow request (fail-open)
            else:
                return True, limit + 1  # Block request (fail-closed)

        return False, current_count

    async def is_rate_limited(self, db: AsyncSession, user_id: int, path: str, limit: int, period: int) -> bool:
        sanitized_path = sanitize_path(path)
        key = f"{user_id}:{sanitized_path}"
        is_limited, _ = await self.check_rate_limit(key, limit, period)
        return is_limited


rate_limiter = RateLimiter()
