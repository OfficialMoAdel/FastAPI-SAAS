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

import time
from datetime import UTC, datetime
from functools import lru_cache

import xxhash
from redis.asyncio import Redis

from src.app.core.logger import get_logger
from src.app.core.resilience.registry import ServiceType, create_circuit_breaker
from src.app.core.utils.lua_manager import get_lua_manager
from src.app.shared.exceptions import ServiceUnavailableError

# Registered with the central CircuitBreakerRegistry (ServiceType.REDIS) so it
# is visible to the admin circuit-breaker endpoints and health summaries,
# instead of rolling its own state like the legacy TokenCacheCircuitBreaker.
# success_threshold=1 preserves the legacy "first success closes the circuit"
# behavior; failure_threshold=5 / recovery_timeout=30 match the old defaults.
token_cache_circuit = create_circuit_breaker(
    name="token_cache_circuit",
    failure_threshold=5,
    recovery_timeout=30.0,
    success_threshold=1,
    service_type=ServiceType.REDIS,
    description="Redis token-blacklist cache (auth revocation lookups)",
)

def _get_redis_client() -> Redis | None:
    """Get Redis client via the single seam (observes test patches of utils.cache.client)."""
    from src.app.core.redis_provider import get_redis

    return get_redis()


def _get_available_redis_client_for_blacklist() -> tuple[Redis | None, bool]:
    """Get Redis client for blacklist operations with circuit breaker check.

    Returns:
        Tuple of (redis_client, fail_closed). If circuit breaker is open and
        TOKEN_CACHE_FAILURE_MODE is 'fail_closed', returns (None, True).
        If Redis is unavailable, returns (None, False) for fail-open behavior.
    """
    fail_closed = settings.auth.TOKEN_CACHE_FAILURE_MODE == "fail_closed"
    if not token_cache_circuit.is_closed:
        # A1: the breaker's job is to stop hammering a failing Redis — NOT to
        # downgrade the configured failure mode. Under fail_closed, report
        # unknown-as-blacklisted so callers (deps DB fallback, verify_token)
        # still enforce revocation during the outage window instead of
        # accepting revoked tokens. Fail-open mode keeps the old behavior.
        return None, fail_closed
    return _get_redis_client(), fail_closed


logger = get_logger(__name__)

# Redis key for the blacklist sorted set
BLACKLIST_KEY = "token:blacklist"
# P2-4: Negative cache key prefix - stores tokens verified as NOT blacklisted
# R2-HYBRID LOW-2: TTL tuned via settings for configurable trade-off
# Reduced to 60 seconds to minimize negative cache invalidation delays
#
# S-2 revocation semantics: add_token_to_blacklist / add_tokens_to_blacklist
# delete the negative key in the SAME pipeline as the blacklist ZADD, so
# revocation is immediate on successful write. The Lua check path is atomic
# (single EVALSHA), so it cannot interleave with a revocation. The only race
# window is the non-atomic 4-roundtrip FALLBACK path (used when Lua script
# loading fails): a concurrent verify can re-SET the negative key after the
# revocation's DELETE, accepting a revoked token for at most NEGATIVE_CACHE_TTL
# (60s). Bounded by design; keep the TTL low.
NEGATIVE_CACHE_KEY = "token:verified:"

# Import settings for configurable TTL
from src.app.core.config import settings

# R2 #11b: Module-level constant avoids repeated settings access per call
# Use getattr with fallback to handle initialization edge cases in test environments
NEGATIVE_CACHE_TTL: int = getattr(settings.r1_unified, "TOKEN_NEGATIVE_CACHE_TTL", 60)

# NOTE: Script reload locking is owned by LuaScriptManager (EVALSHA +
# NOSCRIPT fallback). The legacy per-event-loop lock dict and SHA cache are
# gone; ``_ensure_script_loaded`` remains as a thin compatibility shim for
# tests that patch it.
_script_sha: str | None = None


async def _ensure_script_loaded() -> str | None:
    """Compatibility shim: return the registered script's SHA.

    The script is registered in LuaScriptManager at import time and its SHA
    is derived from the source — no Redis roundtrip is involved. Returns None
    only if the script is somehow unregistered (callers fall back to the
    non-atomic path).
    """
    try:
        return get_lua_manager().get_script("token_blacklist_check").sha1
    except Exception:
        return None


@lru_cache(maxsize=4096)
def _token_cache_key(token: str, jti: str | None = None) -> str:
    """Generate collision-resistant cache key for a token.

    Uses xxhash for fast hashing instead of token prefix, which is
    collision-prone for JWTs (first 20+ chars are often identical headers).
    """
    # R2 #11b: Use JTI for key if available to match blacklist members
    member = _extract_blacklist_member(token, jti)
    return xxhash.xxh64(member.encode()).hexdigest()



def _extract_blacklist_member(token: str, jti: str | None = None) -> str:
    """Extract the Redis sorted-set member key for a token.

    ``blacklist_tokens`` stores ``jti:{jti}`` (not the raw token) when a JTI
    claim is present.  This helper performs the same extraction so that lookup
    and storage always use an identical key.
    """
    if jti:
        return f"jti:{jti}"

    # R4-01: do not extract JTI from unverified token — attacker could craft
    # arbitrary jti to pollute the blacklist cache key space. Only use
    # verified jti passed explicitly; otherwise fall back to raw token.
    return token


async def is_token_blacklisted(token: str, jti: str | None = None) -> bool | None:
    """Check if token is blacklisted using Lua script for atomic operations.

    Uses 2-tier check for optimal performance:
    1. Negative cache (recently verified tokens skip Redis)
    2. Lua script (atomic Redis check) - single roundtrip for all operations

    Args:
        token: The JWT token to check

    Returns:
        True if blacklisted, False if not, None if Redis unavailable (fail open)
    """
    redis_client, fail_closed = _get_available_redis_client_for_blacklist()
    if redis_client is None:
        if fail_closed:
            return True
        return None  # Fail open - fall back to DB check

    # Resolve the member key that blacklist_tokens used when storing this token.
    # blacklist_tokens stores "jti:{jti}" when a JTI claim is present, so we
    # must use the same key here to get a cache hit.
    blacklist_member = _extract_blacklist_member(token, jti)

    try:
        # P0-3: atomic Lua check via the shared manager (EVALSHA + NOSCRIPT fallback)
        script_sha = await _ensure_script_loaded()
        if script_sha is None:
            return await _is_token_blacklisted_fallback(token, jti)

        negative_key = f"{NEGATIVE_CACHE_KEY}{_token_cache_key(token, jti)}"  # Hash for collision resistance
        now = int(time.time())

        # ARGV[1] must match the member stored by blacklist_tokens (jti:{jti})
        result = await get_lua_manager().execute(
            redis_client,
            "token_blacklist_check",
            keys=[negative_key, BLACKLIST_KEY],
            args=[
                blacklist_member,
                str(now),
                str(settings.r1_unified.TOKEN_NEGATIVE_CACHE_TTL),
            ],
        )
        await token_cache_circuit.record_success()
        return bool(int(result))

    except Exception as e:
        await token_cache_circuit.record_failure(e)
        logger.warning("Redis blacklist check failed", error=e)
        # SECURITY FIX: Fail closed if configured
        if settings.auth.TOKEN_CACHE_FAILURE_MODE == "fail_closed":
            return True
        return None  # Fail open - let DB check handle it


async def _is_token_blacklisted_fallback(token: str, jti: str | None = None) -> bool | None:
    """Fallback implementation without Lua script (original 4-roundtrip version).

    Used when Lua script loading fails.

    SECURITY FIX: honors TOKEN_CACHE_FAILURE_MODE on error. Previously this
    path returned None (fail-open) unconditionally, so with fail_closed
    configured a Redis error during the fallback still let the caller treat
    the token as not revoked — defeating the configured failure mode.
    """
    from src.app.core.config import settings

    fail_closed = settings.auth.TOKEN_CACHE_FAILURE_MODE == "fail_closed"
    redis_client = _get_redis_client()
    if redis_client is None:
        from src.app.core.monitoring.r1_unified_metrics import record_fail_open

        if not fail_closed:
            record_fail_open("token_blacklist_fallback")
        return True if fail_closed else None

    blacklist_member = _extract_blacklist_member(token, jti)

    try:
        negative_key = f"{NEGATIVE_CACHE_KEY}{_token_cache_key(token, jti)}"
        if await redis_client.exists(negative_key):
            return False

        score = await redis_client.zscore(BLACKLIST_KEY, blacklist_member)
        if score is None:
            await redis_client.set(negative_key, "1", ex=settings.r1_unified.TOKEN_NEGATIVE_CACHE_TTL)
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
        return True if fail_closed else None


async def add_token_to_blacklist(token: str, expires_at: datetime) -> bool:
    """Add token to Redis blacklist cache.

    Args:
        token: The JWT token to blacklist
        expires_at: When the token expires (used as score for auto-cleanup)

    Returns:
        True if added successfully

    Raises:
        ServiceUnavailableError: Redis is unavailable or the write failed.
            Revocation writes must be loud — a silent False leaves a
            "logged-out" token valid until its natural expiry (up to 7 days
            for refresh tokens). Callers (logout, refresh rotation) surface
            this as 5xx so the client retries instead of believing the
            logout succeeded.
    """
    redis_client = _get_redis_client()
    if redis_client is None:
        raise ServiceUnavailableError("Redis unavailable — token revocation write skipped")

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
        logger.error("Failed to add token to Redis blacklist", error=e)
        raise ServiceUnavailableError("Token revocation write failed") from e


async def add_tokens_to_blacklist(tokens: list[tuple[str, datetime]]) -> int:
    """Bulk add tokens to Redis blacklist cache.

    Args:
        tokens: List of (token, expires_at) tuples

    Returns:
        Number of tokens added successfully

    Raises:
        ServiceUnavailableError: Redis is unavailable or the write failed.
            Same loud-revocation contract as ``add_token_to_blacklist``.
    """
    if not tokens:
        return 0

    redis_client = _get_redis_client()
    if redis_client is None:
        raise ServiceUnavailableError("Redis unavailable — token revocation write skipped")

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
        logger.error("Failed to bulk add tokens to Redis blacklist", error=e)
        raise ServiceUnavailableError("Token revocation bulk write failed") from e


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

    added = await add_tokens_to_blacklist(active_tokens)
    logger.info("Warmed token blacklist cache with tokens", count=added)
    return added


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
    - Legacy script SHA slot (loading is owned by LuaScriptManager)
    - Token cache key LRU cache
    """
    global _script_sha

    _script_sha = None

    # Clear the LRU cache for token keys
    _token_cache_key.cache_clear()

    logger.debug("Token blacklist cache state reset")
