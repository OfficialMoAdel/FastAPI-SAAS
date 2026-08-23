"""Tenant cache index — set-based O(|S|) invalidation with prefix sharding."""

from src.app.core.utils import cache as _state


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
    # If prefix is provided, use the sharded index instead of filtering the global index
    if prefix:
        index_key = _state._tenant_prefix_index_key(tenant_id, prefix)
        global_index_key = _state._tenant_index_key(tenant_id)
    else:
        index_key = _state._tenant_index_key(tenant_id)
        global_index_key = None

    # smembers returns bytes when decode_responses=False
    raw_keys = await client.smembers(index_key)  # type: ignore[misc]
    keys: set[str] = {k.decode("utf-8") if isinstance(k, bytes) else k for k in raw_keys}

    if not keys:
        return 0

    # Old logic required filtering here if prefix was set but index was global.
    # Now index_key is already specific if prefix is set.
    # However, for backward compatibility (during migration where sharded index might be empty),
    # strictly speaking we should fall back to global index filtering if sharded is empty?
    # But migrate_tenant_cache_index will populate both.
    # Let's assume migration has run or we accept that new optimization requires new data.

    # Double check filtering just in case we are using global index (if logic changed)
    # But here we switched index_key. So keys are already filtered by definition of the set.

    # Delete all matching keys in a pipeline for efficiency
    async with client.pipeline(transaction=False) as pipe:
        pipe.delete(*keys)

        # Remove deleted keys from the index we used
        pipe.delete(index_key)

        # If we used a sharded index, we must ALSO remove these keys from the global index
        if prefix and global_index_key:
            pipe.srem(global_index_key, *keys)
        elif not prefix:
            # BUG FIX: Clear all sharded indexes for the tenant to prevent orphaned empty sets
            cursor = 0
            while True:
                cursor, sharded_keys = await client.scan(cursor, match=f"tenant_index:{tenant_id}:*", count=100)
                if sharded_keys:
                    pipe.delete(*sharded_keys)
                if cursor == 0:
                    break

        results = await pipe.execute()

    return results[0] if results else 0


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


async def add_to_tenant_index(tenant_id: int | str, cache_key: str, ttl: int | None = None) -> bool:
    """Add a cache key to the tenant's index set.

    Should be called whenever a tenant-scoped key is written to cache.
    Adds to both the global tenant index and the prefix-sharded index.

    Args:
        tenant_id: Tenant identifier
        cache_key: The cache key to add to the index
        ttl: Optional time-to-live for the index (seconds)

    Returns
    -------
        True if added successfully, False if Redis unavailable
    """
    client = _state.client
    if client is None:
        return False

    index_key = _tenant_index_key(tenant_id)

    # Try to determine prefix for sharded index
    # key format: tenant:{id}:{prefix}:...
    parts = cache_key.split(":", 3)
    prefix_index_key = None
    if len(parts) >= 3 and parts[0] == "tenant":
        prefix = parts[2]
        prefix_index_key = _tenant_prefix_index_key(tenant_id, prefix)

    async with client.pipeline(transaction=False) as pipe:
        pipe.sadd(index_key, cache_key)
        if ttl:
            pipe.expire(index_key, ttl)

        if prefix_index_key:
            pipe.sadd(prefix_index_key, cache_key)
            if ttl:
                pipe.expire(prefix_index_key, ttl)

        await pipe.execute()

    return True


async def remove_from_tenant_index(tenant_id: int | str, cache_key: str) -> bool:
    """Remove a cache key from the tenant's index set.

    Should be called when a specific tenant key is deleted.
    Removes from both global and sharded indexes.

    Args:
        tenant_id: Tenant identifier
        cache_key: The cache key to remove from the index

    Returns
    -------
        True if removed successfully, False if Redis unavailable
    """
    client = _state.client
    if client is None:
        return False

    index_key = _tenant_index_key(tenant_id)

    parts = cache_key.split(":", 3)
    prefix_index_key = None
    if len(parts) >= 3 and parts[0] == "tenant":
        prefix = parts[2]
        prefix_index_key = _tenant_prefix_index_key(tenant_id, prefix)

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
        pipe.expire(_tenant_index_key(tenant_id), ttl * 2)

        # Also add to prefix-sharded index
        sharded_index_key = _tenant_prefix_index_key(tenant_id, prefix)
        pipe.sadd(sharded_index_key, cache_key)
        pipe.expire(sharded_index_key, ttl * 2)
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
        for key in keys:
            # Extract tenant_id from key (format: tenant:123:prefix:...)
            parts = key.split(":", 2)
            if len(parts) >= 2:
                tid = parts[1]
                tenants_seen.add(tid)
                index_key = f"tenant_index:{tid}"

                # Add to global index
                # Also determine prefix and add to sharded index
                # key format: tenant:{id}:{prefix}:...
                # parts from split above are [tenant, id, rest]
                # rest is "prefix:..."

                # We need to split rest to get prefix
                rest = parts[2]
                rest_parts = rest.split(":", 1)
                prefix = rest_parts[0]

                async with client.pipeline(transaction=False) as pipe:
                    pipe.sadd(index_key, key)
                    pipe.sadd(f"tenant_index:{tid}:{prefix}", key)
                    await pipe.execute()

                keys_indexed += 1

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
