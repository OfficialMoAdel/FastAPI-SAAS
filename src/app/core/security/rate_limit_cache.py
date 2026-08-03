"""Rate limit policy cache for Zero-DB rate limiting.

Caches tier rate limit policies in Redis to avoid DB lookups
during request processing. Policies are loaded on app startup
and can be refreshed when tiers are updated.
"""

from typing import Any

from cachetools import TTLCache

from src.app.core.config import settings
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.utils.cache import client as redis_client

logger = get_logger(__name__)

# Module-level L1 cache (256 entries, 5-minute TTL)
_tier_policy_l1: TTLCache[int, list[dict[str, Any]] | None] = TTLCache(maxsize=256, ttl=300)

# Redis key patterns
POLICY_KEY_PREFIX = "rate_limit:policy:tier:"
POLICY_INDEX_KEY = "rate_limit:policies:all"



async def get_tier_policy(tier_id: int) -> list[dict[str, Any]] | None:
    """Get cached rate limit policies for a tier.

    Args:
        tier_id: The tier ID

    Returns:
        List of policy dicts, or None if not cached
    """
    # L1: In-memory check first (avoids Redis RT)
    if tier_id in _tier_policy_l1:
        return _tier_policy_l1[tier_id]

    if redis_client is None:
        _tier_policy_l1[tier_id] = None
        return None

    try:
        key = f"{POLICY_KEY_PREFIX}{tier_id}"
        data = await redis_client.get(key)
        if data:
            result: list[dict[str, Any]] = json_loads(data)
            _tier_policy_l1[tier_id] = result
            return result
        _tier_policy_l1[tier_id] = None
        return None
    except Exception as e:
        logger.warning("Failed to get tier policy from cache", error=e)
        return None


async def warm_policy_cache(tier_policies: dict[int, list[dict[str, Any]]]) -> int:
    """Warm the policy cache with all tier policies.

    Called on app startup to pre-populate cache.
    Uses Redis pipeline for batch operations to reduce startup latency.

    Args:
        tier_policies: Dict mapping tier_id to list of policies

    Returns:
        Number of tiers cached
    """
    if redis_client is None or not tier_policies:
        return 0

    try:
        # Use pipeline for batch cache warming - reduces N roundtrips to 1
        ttl = getattr(settings, "RATE_LIMIT_POLICY_TTL", 3600)
        async with redis_client.pipeline() as pipe:
            for tier_id, policies in tier_policies.items():
                key = f"{POLICY_KEY_PREFIX}{tier_id}"
                pipe.setex(key, ttl, json_dumps(policies, as_str=True))  # Configurable TTL
            await pipe.execute()

        count = len(tier_policies)
        logger.info("Warmed rate limit policy cache with tiers using pipeline", count=count)
        return count
    except Exception as e:
        logger.warning("Failed to warm policy cache", error=e)
        return 0


async def invalidate_tier_policy(tier_id: int) -> bool:
    """Invalidate cached policy for a tier.

    Call this when tier rate limits are updated.

    Args:
        tier_id: The tier ID to invalidate

    Returns:
        True if invalidated successfully
    """
    _tier_policy_l1.pop(tier_id, None)
    if redis_client is None:
        return False

    try:
        key = f"{POLICY_KEY_PREFIX}{tier_id}"
        await redis_client.delete(key)
        return True
    except Exception as e:
        logger.warning("Failed to invalidate tier policy", error=e)
        return False
