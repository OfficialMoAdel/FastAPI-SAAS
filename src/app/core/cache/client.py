"""CacheClient — the interface for module-level cache operations (A3).

Every module that needs plain caching (get/set/delete/exists, JSON
values, tenant-scoped keys, atomic claims) depends on this interface
instead of acquiring a raw Redis client. The Redis adapter wraps the SAME
shared client the provider hands out — it observes test patches of
``utils.cache.client`` exactly like ``get_redis()`` does, so existing
tests that mock the global keep working unchanged.

The interface is deliberately narrow: get/set/delete/invalidate semantics
plus ``tenant_key()`` (the tenant-scoped key builder from ``utils.cache``).
Stampede protection (xfetch), pattern invalidation, and the Lua manager
remain available on the underlying client when a caller needs them —
this seam is for the common case, not a Redis re-implementation.
"""

from __future__ import annotations

from typing import Any, Protocol, cast

from src.app.core.logger import get_logger
from src.app.core.utils.cache.keys import build_tenant_key

logger = get_logger(__name__)


class CacheClient(Protocol):
    """Narrow cache interface: the common case for module-level caching.

    Two adapters satisfy this: ``RedisCacheAdapter`` in production, an
    in-memory fake in tests (see tests/unit/core/cache/).
    """

    async def get(self, key: str) -> str | None:
        """Return the value at ``key``, or None."""
        ...  # pragma: no cover - Protocol

    async def get_strict(self, key: str) -> str | None:
        """Read ``key`` without swallowing backend errors.

        Security-sensitive checks use this: a Redis outage must be
        observable so the caller can log it and run its own fallback,
        instead of being indistinguishable from a cache miss.
        """
        ...  # pragma: no cover - Protocol

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        """Set ``key`` to ``value`` with an optional TTL in seconds."""
        ...  # pragma: no cover - Protocol

    async def set_nx(self, key: str, value: str, ttl: int) -> bool:
        """Atomically set ``key`` only if absent; return True if acquired.

        The claim primitive for distributed locks and idempotency markers.
        ``ttl`` is required (no persistent locks — a crashed holder must
        never block claimants forever). Returns False when the client is
        unavailable, so callers needing a down-Redis fallback must check
        availability via the ``client`` property first.
        """
        ...  # pragma: no cover - Protocol

    async def delete(self, *keys: str) -> int:
        """Delete keys; return the number removed."""
        ...  # pragma: no cover - Protocol

    async def release_lock_if_owned(self, key: str, token: str) -> bool:
        """Release a lock ONLY if this caller still owns it.

        The ownership-token Lua (``lock_release_if_owned``) is the primitive
        the repo already uses in four places. A blind delete lets a slow
        worker whose lock has expired delete the lock a DIFFERENT worker
        acquired in the meantime. Returns False when the lock is gone, is
        owned by someone else, or could not be released.
        """
        ...  # pragma: no cover - Protocol

    async def exists(self, key: str) -> bool:
        """Return True if ``key`` exists."""
        ...  # pragma: no cover - Protocol

    @staticmethod
    def tenant_key(key: str, tenant_id: Any = None) -> str:
        """Build a tenant-scoped key (prefixes tenant when present)."""
        ...  # pragma: no cover - Protocol

    @property
    def client(self) -> Any:
        """Access the underlying Redis client when needed."""
        ...  # pragma: no cover - Protocol

    async def get_json(self, key: str) -> Any | None:
        """Get and deserialize JSON value at ``key``, or None."""
        ...  # pragma: no cover - Protocol

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Serialize and set JSON value at ``key`` with optional TTL."""
        ...  # pragma: no cover - Protocol

# P2-4: fail-safe default TTL (5 min, the TENANT-cache convention) so a
# missing ttl can never create a non-expiring key (unbounded growth).
_DEFAULT_TTL = 300


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
        try:
            return cast("str | None", await client.get(key))
        except Exception as e:  # noqa: BLE001 - the cache must never fail a request
            # D4: the cache is a read-ahead optimisation. A Redis outage must
            # read as a MISS, not as a failed request.
            logger.warning("cache_get_failed", key=key, error=str(e))
            return None

    async def get_strict(self, key: str) -> str | None:
        from src.app.core.redis_provider import get_redis

        client = get_redis()
        if client is None:
            return None
        # Deliberately uncaught: callers that need fail-open use ``get``.
        return cast("str | None", await client.get(key))

    async def set(self, key: str, value: str, ttl: int | None = _DEFAULT_TTL) -> None:
        from src.app.core.redis_provider import get_redis

        client = get_redis()
        if client is None:
            return
        # P2-B: an explicit ttl=None is coerced to the fail-safe default —
        # a missing ttl must never create a non-expiring key (unbounded
        # growth). There is no persistent-key path on this adapter.
        try:
            await client.set(key, value, ex=ttl if ttl is not None else _DEFAULT_TTL)
        except Exception as e:  # noqa: BLE001 - the cache must never fail a request
            # D4: a write that follows a SUCCEEDED business operation must not
            # turn that success into a 500. The value is lost, the operation
            # is not.
            logger.warning("cache_set_failed", key=key, error=str(e))

    async def set_nx(self, key: str, value: str, ttl: int) -> bool:
        from src.app.core.redis_provider import get_redis

        client = get_redis()
        if client is None:
            return False
        # D4: the claim is the ONE operation that does NOT fail open. "The
        # key was taken" and "the claim could not be made" are different
        # answers, and a caller that cannot tell them apart is in trouble:
        # the billing webhook lock falls back to the DB idempotency check
        # when the claim ERRORS, and that fallback is what stops a Stripe
        # event from being processed twice. Collapsing an error into False
        # would silently drop that fallback. The error propagates.
        result = await client.set(key, value, ex=ttl, nx=True)
        return bool(result)

    async def delete(self, *keys: str) -> int:
        from src.app.core.redis_provider import get_redis

        client = get_redis()
        if client is None or not keys:
            return 0
        try:
            return cast(int, await client.delete(*keys))
        except Exception as e:  # noqa: BLE001 - the cache must never fail a request
            logger.warning("cache_delete_failed", keys=list(keys), error=str(e))
            return 0

    async def release_lock_if_owned(self, key: str, token: str) -> bool:
        """Release a lock only if this caller still owns it.

        Uses the ownership-token Lua the repo already owns
        (``lock_release_if_owned``). There is deliberately NO blind-delete
        fallback: the whole point is to avoid deleting a lock a different
        worker acquired after this one's expired. A lock that cannot be
        released expires on its own TTL.
        """
        from src.app.core.redis_provider import get_redis

        client = get_redis()
        if client is None:
            return False
        try:
            from src.app.core.utils.lua_manager import get_lua_manager

            released = await get_lua_manager().execute(
                client,
                "lock_release_if_owned",
                keys=[key],
                args=[token],
            )
            return bool(released)
        except Exception as e:  # noqa: BLE001 - the lock expires on its own
            logger.warning("cache_lock_release_failed", key=key, error=str(e))
            return False

    async def exists(self, key: str) -> bool:
        from src.app.core.redis_provider import get_redis

        client = get_redis()
        if client is None:
            return False
        try:
            return bool(await client.exists(key))
        except Exception as e:  # noqa: BLE001 - the cache must never fail a request
            logger.warning("cache_exists_failed", key=key, error=str(e))
            return False

    @staticmethod
    def tenant_key(key: str, tenant_id: Any = None) -> str:
        return build_tenant_key(key, tenant_id)

    @property
    def client(self) -> Any:
        from src.app.core.redis_provider import get_redis

        return get_redis()

    async def get_json(self, key: str) -> Any | None:
        val = await self.get(key)
        if val is None:
            return None
        from src.app.core.json_utils import json_loads

        try:
            return json_loads(val)
        except Exception as e:
            from src.app.core.logger import get_logger as _get_logger

            _get_logger(__name__).debug("cache_json_decode_failed", key=key, error=str(e))
            return None

    async def set_json(self, key: str, value: Any, ttl: int | None = _DEFAULT_TTL) -> None:
        from src.app.core.json_utils import json_dumps

        serialized = json_dumps(value, as_str=True)
        await self.set(key, serialized, ttl=ttl)

# Module-level singleton — the adapter is stateless (all delegation is
# call-time resolved), matching the provider/dependency pattern.
cache_client = RedisCacheAdapter()

__all__ = ["CacheClient", "RedisCacheAdapter", "cache_client"]
