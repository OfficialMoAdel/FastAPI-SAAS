"""Redis-based token blacklist cache for O(1) lookup.

Uses Redis sorted sets (ZADD/ZSCORE) with token expiry as score for
automatic cleanup and fast membership checks.

Design principles:
- FAIL OPEN: Redis outage returns False (not blacklisted) to avoid blocking auth
- TTL-aware: Tokens stored with expiry timestamp as score for efficient cleanup
- Cache warming: Load active blacklisted tokens from DB on startup
- P2-4: Negative caching for verified tokens to reduce Redis calls
- P0-3: Lua script for atomic multi-operation (4 roundtrips → 1)

NOTE: Bloom filter pre-check was REMOVED (R2-HYBRID CRIT-1) due to security
vulnerability in multi-worker deployments. In distributed environments, each
worker has its own independent Bloom filter that is never synchronized, causing
blacklisted tokens to bypass Redis check on workers that didn't receive the
blacklist update. Relying on Redis + negative cache instead.
"""

import asyncio
import time
from datetime import UTC, datetime
from functools import lru_cache

import xxhash
from redis.asyncio import Redis

from src.app.core.logger import get_logger
from src.app.core.utils import cache


def _get_redis_client() -> Redis | None:
    """Get Redis client dynamically to support test fixtures that set cache.client."""
    return cache.client


logger = get_logger(__name__)

# Redis key for the blacklist sorted set
BLACKLIST_KEY = "token:blacklist"
# P2-4: Negative cache key prefix - stores tokens verified as NOT blacklisted
# R2-HYBRID LOW-2: TTL tuned via settings for configurable trade-off
# Higher TTL = fewer Redis calls but slightly delayed revocation detection
NEGATIVE_CACHE_KEY = "token:verified:"

# Import settings for configurable TTL
from src.app.core.config import settings

# R2 #11b: Module-level constant avoids repeated settings access per call
NEGATIVE_CACHE_TTL: int = settings.TOKEN_NEGATIVE_CACHE_TTL

# Lock for preventing concurrent Lua script reloads during NOSCRIPT errors
# Use dict keyed by event loop ID to support pytest-asyncio's per-test event loops
# Store (lock, holding_task) tuple for re-entrancy support
_script_locks: dict[int, tuple[asyncio.Lock, int | None]] = {}


async def _acquire_script_lock() -> bool:
    """Acquire script reload lock with re-entrancy support.

    Returns True if lock was acquired (caller must release), False if already held.
    """
    try:
        loop = asyncio.get_running_loop()
        loop_id = id(loop)
        task_id = id(asyncio.current_task()) if asyncio.current_task() else None
    except RuntimeError:
        return True  # No loop, proceed without lock

    if loop_id not in _script_locks:
        _script_locks[loop_id] = (asyncio.Lock(), None)

    lock, holding_task = _script_locks[loop_id]

    # Re-entrancy: if current task already holds the lock, proceed
    if holding_task == task_id:
        return False  # Already holding, don't release

    await lock.acquire()
    _script_locks[loop_id] = (lock, task_id)
    return True


def _release_script_lock(release: bool = True) -> None:
    """Release script reload lock if it was acquired."""
    if not release:
        return  # Was re-entrant, don't release
    try:
        loop = asyncio.get_running_loop()
        loop_id = id(loop)
    except RuntimeError:
        return

    if loop_id in _script_locks:
        lock, _ = _script_locks[loop_id]
        if lock.locked():
            lock.release()
        _script_locks[loop_id] = (lock, None)


@lru_cache(maxsize=4096)
def _token_cache_key(token: str) -> str:
    """Generate collision-resistant cache key for a token.

    Uses xxhash for fast hashing instead of token prefix, which is
    collision-prone for JWTs (first 20+ chars are often identical headers).
    """
    # R2 #11b: Use JTI for key if available to match blacklist members
    member = _extract_blacklist_member(token)
    return xxhash.xxh64(member.encode()).hexdigest()


# P0-3: Lua script for atomic blacklist check (4 operations → 1 roundtrip)
BLACKLIST_CHECK_LUA_SCRIPT = """
-- Token blacklist check: atomic 4-operation sequence
-- KEYS[1]: negative cache key (token:verified:{token_prefix})
-- KEYS[2]: blacklist sorted set key (token:blacklist)
-- ARGV[1]: token (full token string)
-- ARGV[2]: current timestamp
-- ARGV[3]: negative cache TTL

-- Check negative cache first
if redis.call('EXISTS', KEYS[1]) == 1 then
    return 0  -- Not blacklisted (cached)
end

-- Check blacklist sorted set
local score = redis.call('ZSCORE', KEYS[2], ARGV[1])
if not score then
    -- Not in blacklist, add to negative cache
    redis.call('SETEX', KEYS[1], ARGV[3], '1')
    return 0  -- Not blacklisted
end

-- Check if expired
if tonumber(score) < tonumber(ARGV[2]) then
    -- Expired, remove and add to negative cache
    redis.call('ZREM', KEYS[2], ARGV[1])
    redis.call('SETEX', KEYS[1], ARGV[3], '1')
    return 0  -- Not blacklisted (was expired)
end

return 1  -- IS blacklisted
"""

# Cache for Lua script SHA
_script_sha: str | None = None


async def _ensure_script_loaded() -> str | None:
    """Load Lua script into Redis if not already loaded.

    Returns script SHA for use with EVALSHA, or None if Redis unavailable.
    """
    redis_client = _get_redis_client()
    if redis_client is None:
        return None

    global _script_sha
    if _script_sha is not None:
        return _script_sha

    try:
        should_release = await _acquire_script_lock()
        try:
            if _script_sha is None:
                _script_sha = await redis_client.script_load(BLACKLIST_CHECK_LUA_SCRIPT)
                logger.debug("blacklist_check_script_loaded", extra={"sha": _script_sha})
        finally:
            _release_script_lock(should_release)
        return _script_sha
    except Exception as e:
        logger.warning("Failed to load Lua script", error=e)
        return None


def _extract_blacklist_member(token: str) -> str:
    """Extract the Redis sorted-set member key for a token.

    ``blacklist_tokens`` stores ``jti:{jti}`` (not the raw token) when a JTI
    claim is present.  This helper performs the same extraction so that lookup
    and storage always use an identical key.
    """
    try:
        import jwt as _jwt

        payload = _jwt.decode(token, options={"verify_signature": False})
        jti = payload.get("jti")
        if jti:
            return f"jti:{jti}"
    except Exception:
        pass  # Malformed token – fall back to raw token
    return token


async def is_token_blacklisted(token: str) -> bool | None:
    """Check if token is blacklisted using Lua script for atomic operations.

    Uses 2-tier check for optimal performance:
    1. Negative cache (recently verified tokens skip Redis)
    2. Lua script (atomic Redis check) - single roundtrip for all operations

    Args:
        token: The JWT token to check

    Returns:
        True if blacklisted, False if not, None if Redis unavailable (fail open)
    """
    redis_client = _get_redis_client()
    if redis_client is None:
        from src.app.core.monitoring.r1_unified_metrics import record_fail_open

        record_fail_open("token_blacklist")
        return None  # Redis not configured, fall back to DB

    # Resolve the member key that blacklist_tokens used when storing this token.
    # blacklist_tokens stores "jti:{jti}" when a JTI claim is present, so we
    # must use the same key here to get a cache hit.
    blacklist_member = _extract_blacklist_member(token)

    try:
        # P0-3: Use Lua script for atomic check (4 operations → 1 roundtrip)
        script_sha = await _ensure_script_loaded()
        if script_sha is None:
            # Fallback to old implementation if script loading failed
            return await _is_token_blacklisted_fallback(token)

        negative_key = f"{NEGATIVE_CACHE_KEY}{_token_cache_key(token)}"  # Hash for collision resistance
        now = int(time.time())

        try:
            # Execute Lua script with EVALSHA
            # ARGV[1] must match the member stored by blacklist_tokens (jti:{jti})
            result = await redis_client.evalsha(  # type: ignore[misc]
                script_sha,
                2,  # number of keys
                negative_key,
                BLACKLIST_KEY,
                blacklist_member,
                str(now),
                str(NEGATIVE_CACHE_TTL),
            )
            return bool(int(result))
        except Exception as script_error:
            # If script not found (e.g., Redis restart), reload and retry
            if "NOSCRIPT" in str(script_error):
                # Use lock to prevent concurrent reloads from multiple coroutines
                should_release = await _acquire_script_lock()
                try:
                    # Double-check pattern - another coroutine might have loaded it
                    global _script_sha
                    if _script_sha is not None and _script_sha != script_sha:
                        # Another coroutine already reloaded with a different SHA, retry
                        retry_result = await redis_client.evalsha(  # type: ignore[misc]
                            _script_sha,
                            2,
                            negative_key,
                            BLACKLIST_KEY,
                            blacklist_member,
                            str(now),
                            str(NEGATIVE_CACHE_TTL),
                        )
                        return bool(int(retry_result))

                    # We need to reload - invalidate old SHA first
                    _script_sha = None
                    script_sha = await _ensure_script_loaded()
                    if script_sha:
                        retry_result = await redis_client.evalsha(  # type: ignore[misc]
                            script_sha,
                            2,
                            negative_key,
                            BLACKLIST_KEY,
                            blacklist_member,
                            str(now),
                            str(NEGATIVE_CACHE_TTL),
                        )
                        return bool(int(retry_result))
                finally:
                    _release_script_lock(should_release)
            raise  # Re-raise if not a NOSCRIPT error

    except Exception as e:
        logger.warning("Redis blacklist check failed, falling back to DB", error=e)
        return None  # Fail open - let DB check handle it


async def _is_token_blacklisted_fallback(token: str) -> bool | None:
    """Fallback implementation without Lua script (original 4-roundtrip version).

    Used when Lua script loading fails.
    """
    redis_client = _get_redis_client()
    if redis_client is None:
        from src.app.core.monitoring.r1_unified_metrics import record_fail_open

        record_fail_open("token_blacklist_fallback")
        return None

    # Use the same member key that blacklist_tokens used during storage.
    blacklist_member = _extract_blacklist_member(token)

    try:
        # Check negative cache first (token recently verified as not blacklisted)
        negative_key = f"{NEGATIVE_CACHE_KEY}{_token_cache_key(token)}"
        if await redis_client.exists(negative_key):
            return False

        # ZSCORE returns the score if member exists, None otherwise
        score = await redis_client.zscore(BLACKLIST_KEY, blacklist_member)
        if score is None:
            # Cache the negative result to avoid repeated lookups
            await redis_client.set(negative_key, "1", ex=NEGATIVE_CACHE_TTL)
            return False

        # Check if token has expired (score is expiry timestamp)
        now = time.time()
        if score < now:
            # Token expired, remove from set
            await redis_client.zrem(BLACKLIST_KEY, blacklist_member)
            return False

        return True
    except Exception as e:
        logger.warning("Redis blacklist fallback check failed", error=e)
        return None


async def add_token_to_blacklist(token: str, expires_at: datetime) -> bool:
    """Add token to Redis blacklist cache.

    Args:
        token: The JWT token to blacklist
        expires_at: When the token expires (used as score for auto-cleanup)

    Returns:
        True if added successfully, False on failure
    """
    redis_client = _get_redis_client()
    if redis_client is None:
        return False

    try:
        # Use expiry timestamp as score for sorted set
        score = expires_at.timestamp()
        # Use JTI-based member key if available
        blacklist_member = _extract_blacklist_member(token)
        # P2-4: Invalidate negative cache for this token
        negative_key = f"{NEGATIVE_CACHE_KEY}{_token_cache_key(token)}"
        # OPT-5: Pipeline both operations for 1 roundtrip instead of 2
        async with redis_client.pipeline() as pipe:
            pipe.zadd(BLACKLIST_KEY, {blacklist_member: score})  # type: ignore[misc]
            pipe.delete(negative_key)
            await pipe.execute()
        return True
    except Exception as e:
        logger.warning("Failed to add token to Redis blacklist", error=e)
        return False


async def add_tokens_to_blacklist(tokens: list[tuple[str, datetime]]) -> int:
    """Bulk add tokens to Redis blacklist cache.

    Args:
        tokens: List of (token, expires_at) tuples

    Returns:
        Number of tokens added successfully
    """
    if not tokens:
        return 0

    redis_client = _get_redis_client()
    if redis_client is None:
        return 0

    try:
        # M1: Use pipeline for atomic multi-operation (add to blacklist + invalidate negative cache)
        async with redis_client.pipeline() as pipe:
            # Build mapping of member -> expiry score
            # R2 #11b: Use JTI for key if available to match lookup logic
            mapping = {_extract_blacklist_member(token): expires_at.timestamp() for token, expires_at in tokens}
            pipe.zadd(BLACKLIST_KEY, mapping)  # type: ignore[misc]
            # M1: Invalidate negative cache for all tokens being blacklisted
            for token, _ in tokens:
                negative_key = f"{NEGATIVE_CACHE_KEY}{_token_cache_key(token)}"
                pipe.delete(negative_key)
            results = await pipe.execute()
        # ZADD returns count of new members added
        added = results[0] if results else 0
        return added if isinstance(added, int) else len(tokens)
    except Exception as e:
        logger.warning("Failed to bulk add tokens to Redis blacklist", error=e)
        return 0


async def remove_expired_tokens() -> int:
    """Remove expired tokens from the blacklist.

    Returns:
        Number of tokens removed
    """
    redis_client = _get_redis_client()
    if redis_client is None:
        return 0

    try:
        now = time.time()
        # ZREMRANGEBYSCORE removes all members with score <= now
        removed = await redis_client.zremrangebyscore(BLACKLIST_KEY, "-inf", now)
        return removed if isinstance(removed, int) else 0
    except Exception as e:
        logger.warning("Failed to cleanup expired tokens", error=e)
        return 0


async def warm_cache_from_db(db_tokens: list[tuple[str, datetime]]) -> int:
    """Warm the Redis cache with tokens from database.

    Called during application startup to populate cache.

    Args:
        db_tokens: List of (token, expires_at) tuples from database

    Returns:
        Number of tokens loaded into cache
    """
    if not db_tokens:
        return 0

    # Filter out already-expired tokens
    now = datetime.now(UTC)
    active_tokens = []
    for t, exp in db_tokens:
        # Handle naive datetimes from DB (assume UTC)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)

        if exp > now:
            active_tokens.append((t, exp))

    if not active_tokens:
        return 0

    count = await add_tokens_to_blacklist(active_tokens)
    logger.info("Warmed token blacklist cache with tokens", count=count)
    return count


async def get_cache_stats() -> dict:
    """Get statistics about the blacklist cache.

    Returns:
        Dict with cache statistics
    """
    redis_client = _get_redis_client()
    if redis_client is None:
        return {"enabled": False}

    try:
        count = await redis_client.zcard(BLACKLIST_KEY)
        return {
            "enabled": True,
            "token_count": count,
        }
    except Exception as e:
        logger.warning("Failed to get cache stats", error=e)
        return {"enabled": True, "error": str(e)}


def reset_token_blacklist_cache() -> None:
    """Reset the token blacklist cache state.

    Used for test isolation to prevent state leakage between tests.
    Clears:
    - Lua script SHA cache
    - Script locks per event loop
    - Token cache key LRU cache
    """
    global _script_sha, _script_locks

    _script_sha = None
    _script_locks.clear()

    # Clear the LRU cache for token keys
    _token_cache_key.cache_clear()

    logger.debug("Token blacklist cache state reset")
