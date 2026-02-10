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

import logging
import time
from datetime import UTC, datetime
from functools import lru_cache

import xxhash

from src.app.core.utils.cache import client as redis_client

logger = logging.getLogger(__name__)

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


@lru_cache(maxsize=4096)
def _token_cache_key(token: str) -> str:
    """Generate collision-resistant cache key for a token.

    Uses xxhash for fast hashing instead of token prefix, which is
    collision-prone for JWTs (first 20+ chars are often identical headers).
    """
    return xxhash.xxh64(token.encode()).hexdigest()


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
    """Ensure Lua script is loaded into Redis and return its SHA.

    Returns:
        Script SHA if loaded successfully, None if Redis unavailable
    """
    global _script_sha

    if redis_client is None:
        return None

    if _script_sha is not None:
        return _script_sha

    try:
        _script_sha = await redis_client.script_load(BLACKLIST_CHECK_LUA_SCRIPT)
        logger.info(f"Loaded token blacklist Lua script with SHA: {_script_sha}")
        return _script_sha
    except Exception as e:
        logger.warning(f"Failed to load Lua script: {e}")
        return None


async def is_token_blacklisted(token: str) -> bool | None:
    """Check if token is in Redis blacklist cache.

    Uses 2-tier check for optimal performance:
    1. Negative cache (recently verified tokens skip Redis)
    2. Lua script (atomic Redis check) - single roundtrip for all operations

    Args:
        token: The JWT token to check

    Returns:
        True if blacklisted, False if not, None if Redis unavailable (fail open)
    """
    if redis_client is None:
        from src.app.core.monitoring.r1_unified_metrics import record_fail_open

        record_fail_open("token_blacklist")
        return None  # Redis not configured, fall back to DB

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
            result = await redis_client.evalsha(  # type: ignore[misc]
                script_sha,
                2,  # number of keys
                negative_key,
                BLACKLIST_KEY,
                token,
                str(now),
                str(NEGATIVE_CACHE_TTL),
            )
            return bool(int(result))
        except Exception as script_error:
            # If script not found (e.g., Redis restart), reload and retry
            if "NOSCRIPT" in str(script_error):
                global _script_sha
                _script_sha = None
                script_sha = await _ensure_script_loaded()
                if script_sha:
                    retry_result = await redis_client.evalsha(  # type: ignore[misc]
                        script_sha,
                        2,
                        negative_key,
                        BLACKLIST_KEY,
                        token,
                        str(now),
                        str(NEGATIVE_CACHE_TTL),
                    )
                    return bool(int(retry_result))
            raise  # Re-raise if not a NOSCRIPT error

    except Exception as e:
        logger.warning(f"Redis blacklist check failed, falling back to DB: {e}")
        return None  # Fail open - let DB check handle it


async def _is_token_blacklisted_fallback(token: str) -> bool | None:
    """Fallback implementation without Lua script (original 4-roundtrip version).

    Used when Lua script loading fails.
    """
    if redis_client is None:
        from src.app.core.monitoring.r1_unified_metrics import record_fail_open

        record_fail_open("token_blacklist_fallback")
        return None

    try:
        # Check negative cache first (token recently verified as not blacklisted)
        negative_key = f"{NEGATIVE_CACHE_KEY}{_token_cache_key(token)}"
        if await redis_client.exists(negative_key):
            return False

        # ZSCORE returns the score if member exists, None otherwise
        score = await redis_client.zscore(BLACKLIST_KEY, token)
        if score is None:
            # Cache the negative result to avoid repeated lookups
            await redis_client.set(negative_key, "1", ex=NEGATIVE_CACHE_TTL)
            return False

        # Check if token has expired (score is expiry timestamp)
        now = time.time()
        if score < now:
            # Token expired, remove from set
            await redis_client.zrem(BLACKLIST_KEY, token)
            return False

        return True
    except Exception as e:
        logger.warning(f"Redis blacklist fallback check failed: {e}")
        return None


async def add_token_to_blacklist(token: str, expires_at: datetime) -> bool:
    """Add token to Redis blacklist cache.

    Args:
        token: The JWT token to blacklist
        expires_at: When the token expires (used as score for auto-cleanup)

    Returns:
        True if added successfully, False on failure
    """
    if redis_client is None:
        return False

    try:
        # Use expiry timestamp as score for sorted set
        score = expires_at.timestamp()
        # P2-4: Invalidate negative cache for this token
        negative_key = f"{NEGATIVE_CACHE_KEY}{_token_cache_key(token)}"
        # OPT-5: Pipeline both operations for 1 roundtrip instead of 2
        pipe = redis_client.pipeline()
        pipe.zadd(BLACKLIST_KEY, {token: score})
        pipe.delete(negative_key)
        await pipe.execute()
        return True
    except Exception as e:
        logger.warning(f"Failed to add token to Redis blacklist: {e}")
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

    if redis_client is None:
        return 0

    try:
        # Build mapping of token -> expiry score
        mapping = {token: expires_at.timestamp() for token, expires_at in tokens}
        added = await redis_client.zadd(BLACKLIST_KEY, mapping)
        return added if isinstance(added, int) else len(tokens)
    except Exception as e:
        logger.warning(f"Failed to bulk add tokens to Redis blacklist: {e}")
        return 0


async def remove_expired_tokens() -> int:
    """Remove expired tokens from the blacklist.

    Returns:
        Number of tokens removed
    """
    if redis_client is None:
        return 0

    try:
        now = time.time()
        # ZREMRANGEBYSCORE removes all members with score <= now
        removed = await redis_client.zremrangebyscore(BLACKLIST_KEY, "-inf", now)
        return removed if isinstance(removed, int) else 0
    except Exception as e:
        logger.warning(f"Failed to cleanup expired tokens: {e}")
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
    logger.info(f"Warmed token blacklist cache with {count} tokens")
    return count


async def get_cache_stats() -> dict:
    """Get statistics about the blacklist cache.

    Returns:
        Dict with cache statistics
    """
    if redis_client is None:
        return {"enabled": False}

    try:
        count = await redis_client.zcard(BLACKLIST_KEY)
        return {
            "enabled": True,
            "token_count": count,
        }
    except Exception as e:
        logger.warning(f"Failed to get cache stats: {e}")
        return {"enabled": True, "error": str(e)}
