"""Rate limit policy cache for Zero-DB rate limiting.

Caches tier rate limit policies in Redis to avoid DB lookups
during request processing. Policies are loaded on app startup
and can be refreshed when tiers are updated.
"""

import logging
from typing import Any

from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.utils.cache import client as redis_client

logger = logging.getLogger(__name__)

# Redis key patterns
POLICY_KEY_PREFIX = "rate_limit:policy:tier:"
POLICY_INDEX_KEY = "rate_limit:policies:all"


async def cache_tier_policy(tier_id: int, policies: list[dict[str, Any]]) -> bool:
    """Cache rate limit policies for a tier.

    Args:
        tier_id: The tier ID
        policies: List of rate limit policy dicts

    Returns:
        True if cached successfully
    """
    if redis_client is None:
        return False

    try:
        key = f"{POLICY_KEY_PREFIX}{tier_id}"
        await redis_client.setex(key, 3600, json_dumps(policies, as_str=True))  # 1 hour TTL
        return True
    except Exception as e:
        logger.warning(f"Failed to cache tier policy: {e}")
        return False


async def get_tier_policy(tier_id: int) -> list[dict[str, Any]] | None:
    """Get cached rate limit policies for a tier.

    Args:
        tier_id: The tier ID

    Returns:
        List of policy dicts, or None if not cached
    """
    if redis_client is None:
        return None

    try:
        key = f"{POLICY_KEY_PREFIX}{tier_id}"
        data = await redis_client.get(key)
        if data:
            result: list[dict[str, Any]] = json_loads(data)
            return result
        return None
    except Exception as e:
        logger.warning(f"Failed to get tier policy from cache: {e}")
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
        async with redis_client.pipeline() as pipe:
            for tier_id, policies in tier_policies.items():
                key = f"{POLICY_KEY_PREFIX}{tier_id}"
                pipe.setex(key, 3600, json_dumps(policies, as_str=True))  # 1 hour TTL
            await pipe.execute()

        count = len(tier_policies)
        logger.info(f"Warmed rate limit policy cache with {count} tiers using pipeline")
        return count
    except Exception as e:
        logger.warning(f"Failed to warm policy cache: {e}")
        return 0


async def invalidate_tier_policy(tier_id: int) -> bool:
    """Invalidate cached policy for a tier.

    Call this when tier rate limits are updated.

    Args:
        tier_id: The tier ID to invalidate

    Returns:
        True if invalidated successfully
    """
    if redis_client is None:
        return False

    try:
        key = f"{POLICY_KEY_PREFIX}{tier_id}"
        await redis_client.delete(key)
        return True
    except Exception as e:
        logger.warning(f"Failed to invalidate tier policy: {e}")
        return False
