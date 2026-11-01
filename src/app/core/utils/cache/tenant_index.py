"""Tenant cache index — set-based O(|S|) invalidation with prefix sharding."""

from src.app.core.utils import cache as _state
from src.app.core.utils.cache.index_policy import index_ttl


def _matches_prefix(cache_key: str, prefix: str) -> bool:
    """Does a FULL cache key contain the business prefix ``prefix``?

    Index members are complete keys built by ``decorator._build_cache_key``:

    - ``public:{version}:{prefix}:{id}``
    - ``tenant:{tid}:{version}:{prefix}:{id}``
    - ``tenant:{tid}:user:{uid}:{version}:{prefix}:{id}``

    The prefix position varies with the key shape, and the prefix itself may
    contain colons (``user_post_cache:{username}``), so slicing at a fixed
    segment cannot work. Wrapping both sides in ``:`` and testing containment
    matches the prefix only at segment boundaries, at any depth.

    NOT the live implementation. The S-14 global-index fallback now runs inside
    the ``cache_tenant_prefix_drain`` Lua script (``lua_manager.py``), which
    matches with ``string.find(':' .. member .. ':', ':' .. prefix .. ':', 1,
    true)`` — the same colon-boundary rule, executed atomically with the drain.
    The LUA SCRIPT IS AUTHORITATIVE; this Python form has no production caller
    (only ``tests/unit/core/cache/test_cache_index.py``) and is kept as the
    readable, unit-tested statement of the rule. An edit to the matching rule
    must land in the Lua script, and this helper must follow it — never the
    other way round, or the two matchers drift with the untested one winning.
    """
    if not prefix:
        return False
    return f":{prefix}:" in f":{cache_key}:"


async def invalidate_tenant_cache(tenant_id: int | str, prefix: str | None = None) -> int:
    """Invalidate all cache entries for a tenant using set-based indexing.

    This uses a Redis Set to track all keys for a tenant, enabling O(|S|)
    invalidation where S is the number of keys for that tenant, instead
    of O(K) where K is total keys in Redis.

    Optimized to use prefix-sharded indexes for O(1) subset invalidation.

    Args:
        tenant_id: Tenant identifier
        prefix: Optional prefix to limit invalidation scope

    Returns
    -------
        Number of keys deleted
    """
    client = _state.client
    if client is None:
        return 0

    # P1-1: Hierarchical Tenant Index Sharding
    # If prefix is provided, use the sharded index instead of filtering the global index.
    #
    # Both drain paths are atomic Lua scripts. The previous Python shape
    # (SMEMBERS -> pipeline delete) had a write-after-snapshot window: a writer
    # could SADD a key after the snapshot but before the index deletion, leaving
    # that live cache key unreachable. The scripts keep snapshot, index cleanup,
    # and data-key deletion in one server-side step.
    if prefix:
        index_key = _state._tenant_prefix_index_key(tenant_id, prefix)
        global_index_key = _state._tenant_index_key(tenant_id)
        deleted = await _state.get_lua_manager().execute(
            client,
            "cache_tenant_prefix_drain",
            [index_key, global_index_key],
            [prefix],
        )
    else:
        index_key = _state._tenant_index_key(tenant_id)
        # S-14: the script falls back to the global index when the requested
        # shard is empty, matching the business prefix at colon boundaries.
        deleted = await _state.get_lua_manager().execute(
            client,
            "cache_tenant_global_drain",
            [index_key],
            [f"tenant_index:{tenant_id}:*"],
        )

    return int(deleted or 0)


def _tenant_index_key(tenant_id: int | str) -> str:
    """Get the Redis Set key that indexes all cache keys for a tenant.

    Args:
        tenant_id: Tenant identifier

    Returns
    -------
        Index set key like 'tenant_index:123'
    """
    return f"tenant_index:{tenant_id}"


def _tenant_prefix_index_key(tenant_id: int | str, prefix: str) -> str:
    """Get the Redis Set key that indexes tenant cache keys for a specific prefix.

    Args:
        tenant_id: Tenant identifier
        prefix: Cache key prefix

    Returns
    -------
        Index set key like 'tenant_index:123:user'
    """
    return f"tenant_index:{tenant_id}:{prefix}"


# P2-B: index sets default to a bounded expiry — a missing ttl must never
# leave a non-expiring index set behind (unbounded growth). Pass ttl=0/None
# explicitly to opt out for a caller-managed lifecycle.
#
# This is a DATA-key TTL, not an index TTL. ``index_ttl`` doubles it, so the
# default keeps an index set alive for 2h while the data it describes lives 1h —
# the rule ``cache/index_policy.py`` states and every other writer follows. The
# constant used to be 7200 and was passed straight to ``EXPIRE``, so the same
# number meant "index TTL" here and "data TTL" in ``set_tenant_cache``; the
# effective index lifetime is unchanged by stating it the other way round.
_DEFAULT_DATA_TTL = 3600


async def add_to_tenant_index(
    tenant_id: int | str,
    cache_key: str,
    ttl: int | None = _DEFAULT_DATA_TTL,
    *,
    prefix: str | None = None,
) -> bool:
    """Add a cache key to the tenant's index set.

    Should be called whenever a tenant-scoped key is written to cache.
    Adds to both the global tenant index and the prefix-sharded index.

    Args:
        tenant_id: Tenant identifier
        cache_key: The cache key to add to the index
        ttl: Time-to-live of the DATA key this entry describes, in seconds. The
            index sets are given ``index_ttl(ttl)`` (2x) so they always outlive
            the data: an index that expires first makes still-live keys
            unreachable, which is the stale-read hole P1-9 closed. Pass 0/None
            to opt out of the expiry for a caller-managed lifecycle.
            (Default: 1h of data -> 2h of index.)
        prefix: Business prefix used to choose the shard. Pass this for keys
            built by the cache decorator (which may be user-scoped). The legacy
            positional fallback only understands ``tenant:{tid}:{prefix}:...``
            keys and exists for callers that have not migrated.

    Returns
    -------
        True if added successfully, False if Redis unavailable
    """
    client = _state.client
    if client is None:
        return False

    index_key = _tenant_index_key(tenant_id)

    if prefix is None:
        # Legacy key format: tenant:{id}:{prefix}:...
        parts = cache_key.split(":", 3)
        if len(parts) >= 3 and parts[0] == "tenant":
            prefix = parts[2]

    prefix_index_key = _tenant_prefix_index_key(tenant_id, prefix) if prefix else None
    # P1-9: an index must outlive the data key. This writer previously used the
    # exact data TTL, which recreated the stale-read hole the other writers
    # avoid.
    index_expiry = index_ttl(ttl) if ttl else None

    async with client.pipeline(transaction=False) as pipe:
        pipe.sadd(index_key, cache_key)
        if index_expiry:
            pipe.expire(index_key, index_expiry)

        if prefix_index_key:
            pipe.sadd(prefix_index_key, cache_key)
            if index_expiry:
                pipe.expire(prefix_index_key, index_expiry)

        await pipe.execute()

    return True


async def remove_from_tenant_index(
    tenant_id: int | str,
    cache_key: str,
    *,
    prefix: str | None = None,
) -> bool:
    """Remove a cache key from the tenant's index set.

    Should be called when a specific tenant key is deleted.
    Removes from both global and sharded indexes.

    Args:
        tenant_id: Tenant identifier
        cache_key: The cache key to remove from the index
        prefix: Business prefix used to choose the shard. Pass this for keys
            built by the cache decorator (which may be user-scoped). The legacy
            positional fallback only understands ``tenant:{tid}:{prefix}:...``
            keys and exists for callers that have not migrated.

    Returns
    -------
        True if removed successfully, False if Redis unavailable
    """
    client = _state.client
    if client is None:
        return False

    index_key = _tenant_index_key(tenant_id)

    if prefix is None:
        parts = cache_key.split(":", 3)
        if len(parts) >= 3 and parts[0] == "tenant":
            prefix = parts[2]

    prefix_index_key = _tenant_prefix_index_key(tenant_id, prefix) if prefix else None

    async with client.pipeline(transaction=False) as pipe:
        pipe.srem(index_key, cache_key)
        if prefix_index_key:
            pipe.srem(prefix_index_key, cache_key)
        await pipe.execute()

    return True


async def set_tenant_cache(
    tenant_id: int | str,
    prefix: str,
    key_parts: tuple[str | int, ...],
    value: str,
    ttl: int = 3600,
) -> bool:
    """Set a tenant-scoped cache value with automatic index tracking.

    This is the preferred way to set tenant cache values as it automatically
    maintains the tenant key index for efficient invalidation.

    Args:
        tenant_id: Tenant identifier
        prefix: Cache key prefix (e.g., 'user', 'permissions')
        key_parts: Additional key parts
        value: Serialized value to cache
        ttl: Time-to-live in seconds (default: 1 hour)

    Returns
    -------
        True if set successfully, False if Redis unavailable
    """
    client = _state.client
    if client is None:
        return False

    cache_key = _state.tenant_cache_key(tenant_id, prefix, *key_parts)

    # Use pipeline for atomic set + index add
    async with client.pipeline(transaction=False) as pipe:
        pipe.setex(cache_key, ttl, value)
        pipe.sadd(_tenant_index_key(tenant_id), cache_key)
        pipe.expire(_tenant_index_key(tenant_id), index_ttl(ttl))

        # Also add to prefix-sharded index
        sharded_index_key = _tenant_prefix_index_key(tenant_id, prefix)
        pipe.sadd(sharded_index_key, cache_key)
        pipe.expire(sharded_index_key, index_ttl(ttl))
        await pipe.execute()

    return True


async def get_tenant_cache(
    tenant_id: int | str,
    prefix: str,
    *key_parts: str | int,
) -> str | None:
    """Get a tenant-scoped cache value.

    Args:
        tenant_id: Tenant identifier
        prefix: Cache key prefix
        *key_parts: Additional key parts

    Returns
    -------
        Cached value or None if not found
    """
    client = _state.client
    if client is None:
        return None

    cache_key = _state.tenant_cache_key(tenant_id, prefix, *key_parts)
    result: str | None = await client.get(cache_key)  # type: ignore[assignment]
    return result


async def delete_tenant_cache(
    tenant_id: int | str,
    prefix: str,
    *key_parts: str | int,
) -> bool:
    """Delete a specific tenant-scoped cache value.

    Also removes the key from the tenant index.

    Args:
        tenant_id: Tenant identifier
        prefix: Cache key prefix
        *key_parts: Additional key parts

    Returns
    -------
        True if deleted, False if Redis unavailable
    """
    client = _state.client
    if client is None:
        return False

    cache_key = _state.tenant_cache_key(tenant_id, prefix, *key_parts)
    prefix_index_key = _tenant_prefix_index_key(tenant_id, prefix)

    # Use pipeline for atomic delete + index remove
    async with client.pipeline(transaction=False) as pipe:
        pipe.delete(cache_key)
        pipe.srem(_tenant_index_key(tenant_id), cache_key)
        pipe.srem(prefix_index_key, cache_key)
        await pipe.execute()

    return True


async def migrate_tenant_cache_index(tenant_id: int | str | None = None) -> dict[str, int]:
    """Migrate existing tenant cache keys to the set-based index.

    This scans for existing tenant:* keys and adds them to the appropriate
    index sets. Run this once during deployment to populate indexes for
    existing cached data.

    Args:
        tenant_id: Optional specific tenant to migrate. If None, migrates all.

    Returns
    -------
        Dict with migration stats: {'tenants_migrated': N, 'keys_indexed': M}
    """
    client = _state.client
    if client is None:
        return {"tenants_migrated": 0, "keys_indexed": 0}

    pattern = f"tenant:{tenant_id}:*" if tenant_id else "tenant:*"
    tenants_seen: set[str] = set()
    keys_indexed = 0
    cursor = 0
    iterations = 0

    while True:
        iterations += 1
        if iterations > _state.MAX_SCAN_ITERATIONS:
            # Prevent infinite loops if scan keeps returning cursor
            _state.stampede_logger.warning(
                "SCAN iteration limit reached in migrate_tenant_cache_index",
                extra={"pattern": pattern, "iterations": _state.MAX_SCAN_ITERATIONS},
            )
            break

        cursor, keys = await client.scan(cursor, match=pattern, count=100)
        if keys:
            # P1 FIX (code-review): keys are bytes when decode_responses=False;
            # str.split on bytes raises TypeError and mixed-type sadd corrupts
            # the index. Decode first. Pipeline acquisition handles both the
            # real client (sync factory returning an async-CM) and test
            # doubles (AsyncMock whose call returns a coroutine).
            import inspect

            pipe_obj = client.pipeline(transaction=False)
            if inspect.isawaitable(pipe_obj):
                pipe_obj = await pipe_obj
            async with pipe_obj as pipe:
                for key in keys:
                    key_str = key.decode("utf-8", errors="replace") if isinstance(key, bytes) else str(key)
                    # Extract tenant_id from key (format: tenant:123:prefix:...)
                    parts = key_str.split(":", 2)
                    if len(parts) >= 2:
                        tid = parts[1]
                        tenants_seen.add(tid)
                        index_key = f"tenant_index:{tid}"

                        rest = parts[2]
                        rest_parts = rest.split(":", 1)
                        prefix = rest_parts[0]

                        pipe.sadd(index_key, key_str)
                        pipe.sadd(f"tenant_index:{tid}:{prefix}", key_str)
                        keys_indexed += 1
                await pipe.execute()
        if cursor == 0:
            break

    return {"tenants_migrated": len(tenants_seen), "keys_indexed": keys_indexed}


async def get_tenant_index_size(tenant_id: int | str) -> int:
    """Get the number of cached keys for a tenant.

    Useful for monitoring and debugging.

    Args:
        tenant_id: Tenant identifier

    Returns
    -------
        Number of keys in the tenant's index
    """
    client = _state.client
    if client is None:
        return 0

    index_key = _tenant_index_key(tenant_id)
    count: int = await client.scard(index_key)  # type: ignore[misc, assignment]
    return count
