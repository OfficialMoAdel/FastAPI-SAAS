import asyncio
import functools
import random
import re
import time
from collections.abc import AsyncGenerator, Callable
from typing import Any

from fastapi import Request, Response
from redis.asyncio import ConnectionPool, Redis

# Global set to hold strong references to background tasks (prevents GC of running tasks)
_background_tasks: set[asyncio.Task] = set()

from src.app.core.config import settings
from src.app.core.exceptions.cache_exceptions import (
    CacheIdentificationInferenceError,
    InvalidRequestError,
    MissingClientError,
)
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.monitoring.r1_unified_metrics import record_xfetch_latency
from src.app.core.utils.lua_manager import get_lua_manager

pool: ConnectionPool | None = None
client: Redis | None = None

# =============================================================================
# U5: Pre-compiled regex patterns for cache decorator (performance optimization)
# =============================================================================
_BRACKET_PATTERN = re.compile(r"{(.*?)}")
MAX_SCAN_ITERATIONS = 1000

# Standard library logger
logger = get_logger(__name__)

# Structlog logger for stampede
stampede_logger: Any = get_logger("src.app.core.utils.cache.stampede")


# =============================================================================
# Tenant-Scoped Cache Utilities
# =============================================================================


def tenant_cache_key(tenant_id: int | str, prefix: str, *key_parts: str | int) -> str:
    """Build a tenant-scoped cache key.

    Args:
        tenant_id: Tenant identifier
        prefix: Cache key prefix (e.g., 'user', 'permissions')
        *key_parts: Additional key parts

    Returns
    -------
        Tenant-scoped cache key like 'tenant:123:user:456'

    Example:
        >>> tenant_cache_key(1, "user", 42)
        'tenant:1:user:42'
        >>> tenant_cache_key(1, "permissions", "admin")
        'tenant:1:permissions:admin'
    """
    parts = [f"tenant:{tenant_id}", prefix]
    parts.extend(str(p) for p in key_parts)
    return ":".join(parts)


def tenant_cache_pattern(tenant_id: int | str, prefix: str = "*") -> str:
    """Build a pattern for tenant-scoped cache keys.

    Args:
        tenant_id: Tenant identifier
        prefix: Cache key prefix pattern (default: '*' for all)

    Returns
    -------
        Pattern like 'tenant:123:*' for SCAN/KEYS operations

    Example:
        >>> tenant_cache_pattern(1)
        'tenant:1:*'
        >>> tenant_cache_pattern(1, "user")
        'tenant:1:user:*'
    """
    return f"tenant:{tenant_id}:{prefix}:*" if prefix != "*" else f"tenant:{tenant_id}:*"


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
    if client is None:
        return 0

    # P1-1: Hierarchical Tenant Index Sharding
    # If prefix is provided, use the sharded index instead of filtering the global index
    if prefix:
        index_key = _tenant_prefix_index_key(tenant_id, prefix)
        global_index_key = _tenant_index_key(tenant_id)
    else:
        index_key = _tenant_index_key(tenant_id)
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
    if client is None:
        return False

    cache_key = tenant_cache_key(tenant_id, prefix, *key_parts)

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
    if client is None:
        return None

    cache_key = tenant_cache_key(tenant_id, prefix, *key_parts)
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
    if client is None:
        return False

    cache_key = tenant_cache_key(tenant_id, prefix, *key_parts)
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
    if client is None:
        return {"tenants_migrated": 0, "keys_indexed": 0}

    pattern = f"tenant:{tenant_id}:*" if tenant_id else "tenant:*"
    tenants_seen: set[str] = set()
    keys_indexed = 0
    cursor = 0
    iterations = 0

    while True:
        iterations += 1
        if iterations > MAX_SCAN_ITERATIONS:
            # Prevent infinite loops if scan keeps returning cursor
            stampede_logger.warning(
                "SCAN iteration limit reached in migrate_tenant_cache_index",
                extra={"pattern": pattern, "iterations": MAX_SCAN_ITERATIONS},
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
    if client is None:
        return 0

    index_key = _tenant_index_key(tenant_id)
    count: int = await client.scard(index_key)  # type: ignore[misc, assignment]
    return count


# =============================================================================
# Cache Stampede Protection
# =============================================================================

import asyncio

from structlog import get_logger


async def get_with_stampede_protection(
    key: str,
    fetch_func: Callable[[], Any],
    ttl: int = 3600,
    beta: float = 1.0,
) -> Any:
    """Get cached value with probabilistic early expiration (XFetch algorithm).

    Prevents cache stampede by probabilistically refreshing cache before
    expiration, spreading out refresh requests across time.

    Uses atomic Lua script for GET+TTL when R1_XFETCH_USE_LUA_SCRIPT=true,
    reducing Redis roundtrips from 2 to 1 (~50% latency improvement).

    Args:
        key: Cache key
        fetch_func: Async function to fetch fresh data if cache miss
        ttl: Time-to-live in seconds (default: 1 hour)
        beta: Tuning parameter for early refresh probability (higher = earlier refresh)

    Returns
    -------
        Cached or freshly fetched value

    Example:
        data = await get_with_stampede_protection(
            "user:123:profile",
            lambda: fetch_user_profile(123),
            ttl=3600,
        )
    """
    if client is None:
        return await fetch_func()

    # Get cached value with TTL info
    start_time = time.perf_counter()

    if settings.XFETCH_USE_LUA_SCRIPT:
        # Optimized path: Single Lua script for GET + TTL (1 roundtrip)
        try:
            lua_manager = get_lua_manager()
            result = await lua_manager.execute(
                client,
                "xfetch_get_with_ttl",
                keys=[key],
                args=[],
            )

            latency = time.perf_counter() - start_time
            record_xfetch_latency(latency, is_hit=result[0] is not None)

            # Unpack result: [value, ttl]
            cached = result[0]
            remaining_ttl = int(result[1]) if result[1] is not None else -2

            # Handle bytes → string conversion
            if cached is not None and isinstance(cached, bytes):
                cached = cached.decode("utf-8")

        except Exception as e:
            # Fallback to original implementation on Lua failure
            stampede_logger.warning(
                "XFetch Lua script failed, using fallback",
                error=str(e),
                key=key,
            )
            # Fall through to original implementation
            cached = await client.get(key)
            remaining_ttl = await client.ttl(key)
            latency = time.perf_counter() - start_time
            record_xfetch_latency(latency, is_hit=cached is not None)
    else:
        # Original path: 2 separate Redis calls
        cached = await client.get(key)
        remaining_ttl = await client.ttl(key)
        latency = time.perf_counter() - start_time
        record_xfetch_latency(latency, is_hit=cached is not None)

    if cached is not None and remaining_ttl > 0:
        # XFetch probabilistic early expiration
        # As TTL approaches 0, probability of refresh increases
        delta = ttl - remaining_ttl  # Time since cache was set
        if delta > 0:
            # Probability increases as we approach expiration
            probability = min(beta * (delta / ttl) ** 2, 1.0)

            if random.random() < probability:
                # P3-1: Add background refresh lock to prevent stampede
                # Only one process should trigger the background refresh
                lock_key = f"{key}:refresh_lock"
                # Try to acquire lock for 10s (short duration just for the trigger)
                # nx=True ensures we only get it if it doesn't exist
                lock_acquired = await client.set(lock_key, "1", nx=True, ex=10)

                if lock_acquired:
                    stampede_logger.debug(
                        "Cache early refresh triggered",
                        key=key,
                        remaining_ttl=remaining_ttl,
                        probability=probability,
                    )
                    # Refresh in background, return stale data
                    # Pass lock_key to cleanup after refresh
                    # Track task to prevent GC while running
                    task = asyncio.create_task(_refresh_cache_with_lock(key, fetch_func, ttl, lock_key))
                    _background_tasks.add(task)
                    task.add_done_callback(_background_tasks.discard)
                else:
                    stampede_logger.debug(
                        "Cache early refresh skipped - already in progress",
                        key=key,
                    )

        return json_loads(cached)

    # Cache miss - fetch and cache
    return await _refresh_cache(key, fetch_func, ttl)


async def _refresh_cache_with_lock(key: str, fetch_func: Callable[[], Any], ttl: int, lock_key: str) -> Any:
    """Refresh cache and cleanup lock."""
    try:
        return await _refresh_cache(key, fetch_func, ttl)
    finally:
        if client:
            # Release the lock so subsequent refreshes can happen
            await client.delete(lock_key)


async def _refresh_cache(key: str, fetch_func: Callable[[], Any], ttl: int) -> Any:
    """Refresh cache with fresh data."""
    if client is None:
        return await fetch_func()

    data = await fetch_func()
    # U4: orjson handles datetime, UUID, Pydantic natively - skip jsonable_encoder
    serialized = json_dumps(data)
    await client.setex(key, ttl, serialized)
    return data


async def get_with_lock(
    key: str,
    fetch_func: Callable[[], Any],
    ttl: int = 3600,
    lock_timeout: int = 10,
    base_delay: float = 0.05,
    max_delay: float = 1.0,
    jitter: float = 0.25,
) -> Any:
    """Get cached value with lock-based stampede protection.

    Uses a distributed lock to ensure only one process refreshes the cache
    at a time. Other processes wait for the lock holder to complete.

    Best for high-contention keys where data is expensive to compute.

    Args:
        key: Cache key
        fetch_func: Async function to fetch fresh data if cache miss
        ttl: Time-to-live in seconds
        lock_timeout: Maximum time to hold lock in seconds
        base_delay: Initial wait delay in seconds (default: 50ms)
        max_delay: Maximum wait delay in seconds (default: 1s)
        jitter: Jitter factor (0-1) to randomize wait time (default: 0.25)

    Returns
    -------
        Cached or freshly fetched value
    """
    if client is None:
        return await fetch_func()

    # Try cache first
    cached = await client.get(key)
    if cached is not None:
        return json_loads(cached)

    # Cache miss - try to acquire lock
    lock_key = f"{key}:lock"
    lock_acquired = await client.set(lock_key, "1", nx=True, ex=lock_timeout)

    if lock_acquired:
        try:
            # We got the lock - fetch and cache
            data = await fetch_func()
            # U4: orjson handles datetime, UUID, Pydantic natively - skip jsonable_encoder
            serialized = json_dumps(data)
            await client.setex(key, ttl, serialized)
            return data
        finally:
            await client.delete(lock_key)
    else:
        # Another process has the lock - wait and retry with exponential backoff
        elapsed = 0.0
        delay = base_delay

        while elapsed < lock_timeout:
            # Add jitter to prevent thundering herd on lock release
            # Randomize delay by +/- jitter factor
            actual_delay = delay * (1 + random.uniform(-jitter, jitter))

            await asyncio.sleep(actual_delay)
            elapsed += actual_delay

            cached = await client.get(key)
            if cached is not None:
                return json_loads(cached)

            # Exponential backoff: double the delay, capped at max_delay
            delay = min(delay * 2, max_delay)

        # Lock timeout - fetch anyway
        stampede_logger.warning("Cache lock timeout, fetching anyway", key=key)
        return await fetch_func()


def _infer_resource_id(kwargs: dict[str, Any], resource_id_type: type | tuple[type, ...]) -> int | str:
    """Infer the resource ID from a dictionary of keyword arguments.

    Parameters
    ----------
    kwargs: Dict[str, Any]
        A dictionary of keyword arguments.
    resource_id_type: Union[type, Tuple[type, ...]]
        The expected type of the resource ID, which can be integer (int) or a string (str).

    Returns
    -------
    Union[None, int, str]
        The inferred resource ID. If it cannot be inferred or does not match the expected type, it returns None.

    Note
    ----
        - When `resource_id_type` is `int`, the function looks for an argument with the key 'id'.
        - When `resource_id_type` is `str`, it attempts to infer the resource ID as a string.
    """
    resource_id: int | str | None = None
    for arg_name, arg_value in kwargs.items():
        if isinstance(arg_value, resource_id_type):
            resource_id = arg_value
            break

    if resource_id is None:
        raise CacheIdentificationInferenceError

    return resource_id


def _extract_data_inside_brackets(input_string: str) -> list[str]:
    """Extract data inside curly brackets from a given string using regular expressions.

    Parameters
    ----------
    input_string: str
        The input string in which to find data enclosed within curly brackets.

    Returns
    -------
    List[str]
        A list of strings containing the data found inside the curly brackets within the input string.

    Example
    -------
    >>> _extract_data_inside_brackets("The {quick} brown {fox} jumps over the {lazy} dog.")
    ['quick', 'fox', 'lazy']
    """
    # U5: Use pre-compiled pattern for better performance
    return _BRACKET_PATTERN.findall(input_string)


def _construct_data_dict(data_inside_brackets: list[str], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Construct a dictionary based on data inside brackets and keyword arguments."""
    data_dict = {}
    for key in data_inside_brackets:
        data_dict[key] = kwargs.get(key, f"_{key}_missing_")
    return data_dict


def _format_prefix(prefix: str, kwargs: dict[str, Any]) -> str:
    """Format a prefix using keyword arguments.

    Parameters
    ----------
    prefix: str
        The prefix template to be formatted.
    kwargs: Dict[str, Any]
        A dictionary of keyword arguments.

    Returns
    -------
    str: The formatted prefix.
    """
    data_inside_brackets = _extract_data_inside_brackets(prefix)
    data_dict = _construct_data_dict(data_inside_brackets, kwargs)
    formatted_prefix = prefix.format(**data_dict)
    return formatted_prefix


def _format_extra_data(to_invalidate_extra: dict[str, str], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Format extra data based on provided templates and keyword arguments.

    This function takes a dictionary of templates and their associated values and a dictionary of keyword arguments.
    It formats the templates with the corresponding values from the keyword arguments and returns a dictionary
    where keys are the formatted templates and values are the associated keyword argument values.

    Parameters
    ----------
    to_invalidate_extra: Dict[str, str]
        A dictionary where keys are templates and values are the associated values.
    kwargs: Dict[str, Any]
        A dictionary of keyword arguments.

    Returns
    -------
        Dict[str, Any]: A dictionary where keys are formatted templates and values
        are associated keyword argument values.
    """
    formatted_extra = {}
    for prefix, id_template in to_invalidate_extra.items():
        formatted_prefix = _format_prefix(prefix, kwargs)
        id = _extract_data_inside_brackets(id_template)[0]
        formatted_extra[formatted_prefix] = kwargs[id]

    return formatted_extra


async def _delete_keys_by_pattern(pattern: str) -> None:
    """Delete keys from Redis that match a given pattern using the SCAN command.

    This function iteratively scans the Redis key space for keys that match a specific pattern
    and deletes them. It uses the SCAN command to efficiently find keys, which is more
    performance-friendly compared to the KEYS command, especially for large datasets.

    The function scans the key space in an iterative manner using a cursor-based approach.
    It retrieves a batch of keys matching the pattern on each iteration and deletes them
    until no matching keys are left.

    Parameters
    ----------
    pattern: str
        The pattern to match keys against. The pattern can include wildcards,
        such as '*' for matching any character sequence. Example: 'user:*'

    Notes
    -----
    - The SCAN command is used with a count of 100 to retrieve keys in batches.
      This count can be adjusted based on the size of your dataset and Redis performance.

    - The function uses the delete command to remove keys in bulk. If the dataset
      is extremely large, consider implementing additional logic to handle bulk deletion
      more efficiently.

    - Be cautious with patterns that could match a large number of keys, as deleting
      many keys simultaneously may impact the performance of the Redis server.
    """
    if client is None:
        return

    cursor = 0
    iterations = 0
    while True:
        iterations += 1
        if iterations > MAX_SCAN_ITERATIONS:
            stampede_logger.warning(
                "SCAN iteration limit reached in _delete_keys_by_pattern",
                extra={"pattern": pattern, "iterations": MAX_SCAN_ITERATIONS},
            )
            break

        cursor, keys = await client.scan(cursor, match=pattern, count=100)
        if keys:
            await client.delete(*keys)
        if cursor == 0:
            break


def cache(
    key_prefix: str,
    resource_id_name: Any = None,
    expiration: int = 3600,
    resource_id_type: type | tuple[type, ...] = int,
    to_invalidate_extra: dict[str, Any] | None = None,
    pattern_to_invalidate_extra: list[str] | None = None,
    invalidation_patterns: list[str] | None = None,
) -> Callable:
    """Cache decorator for FastAPI endpoints.

    This decorator enables caching the results of FastAPI endpoint functions to improve response times
    and reduce the load on the application by storing and retrieving data in a cache.

    Parameters
    ----------
    key_prefix: str
        A unique prefix to identify the cache key.
    resource_id_name: Any, optional
        The name of the resource ID argument in the decorated function. If provided, it is used directly;
        otherwise, the resource ID is inferred from the function's arguments.
    expiration: int, optional
        The expiration time for the cached data in seconds. Defaults to 3600 seconds (1 hour).
    resource_id_type: Union[type, Tuple[type, ...]], default int
        The expected type of the resource ID.
        This can be a single type (e.g., int) or a tuple of types (e.g., (int, str)).
        Defaults to int. This is used only if resource_id_name is not provided.
    to_invalidate_extra: Dict[str, Any] | None, optional
        A dictionary where keys are cache key prefixes and values are templates for cache key suffixes.
        These keys are invalidated when the decorated function is called with a method other than GET.
    pattern_to_invalidate_extra: List[str] | None, optional
        A list of string patterns for cache keys that should be invalidated when the decorated function is called.
        This allows for bulk invalidation of cache keys based on a matching pattern.
    invalidation_patterns: List[str] | None, optional
        A list of patterns to register this cache key against for O(1) invalidation.
        When this endpoint is called (GET), the cache key will be added to the index sets for these patterns.
        This allows future O(1) invalidation using `invalidate_by_pattern`.

    Returns
    -------
    Callable
        A decorator function that can be applied to FastAPI endpoint functions.

    Example usage
    -------------

    ```python
    from fastapi import FastAPI, Request
    from my_module import cache  # Replace with your actual module and imports

    app = FastAPI()

    # Define a sample endpoint with caching
    @app.get("/sample/{resource_id}")
    @cache(key_prefix="sample_data", expiration=3600, resource_id_type=int)
    async def sample_endpoint(request: Request, resource_id: int):
        # Your endpoint logic here
        return {"data": "your_data"}
    ```

    This decorator caches the response data of the endpoint function using a unique cache key.
    The cached data is retrieved for GET requests, and the cache is invalidated for other types of requests.

    Advanced Example Usage
    -------------
    ```python
    from fastapi import FastAPI, Request
    from my_module import cache

    app = FastAPI()


    @app.get("/users/{user_id}/items")
    @cache(key_prefix="user_items", resource_id_name="user_id", expiration=1200)
    async def read_user_items(request: Request, user_id: int):
        # Endpoint logic to fetch user's items
        return {"items": "user specific items"}


    @app.put("/items/{item_id}")
    @cache(
        key_prefix="item_data",
        resource_id_name="item_id",
        to_invalidate_extra={"user_items": "{user_id}"},
        pattern_to_invalidate_extra=["user_*_items:*"],
    )
    async def update_item(request: Request, item_id: int, data: dict, user_id: int):
        # Update logic for an item
        # Invalidate both the specific item cache and all user-specific item lists
        return {"status": "updated"}
    ```

    In this example:
    - When reading user items, the response is cached under a key formed with 'user_items' prefix and 'user_id'.
    - When updating an item, the cache for this specific item (under 'item_data:item_id') and all caches with keys
      starting with 'user_{user_id}_items:' are invalidated. The `to_invalidate_extra` parameter specifically targets
      the cache for user-specific item lists, while `pattern_to_invalidate_extra` allows bulk invalidation of all keys
      matching the pattern 'user_*_items:*', covering all users.

    Note
    ----
    - resource_id_type is used only if resource_id is not passed.
    - `to_invalidate_extra` and `pattern_to_invalidate_extra` are used for cache invalidation on methods other than GET.
    - Using `pattern_to_invalidate_extra` can be resource-intensive on large datasets. Use it judiciously and
      consider the potential impact on Redis performance.
    """

    def wrapper(func: Callable) -> Callable:
        @functools.wraps(func)
        async def inner(request: Request, *args: Any, **kwargs: Any) -> Any:
            if client is None:
                raise MissingClientError

            if resource_id_name:
                resource_id = kwargs[resource_id_name]
            else:
                resource_id = _infer_resource_id(kwargs=kwargs, resource_id_type=resource_id_type)

            formatted_key_prefix = _format_prefix(key_prefix, kwargs)
            cache_key = f"{formatted_key_prefix}:{resource_id}"
            if request.method == "GET":
                if to_invalidate_extra is not None or pattern_to_invalidate_extra is not None:
                    raise InvalidRequestError

                cached_data = await client.get(cache_key)
                if cached_data:
                    # P6: Skip re-parse - return raw JSON bytes directly
                    return Response(content=cached_data, media_type="application/json")

            result = await func(request, *args, **kwargs)

            if request.method == "GET":
                # U4: orjson handles datetime, UUID, Pydantic natively - skip jsonable_encoder
                serialized_data = json_dumps(result)

                # Use pipeline for atomic set + index registration
                async with client.pipeline(transaction=False) as pipe:
                    pipe.setex(cache_key, expiration, serialized_data)

                    # Pattern-based invalidation registration
                    if invalidation_patterns:
                        formatted_patterns = [_format_prefix(p, kwargs) for p in invalidation_patterns]
                        for pattern in formatted_patterns:
                            idx_key = _pattern_index_key(pattern)
                            pipe.sadd(idx_key, cache_key)
                            pipe.expire(idx_key, expiration)

                    # ISSUE 2 FIX: Register tenant-scoped keys for invalidate_tenant_cache()
                    if cache_key.startswith("tenant:"):
                        parts = cache_key.split(":", 3)
                        if len(parts) >= 2:
                            tenant_id = parts[1]
                            pipe.sadd(_tenant_index_key(tenant_id), cache_key)
                            pipe.expire(_tenant_index_key(tenant_id), expiration * 2)

                            # Also add to prefix-sharded index
                            if len(parts) >= 3:
                                prefix = parts[2]
                                sharded_idx_key = _tenant_prefix_index_key(tenant_id, prefix)
                                pipe.sadd(sharded_idx_key, cache_key)
                                pipe.expire(sharded_idx_key, expiration * 2)

                    await pipe.execute()

                return result  # Return original result, not re-parsed

            else:
                # OPT-6: Pipeline cache invalidation (N deletes → 1 roundtrip)
                keys_to_delete = [cache_key]
                if to_invalidate_extra is not None:
                    formatted_extra = _format_extra_data(to_invalidate_extra, kwargs)
                    for prefix, id in formatted_extra.items():
                        keys_to_delete.append(f"{prefix}:{id}")

                if keys_to_delete:
                    async with client.pipeline() as pipe:
                        for key in keys_to_delete:
                            pipe.delete(key)
                        await pipe.execute()

                if pattern_to_invalidate_extra is not None:
                    for pattern in pattern_to_invalidate_extra:
                        formatted_pattern = _format_prefix(pattern, kwargs)
                        # OPT-8: Use O(1) pattern invalidation
                        # This avoids the O(N) SCAN operation on the Redis database.
                        await invalidate_by_pattern(formatted_pattern)

            return result

        return inner

    return wrapper


# =============================================================================
# Pattern-Based Cache Invalidation (O(1))
# =============================================================================


def _pattern_index_key(pattern: str) -> str:
    """Generate Redis key for pattern index set.

    Replaces '*' with '_STAR_' to create a safe static key name.
    """
    safe_pattern = pattern.replace("*", "_STAR_")
    return f"idx_pattern:{safe_pattern}"


async def register_key_patterns(
    key: str,
    patterns: list[str],
    ttl: int,
) -> None:
    """Register a cache key against multiple patterns for O(1) invalidation.

    Args:
        key: The cache key being stored (e.g., 'user:123:profile')
        patterns: List of patterns this key belongs to (e.g., ['user:*:profile'])
        ttl: Time-to-live for the index entries (should match key TTL)
    """
    if client is None or not patterns:
        return

    async with client.pipeline(transaction=False) as pipe:
        for pattern in patterns:
            idx_key = _pattern_index_key(pattern)
            pipe.sadd(idx_key, key)
            # Set TTL on index key to ensure it expires if not explicitly invalidated
            pipe.expire(idx_key, ttl)
        await pipe.execute()


async def invalidate_by_pattern(pattern: str) -> int:
    """Invalidate all keys matching a pattern using the O(1) index set.

    Instead of SCAN (O(N)), this looks up the set of keys associated with
    the pattern and deletes them directly (O(M)).

    Args:
        pattern: The pattern to invalidate (e.g., 'user:*:profile')

    Returns
    -------
        Number of keys deleted
    """
    if client is None:
        return 0

    idx_key = _pattern_index_key(pattern)
    # Get all keys registered for this pattern
    keys = await client.smembers(idx_key)  # type: ignore[misc]

    if not keys:
        return 0

    async with client.pipeline(transaction=False) as pipe:
        # Delete the actual cached keys
        pipe.delete(*keys)
        # Delete the index set itself (it's done its job)
        pipe.delete(idx_key)
        results = await pipe.execute()

    # First result is number of keys deleted by delete(*keys)
    return results[0] if results else 0


async def set_cache_with_patterns(
    key: str,
    value: Any,
    ttl: int = 3600,
    patterns: list[str] | None = None,
) -> bool:
    """Set cache value and register it for pattern-based invalidation.

    Args:
        key: Cache key
        value: Value to cache
        ttl: TTL in seconds
        patterns: List of patterns to register this key against

    Returns
    -------
        True if successful
    """
    if client is None:
        return False

    serialized = json_dumps(value)

    async with client.pipeline(transaction=False) as pipe:
        pipe.setex(key, ttl, serialized)
        if patterns:
            for pattern in patterns:
                idx_key = _pattern_index_key(pattern)
                pipe.sadd(idx_key, key)
                pipe.expire(idx_key, ttl)
        await pipe.execute()

    return True


async def async_get_redis() -> AsyncGenerator[Redis, None]:
    """Get the shared Redis client from the pool.

    Note: The module-level `client` is initialized at startup and reuses
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
    if client is None:
        raise MissingClientError("Redis client not initialized")
    yield client
