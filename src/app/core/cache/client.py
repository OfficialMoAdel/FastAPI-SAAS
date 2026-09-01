"""CacheClient — the declared seam for cache operations (candidate 2).

Every module that today acquires a Redis client four different ways
(``redis_provider.get_redis``, the legacy ``utils.cache.client`` global,
direct ``redis.asyncio`` imports, or the FastAPI ``async_get_redis``
dependency) can instead depend on this small interface. The Redis adapter
wraps the SAME shared client the provider hands out — it observes test
patches of ``utils.cache.client`` exactly like ``get_redis()`` does, so
the ~46 test files that mock the global keep working unchanged.

The interface is deliberately narrow: get/set/delete/invalidate semantics
plus ``tenant_key()`` (the tenant-scoped key builder from ``utils.cache``).
Stampede protection (xfetch), pattern invalidation, and the Lua manager
remain available on the underlying client when a caller needs them —
this seam is for the common case, not a Redis re-implementation.
"""

from __future__ import annotations

from typing import Any, Protocol, cast

from src.app.core.utils.cache.keys import build_tenant_key


class CacheClient(Protocol):
    """Narrow cache interface: the common case for module-level caching.

    Two adapters satisfy this: ``RedisCacheAdapter`` in production, an
    in-memory fake in tests (see tests/unit/core/cache/).
    """

    async def get(self, key: str) -> str | None:
        """Return the value at ``key``, or None."""
        ...  # pragma: no cover - Protocol

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        """Set ``key`` to ``value`` with an optional TTL in seconds."""
        ...  # pragma: no cover - Protocol

    async def delete(self, *keys: str) -> int:
        """Delete keys; return the number removed."""
        ...  # pragma: no cover - Protocol

    async def exists(self, key: str) -> bool:
        """Return True if ``key`` exists."""
        ...  # pragma: no cover - Protocol

    @staticmethod
    def tenant_key(key: str, tenant_id: Any = None) -> str:
        """Build a tenant-scoped key (prefixes tenant when present)."""
        ...  # pragma: no cover - Protocol


class RedisCacheAdapter:
    """Adapter over the shared Redis client from ``redis_provider``.

    Not a re-implementation: every method delegates to the client that
    ``get_redis()`` returns at call time, so runtime reassignment and
    ``mock.patch("...utils.cache.client", ...)`` in tests are always
    observed (same contract as the provider itself).
    """

    async def get(self, key: str) -> str | None:
        from src.app.core.redis_provider import get_redis

        client = get_redis()
        if client is None:
            return None
        return cast("str | None", await client.get(key))

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        from src.app.core.redis_provider import get_redis

        client = get_redis()
        if client is None:
            return
        if ttl is not None:
            await client.set(key, value, ex=ttl)
        else:
            await client.set(key, value)

    async def delete(self, *keys: str) -> int:
        from src.app.core.redis_provider import get_redis

        client = get_redis()
        if client is None or not keys:
            return 0
        return cast(int, await client.delete(*keys))

    async def exists(self, key: str) -> bool:
        from src.app.core.redis_provider import get_redis

        client = get_redis()
        if client is None:
            return False
        return bool(await client.exists(key))

    @staticmethod
    def tenant_key(key: str, tenant_id: Any = None) -> str:
        return build_tenant_key(key, tenant_id)


# Module-level singleton — the adapter is stateless (all delegation is
# call-time resolved), matching the provider/dependency pattern.
cache_client = RedisCacheAdapter()

__all__ = ["CacheClient", "RedisCacheAdapter", "cache_client"]
