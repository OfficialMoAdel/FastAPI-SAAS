"""O(1) pattern-based cache invalidation registry.

Keys are registered against pattern index sets (Redis Sets) when written;
invalidation then deletes the registered keys directly (O(M)) instead of
scanning the key space (O(N)).
"""

from collections.abc import Callable  # noqa: F401  (annotation parity with monolith)
from typing import Any

from src.app.core.utils import cache as _state


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
    client = _state.client
    if client is None or not patterns:
        return

    async with client.pipeline(transaction=False) as pipe:
        for pattern in patterns:
            idx_key = _pattern_index_key(pattern)
            pipe.sadd(idx_key, key)
            # P1-9: index outlives the data key (tenant-index convention).
            # An index expiring at-or-before the data key lets a later write
            # find an empty index and miss still-live keys (stale reads);
            # dead index members are harmless (deleting an expired key is a no-op).
            pipe.expire(idx_key, ttl * 2)
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
    client = _state.client
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
        ttl: TTL in seconds (default: 3600)
        patterns: List of patterns to register this key against

    Returns
    -------
        True if successful
    """
    client = _state.client
    if client is None:
        return False

    serialized = _state.json_dumps(value)

    async with client.pipeline(transaction=False) as pipe:
        pipe.setex(key, ttl, serialized)
        if patterns:
            for pattern in patterns:
                idx_key = _pattern_index_key(pattern)
                pipe.sadd(idx_key, key)
                # P1-9: index TTL is 2x the data TTL (see register_key_patterns).
                pipe.expire(idx_key, ttl * 2)
        await pipe.execute()

    return True
