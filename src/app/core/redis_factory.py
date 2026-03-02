from redis.asyncio import ConnectionPool

from src.app.core.config import settings

_redis_pool: ConnectionPool | None = None


async def get_redis_pool() -> ConnectionPool:
    """Get or create the shared Redis connection pool."""
    global _redis_pool

    if _redis_pool is None:
        _redis_pool = ConnectionPool.from_url(
            settings.REDIS_CACHE_URL,
            max_connections=settings.REDIS_CACHE_MAX_CONNECTIONS,
            socket_timeout=settings.REDIS_CACHE_SOCKET_TIMEOUT,
            socket_connect_timeout=settings.REDIS_CACHE_SOCKET_CONNECT_TIMEOUT,
            retry_on_timeout=True,
            decode_responses=False,  # Shared pool must use bytes for ARQ compatibility
        )

    return _redis_pool


async def close_redis_pool() -> None:
    """Close the shared Redis connection pool."""
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.disconnect()
        _redis_pool = None
