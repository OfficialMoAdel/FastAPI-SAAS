"""Single Redis/ARQ access seam.

One module owns connection lifecycle and hands out clients. Every consumer
either imports :func:`get_redis` / :func:`get_arq` from here or goes through
FastAPI DI (:mod:`src.app.core.cache.dependencies`) — never the bare globals.

Compatibility contract
----------------------
The legacy module globals ``src.app.core.utils.cache.client/pool`` and
``src.app.core.utils.queue.pool`` remain the live state: the provider writes
them on startup/shutdown, and reads them at call time so ``mock.patch`` of
those names in the ~46 test files keeps working unchanged. New code should
not read the globals directly — call the functions here.
"""

from arq.connections import ArqRedis
from redis.asyncio import ConnectionPool, Redis

from src.app.core.logger import get_logger

logger = get_logger(__name__)

__all__ = ["get_arq", "get_redis", "get_redis_pool", "require_redis", "set_clients", "shutdown"]




async def get_redis_pool() -> ConnectionPool:
    """Create (or return) the shared raw Redis connection pool.

    Delegates to CacheManager, which owns pool configuration
    (URL, max connections, timeouts) and instance caching.
    """
    from src.app.core.cache.manager import cache_manager

    return await cache_manager.get_pool()


def get_redis() -> Redis | None:
    """Return the shared decode_responses Redis client, or None before startup.

    Reads through the owning module's namespace at call time so runtime
    reassignment (startup, shutdown, test fixtures that patch
    ``utils.cache.client``) is always observed.
    """
    import src.app.core.utils.cache as _cache

    return _cache.client


def get_arq() -> ArqRedis | None:
    """Return the shared ARQ queue client, or None when queues are disabled.

    Same late-binding semantics as :func:`get_redis`; observes patches of
    ``utils.queue.pool``.
    """
    from src.app.core.utils import queue

    return queue.pool


async def set_clients(
    *,
    redis_client: Redis | None,
    redis_pool: ConnectionPool | None = None,
    arq_pool: ArqRedis | None = None,
) -> None:
    """Install clients into the legacy global slots (startup path).

    Args:
        redis_client: The shared cache client (decode_responses=True).
        redis_pool: Optional underlying connection pool.
        arq_pool: Optional ARQ queue client sharing the same pool.
    """
    import src.app.core.utils.cache as _cache
    from src.app.core.utils import queue

    _cache.client = redis_client
    _cache.pool = redis_pool
    if arq_pool is not None:
        queue.pool = arq_pool


async def shutdown() -> None:
    """Close and clear both clients (lifespan teardown path)."""
    import src.app.core.utils.cache as _cache
    from src.app.core.utils import queue

    if _cache.client is not None:
        try:
            # aclose() for proper async cleanup (redis-py 5.0+)
            if hasattr(_cache.client, "aclose"):
                await _cache.client.aclose()  # type: ignore[attr-defined]
            else:
                await _cache.client.close()
        except Exception as e:
            logger.warning("redis_client_close_failed", error=str(e))
        _cache.client = None
    _cache.pool = None

    if queue.pool is not None:
        try:
            await queue.pool.close()
        except Exception as e:
            logger.warning("arq_pool_close_failed", error=str(e))
        queue.pool = None


def require_redis() -> Redis:
    """Return the shared Redis client or raise if not initialized.

    For code paths where None means a configuration error rather than an
    expected disabled state.
    """
    from src.app.core.exceptions.cache_exceptions import MissingClientError

    client = get_redis()
    if client is None:
        raise MissingClientError("Redis client not initialized")
    return client
