"""Redis-based token blacklist cache for O(1) lookup.

Uses Redis sorted sets (ZADD/ZSCORE) with token expiry as score for
automatic cleanup and fast membership checks.

Design principles:
- FAIL OPEN: Redis outage returns False (not blacklisted) to avoid blocking auth
- TTL-aware: Tokens stored with expiry timestamp as score for efficient cleanup
- Cache warming: Load active blacklisted tokens from DB on startup
"""

import logging
from datetime import UTC, datetime

from src.app.core.utils.cache import client as redis_client

logger = logging.getLogger(__name__)

# Redis key for the blacklist sorted set
BLACKLIST_KEY = "token:blacklist"


async def is_token_blacklisted(token: str) -> bool | None:
    """Check if token is in Redis blacklist cache.

    Args:
        token: The JWT token to check

    Returns:
        True if blacklisted, False if not, None if Redis unavailable (fail open)
    """
    if redis_client is None:
        return None  # Redis not configured, fall back to DB

    try:
        # ZSCORE returns the score if member exists, None otherwise
        score = await redis_client.zscore(BLACKLIST_KEY, token)
        if score is None:
            return False

        # Check if token has expired (score is expiry timestamp)
        now = datetime.now(UTC).timestamp()
        if score < now:
            # Token expired, remove from set
            await redis_client.zrem(BLACKLIST_KEY, token)
            return False

        return True
    except Exception as e:
        logger.warning(f"Redis blacklist check failed, falling back to DB: {e}")
        return None  # Fail open - let DB check handle it


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
        await redis_client.zadd(BLACKLIST_KEY, {token: score})
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
    if redis_client is None or not tokens:
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
        now = datetime.now(UTC).timestamp()
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
    active_tokens = [(t, exp) for t, exp in db_tokens if exp > now]

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
