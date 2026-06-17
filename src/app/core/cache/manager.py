"""Unified Cache Manager.

Consolidates Redis connection pooling, cache warming, and core cache operations.
"""

from redis.asyncio import ConnectionPool, Redis

from src.app.core.cache.warmer import CacheWarmer, warmer
from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.utils.cache import client as default_client

logger = get_logger(__name__)


class CacheManager:
    """Unified manager for cache operations, connections, and warming."""

    def __init__(self) -> None:
        self._pool: ConnectionPool | None = None
        self.warmer: CacheWarmer = warmer

    async def get_pool(self) -> ConnectionPool:
        """Get or create the shared Redis connection pool."""
        if self._pool is None:
            self._pool = ConnectionPool.from_url(
                settings.REDIS_CACHE_URL,
                max_connections=settings.REDIS_CACHE_MAX_CONNECTIONS,
                socket_timeout=settings.REDIS_CACHE_SOCKET_TIMEOUT,
                socket_connect_timeout=settings.REDIS_CACHE_SOCKET_CONNECT_TIMEOUT,
                retry_on_timeout=True,
                decode_responses=False,
            )
        return self._pool

    async def close_pool(self) -> None:
        """Close the shared Redis connection pool."""
        if self._pool is not None:
            await self._pool.disconnect()
            self._pool = None

    @property
    def client(self) -> Redis | None:
        """Get the active Redis client."""
        # Fallback to the utils/cache.py client for backward compatibility
        return default_client


# Global instance
cache_manager = CacheManager()

# Export backward-compatible functions
get_redis_pool = cache_manager.get_pool
close_redis_pool = cache_manager.close_pool
