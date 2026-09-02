"""Rate limit policy cache for Zero-DB rate limiting.

Caches tier rate limit policies in Redis to avoid DB lookups
during request processing. Policies are loaded on app startup
and can be refreshed when tiers are updated.
"""

import asyncio
from typing import Any

from cachetools import TTLCache

from src.app.core.config import settings
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.redis_provider import get_redis

logger = get_logger(__name__)

# Module-level L1 cache (256 entries, 5-minute TTL)
_tier_policy_l1: TTLCache[int, list[dict[str, Any]] | None] = TTLCache(maxsize=256, ttl=300)

# INFO-R11 FIX: cross-worker L1 invalidation. Tier policy updates publish on
# this channel so every process drops its in-memory entry immediately instead
# of serving stale policies for up to 300s.
_POLICY_INVALIDATION_CHANNEL = "rate_limit_policy:invalidate"
_policy_invalidation_task: asyncio.Task | None = None


def start_policy_invalidation_listener() -> None:
    """Start (once per event loop) a subscriber applying L1 invalidations."""
    global _policy_invalidation_task
    redis_client = get_redis()
    if redis_client is None:
        return
    if _policy_invalidation_task is not None and not _policy_invalidation_task.done():
        return

    async def _listen() -> None:
        if not hasattr(redis_client, "pubsub"):
            return
        pubsub = redis_client.pubsub()
        try:
            await pubsub.subscribe(_POLICY_INVALIDATION_CHANNEL)
            async for msg in pubsub.listen():
                if not isinstance(msg, dict) or msg.get("type") != "message":
                    continue
                data = msg.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="ignore")
                if data == "*":
                    _tier_policy_l1.clear()
                elif data and str(data).isdigit():
                    _tier_policy_l1.pop(int(data), None)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("Policy L1 invalidation listener exited", error=str(e))

    _policy_invalidation_task = asyncio.create_task(_listen())


_start_policy_invalidation_listener = start_policy_invalidation_listener


async def stop_policy_invalidation_listener() -> None:
    """Stop the policy invalidation subscriber task on shutdown."""
    global _policy_invalidation_task
    if _policy_invalidation_task is not None and not _policy_invalidation_task.done():
        _policy_invalidation_task.cancel()
        try:
            await _policy_invalidation_task
        except asyncio.CancelledError:
            pass
        _policy_invalidation_task = None

async def _publish_policy_invalidation(tier_id: int) -> None:
    redis_client = get_redis()
    if redis_client is None:
        return
    try:
        await redis_client.publish(_POLICY_INVALIDATION_CHANNEL, str(tier_id))
    except Exception as e:  # noqa: BLE001 - never break mutations on pubsub errors
        logger.warning("Failed to publish policy invalidation", error=str(e))


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
    redis_client = get_redis()
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
    redis_client = get_redis()
    if redis_client is None or not tier_policies:
        return 0

    try:
        # INFO-R11 FIX: use the now-declared typed setting (was a phantom knob).
        ttl = settings.rate_limit.RATE_LIMIT_POLICY_TTL
        async with redis_client.pipeline() as pipe:
            for tier_id, policies in tier_policies.items():
                key = f"{POLICY_KEY_PREFIX}{tier_id}"
                pipe.setex(key, ttl, json_dumps(policies, as_str=True))
            await pipe.execute()

        count = len(tier_policies)
        logger.info("Warmed rate limit policy cache with tiers using pipeline", count=count)
        return count
    except Exception as e:
        logger.warning("Failed to warm policy cache", error=e)
        return 0


async def invalidate_tier_policy(tier_id: int) -> bool:
    """Invalidate cached policy for a tier in single round-trip (P2-2 Optimization).

    Pipelines key deletion and L1 invalidation publish into 1 network execution.

    Args:
        tier_id: The tier ID to invalidate

    Returns:
        True if invalidated successfully
    """
    _tier_policy_l1.pop(tier_id, None)
    redis_client = get_redis()
    if redis_client is None:
        return False

    try:
        key = f"{POLICY_KEY_PREFIX}{tier_id}"
        async with redis_client.pipeline(transaction=False) as pipe:
            pipe.delete(key)
            pipe.publish(_POLICY_INVALIDATION_CHANNEL, str(tier_id))
            await pipe.execute()
        return True
    except Exception as e:
        logger.warning("Failed to invalidate tier policy", error=e)
        return False
