import logging

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

LOGGER = logging.getLogger(__name__)


async def check_database_health(db: AsyncSession) -> bool:
    try:
        await db.execute(text("SELECT 1"))
        return True
    except Exception as e:
        LOGGER.exception(f"Database health check failed with error: {e}")
        return False


async def check_redis_health(redis: Redis) -> bool:
    try:
        await redis.ping()
        return True
    except Exception as e:
        LOGGER.exception(f"Redis health check failed with error: {e}")
        return False


async def check_redis_queue_health() -> bool:
    """Check Redis queue (ARQ) health for background job processing."""
    from .utils.queue import queue_pool

    if queue_pool is None:
        LOGGER.warning("Redis queue pool not initialized")
        return False
    try:
        redis = Redis(connection_pool=queue_pool)
        await redis.ping()
        return True
    except Exception as e:
        LOGGER.exception(f"Redis queue health check failed with error: {e}")
        return False

