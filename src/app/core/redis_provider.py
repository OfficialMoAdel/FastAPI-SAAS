"""Single Redis/ARQ access seam.

One module owns connection lifecycle and hands out clients. Client
construction lives here too (:func:`create_cache_client` /
:func:`create_arq_pool`) — no other module constructs ``redis.Redis(...)`` or
``ArqRedis(...)`` directly.

Consumer direction (A3):

- Module-level caching (get/set/delete/exists, JSON values, tenant-scoped
  keys, atomic claims) goes through the CacheClient interface
  (:mod:`src.app.core.cache.client` :: ``cache_client``) — never
  :func:`get_redis` directly. The adapter resolves the same shared client
  at call time, so the None-guards and key-building live behind its seam.
- :func:`get_redis` remains for infrastructure primitives the narrow
  interface does not cover (pipelines, pub/sub, Lua scripts, hashes,
  streams, distributed locks, FastAPI DI in
  :mod:`src.app.core.cache.dependencies`) and as the patch point the test
  suite mocks. New module-caching code must not import it.

Compatibility contract
----------------------
The legacy module globals ``src.app.core.utils.cache.client/pool`` and
``src.app.core.utils.queue.pool`` remain the live state: the provider writes
them on startup/shutdown, and reads them at call time so ``mock.patch`` of
those names in the ~46 test files keeps working unchanged. New code should
not read the globals directly — modules use ``cache_client``; infra calls
the functions here.
"""
from typing import cast

from arq.connections import ArqRedis
from redis.asyncio import ConnectionPool, Redis

from src.app.core.logger import get_logger

logger = get_logger(__name__)

__all__ = [
    "create_arq_pool",
    "create_cache_client",
    "get_arq",
    "get_redis",
    "get_redis_pool",
    "set_clients",
    "shutdown",
]

_shared_pool: ConnectionPool | None = None


async def get_redis_pool() -> ConnectionPool:
    """Create (or return) the shared raw Redis connection pool.

    Pool ownership lives here — the provider is the single owner of the
    Redis lifecycle (clients + pool). The former CacheManager shim
    (core/cache/manager.py, zero module callers beyond this delegation)
    was deleted; this is the same pool-config logic, relocated.
    """
    global _shared_pool
    from src.app.core.config import settings

    if _shared_pool is None:
        _shared_pool = ConnectionPool.from_url(
            settings.redis_cache.REDIS_CACHE_URL,
            max_connections=settings.redis_cache.REDIS_CACHE_MAX_CONNECTIONS,
            socket_timeout=settings.redis_cache.REDIS_CACHE_SOCKET_TIMEOUT,
            socket_connect_timeout=settings.redis_cache.REDIS_CACHE_SOCKET_CONNECT_TIMEOUT,
            retry_on_timeout=True,
            decode_responses=False,
        )
    return cast(ConnectionPool, _shared_pool)


async def close_redis_pool() -> None:
    """Close the shared pool (lifespan teardown path)."""
    global _shared_pool
    if _shared_pool is not None:
        await _shared_pool.disconnect()
        _shared_pool = None


def get_redis() -> Redis | None:
    """Return the shared decode_responses Redis client, or None before startup.

    Legacy entry point for module caching — new module-caching code must use
    the CacheClient interface (``src.app.core.cache.client.cache_client``)
    instead. Retained for infrastructure primitives (pipelines, pub/sub, Lua,
    hashes, streams, locks), the FastAPI DI seam, and the test suite, which
    mocks the underlying global.

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


async def create_cache_client() -> Redis:
    """Create the shared decode_responses cache client and install it.

    Single construction seam for the cache client (P1-#3: the escape hatch
    in ``core/setup.py`` moved here). Idempotent: returns the already
    installed client when one exists.
    """
    import src.app.core.utils.cache as _cache

    if _cache.client is not None:
        return _cache.client
    pool = await get_redis_pool()
    client = Redis(connection_pool=pool, decode_responses=True)
    await set_clients(redis_client=client, redis_pool=pool)
    return client


async def create_arq_pool() -> ArqRedis:
    """Create the shared ARQ queue client on the shared pool.

    Single construction seam for the queue client (K1 wire serializers via
    :func:`get_arq_pool_kwargs`). Does NOT install into the legacy
    ``queue.pool`` global — callers that need it installed use
    :func:`set_clients`.
    """
    from src.app.core.events.arq_adapter import get_arq_pool_kwargs

    pool = await get_redis_pool()
    return ArqRedis(connection_pool=pool, **get_arq_pool_kwargs())


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
