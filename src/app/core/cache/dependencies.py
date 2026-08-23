"""FastAPI dependency providing the shared Redis cache client.

The client itself is owned and initialized at startup by
``core.setup`` (mutating ``src.app.core.utils.cache.client``); this module is
the DI seam that hands it to request handlers. It reads the client through
the owning module's namespace at call time so runtime reassignment
(startup/shutdown) is always observed.
"""

from collections.abc import AsyncGenerator

from redis.asyncio import Redis

from src.app.core.exceptions.cache_exceptions import MissingClientError
from src.app.core.utils import cache as _cache


async def async_get_redis() -> AsyncGenerator[Redis, None]:
    """Get the shared Redis client from the pool.

    Note: The shared ``client`` is initialized at startup and reuses
    the connection pool for all requests, avoiding per-request overhead.

    Yields
    ------
    Redis
        The shared Redis client instance.

    Raises
    ------
    MissingClientError
        If the Redis client has not been initialized.
    """
    if _cache.client is None:
        raise MissingClientError("Redis client not initialized")
    yield _cache.client
