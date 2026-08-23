"""Cache stampede protection — XFetch probabilistic early expiration + locks."""

import time
from collections.abc import Callable
from typing import Any

from src.app.core.utils import cache as _state


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
    client = _state.client
    if client is None:
        return await fetch_func()

    # Get cached value with TTL info
    start_time = time.perf_counter()

    if _state.settings.XFETCH_USE_LUA_SCRIPT:
        # Optimized path: Single Lua script for GET + TTL (1 roundtrip)
        try:
            lua_manager = _state.get_lua_manager()
            result = await lua_manager.execute(
                client,
                "xfetch_get_with_ttl",
                keys=[key],
                args=[],
            )

            latency = time.perf_counter() - start_time
            _state.record_xfetch_latency(latency, is_hit=result[0] is not None)

            # Unpack result: [value, ttl]
            cached = result[0]
            remaining_ttl = int(result[1]) if result[1] is not None else -2

            # Handle bytes → string conversion
            if cached is not None and isinstance(cached, bytes):
                cached = cached.decode("utf-8")

        except Exception as e:
            # Fallback to original implementation on Lua failure
            _state.stampede_logger.warning(
                "XFetch Lua script failed, using fallback",
                error=str(e),
                key=key,
            )
            # Fall through to original implementation
            cached = await client.get(key)
            remaining_ttl = await client.ttl(key)
            latency = time.perf_counter() - start_time
            _state.record_xfetch_latency(latency, is_hit=cached is not None)
    else:
        # Original path: 2 separate Redis calls
        cached = await client.get(key)
        remaining_ttl = await client.ttl(key)
        latency = time.perf_counter() - start_time
        _state.record_xfetch_latency(latency, is_hit=cached is not None)

    if cached is not None and remaining_ttl > 0:
        # XFetch probabilistic early expiration
        # As TTL approaches 0, probability of refresh increases
        delta = ttl - remaining_ttl  # Time since cache was set
        if delta > 0:
            # Probability increases as we approach expiration
            probability = min(beta * (delta / ttl) ** 2, 1.0)

            if _state.random.random() < probability:
                # P3-1: Add background refresh lock to prevent stampede
                # Only one process should trigger the background refresh
                lock_key = f"{key}:refresh_lock"
                # Try to acquire lock for 10s (short duration just for the trigger)
                # nx=True ensures we only get it if it doesn't exist
                lock_acquired = await client.set(lock_key, "1", nx=True, ex=5)

                if lock_acquired:
                    _state.stampede_logger.debug(
                        "Cache early refresh triggered",
                        key=key,
                        remaining_ttl=remaining_ttl,
                        probability=probability,
                    )
                    # Refresh in background, return stale data
                    # Pass lock_key to cleanup after refresh
                    # Track task to prevent GC while running
                    task = _state.asyncio.create_task(_refresh_cache_with_lock(key, fetch_func, ttl, lock_key))
                    _state._background_tasks.add(task)
                    task.add_done_callback(_state._background_tasks.discard)
                else:
                    _state.stampede_logger.debug(
                        "Cache early refresh skipped - already in progress",
                        key=key,
                    )

        return _state.json_loads(cached)

    # Cache miss - use lock-based fetch to prevent thundering herd
    lock_key = f"{key}:refresh_lock"
    lock_acquired = await client.set(lock_key, "1", nx=True, ex=5)
    if lock_acquired:
        try:
            return await _refresh_cache(key, fetch_func, ttl)
        finally:
            await client.delete(lock_key)
    else:
        # Wait and retry with simple polling
        for _ in range(20):
            await _state.asyncio.sleep(0.1)
            cached = await client.get(key)
            if cached is not None:
                return _state.json_loads(cached)
        # Timeout waiting for lock - fetch anyway
        _state.stampede_logger.warning("Cache lock timeout on full miss, fetching anyway", key=key)
        return await fetch_func()


async def _refresh_cache_with_lock(key: str, fetch_func: Callable[[], Any], ttl: int, lock_key: str) -> Any:
    """Refresh cache and cleanup lock."""
    try:
        return await _refresh_cache(key, fetch_func, ttl)
    finally:
        if _state.client:
            # Release the lock so subsequent refreshes can happen
            await _state.client.delete(lock_key)


async def _refresh_cache(key: str, fetch_func: Callable[[], Any], ttl: int) -> Any:
    """Refresh cache with fresh data."""
    client = _state.client
    if client is None:
        return await fetch_func()

    data = await fetch_func()
    # U4: orjson handles datetime, UUID, Pydantic natively - skip jsonable_encoder
    serialized = _state.json_dumps(data)
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
    client = _state.client
    if client is None:
        return await fetch_func()

    # Try cache first
    cached = await client.get(key)
    if cached is not None:
        return _state.json_loads(cached)

    # Cache miss - try to acquire lock
    lock_key = f"{key}:lock"
    lock_acquired = await client.set(lock_key, "1", nx=True, ex=lock_timeout)

    if lock_acquired:
        try:
            # Double-check cache after acquiring lock
            cached = await client.get(key)
            if cached is not None:
                return _state.json_loads(cached)

            # We got the lock - fetch and cache
            data = await fetch_func()
            # U4: orjson handles datetime, UUID, Pydantic natively - skip jsonable_encoder
            serialized = _state.json_dumps(data)
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
            actual_delay = delay * (1 + _state.random.uniform(-jitter, jitter))

            await _state.asyncio.sleep(actual_delay)
            elapsed += actual_delay

            cached = await client.get(key)
            if cached is not None:
                return _state.json_loads(cached)

            # Exponential backoff: double the delay, capped at max_delay
            delay = min(delay * 2, max_delay)

        # Lock timeout - fetch anyway
        _state.stampede_logger.warning("Cache lock timeout, fetching anyway", key=key)
        return await fetch_func()
