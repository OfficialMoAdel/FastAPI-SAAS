"""Rate limit policy cache for Zero-DB rate limiting.

Caches tier rate limit policies in Redis to avoid DB lookups
during request processing. Policies are loaded on app startup
and can be refreshed when tiers are updated.
"""

from typing import Any

from cachetools import TTLCache

from src.app.core.cache.l1 import IntKeyCodec, L1Cache
from src.app.core.config import settings
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.redis_provider import get_redis

logger = get_logger(__name__)
# S-23: local alias so warm_policy_cache can warn without shadowing the
# module-level `logger` used by the read paths above it.
_pkg_logger = logger

# Module-level L1 cache (256 entries, 5-minute TTL)
_tier_policy_l1: TTLCache[int, list[dict[str, Any]] | None] = TTLCache(maxsize=256, ttl=300)

# R5-MICRO: compiled per-path map (path -> (limit, period)) per tier, built
# once from the L1 policy list instead of re-scanning it linearly on every
# request. Invalidated alongside _tier_policy_l1 so it can never outlive the
# policy list it was compiled from.
_tier_policy_path_map: TTLCache[int, dict[str, tuple[int | None, int | None]]] = TTLCache(maxsize=256, ttl=300)

# INFO-R11 FIX: cross-worker L1 invalidation. Tier policy updates publish on this
# channel so every process drops its in-memory entry immediately instead of
# serving stale policies for up to 300s.
_POLICY_INVALIDATION_CHANNEL = "rate_limit_policy:invalidate"

# The seam owns the grammar. IntKeyCodec is the fail-OPEN codec: a payload that
# is not a digit is IGNORED rather than broadcast, so a malformed message cannot
# flush the whole fleet's policy cache. Both layers are passed together so the
# compiled path map can never outlive the policy list it was built from.
_policy_l1_seam = L1Cache(
    name="rate_limit_policy_l1",
    channel=_POLICY_INVALIDATION_CHANNEL,
    codec=IntKeyCodec(),
    caches=[_tier_policy_l1, _tier_policy_path_map],
    logger_name="rate_limit_policy_l1",
)

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


async def get_tier_policy_limits(tier_id: int, path: str) -> tuple[int | None, int | None]:
    """Resolve (limit, period) for one path from the tier's policy list.

    R5-MICRO: builds a per-path dict on first access per tier instead of
    linearly scanning the policy list on every request. Semantics identical
    to the previous scan: first matching policy row wins, missing keys
    resolve to (None, None).

    Returns:
        (limit, period) from the matching policy, or (None, None) when the
        tier has no cached policies or no row matches the path.
    """
    path_map = _tier_policy_path_map.get(tier_id)
    if path_map is None:
        tier_policies = await get_tier_policy(tier_id)
        if not tier_policies:
            return None, None
        # setdefault: first matching row wins, mirroring the previous linear
        # scan semantics for lists with duplicate path rows.
        path_map = {}
        for policy in tier_policies:
            path_key = policy.get("path")
            if path_key is not None:
                path_map.setdefault(str(path_key), (policy.get("limit"), policy.get("period")))
        _tier_policy_path_map[tier_id] = path_map
    return path_map.get(path, (None, None))


async def warm_policy_cache(tier_policies: dict[int, list[dict[str, Any]]]) -> int:
    """Warm the policy cache with all tier policies.

    Called on app startup to pre-populate cache.
    Uses Redis pipeline for batch operations to reduce startup latency.

    S-23 FIX: this writes REDIS only. It is also called on the H-9 write-back
    path in ``rate_policy.resolve_rate_limits``, which runs AFTER a cache miss
    has already populated the L1 layers — with the authoritative, freshly-read
    policy list. Leaving L1 untouched meant the stale entry kept shadowing the
    fresh one for up to ``_tier_policy_l1``'s 300s TTL, and the compiled
    ``_tier_policy_path_map`` (which drives ``get_tier_policy_limits``) stayed
    empty, so every subsequent request re-ran the DB fallback until that TTL
    expired.

    Seeding L1 here is what makes the write-back actually accelerate anything:
    the next request resolves from memory instead of Redis or the DB.
    """
    if not tier_policies:
        return 0

    # Seed L1 first so a Redis failure cannot leave the stale entry in place
    # while the caller believes the cache was refreshed.
    for tier_id, policies in tier_policies.items():
        _tier_policy_l1[tier_id] = policies
        # The compiled path map must be rebuilt from the SAME list; leaving the
        # previous map in place would resolve limits against the old rows.
        _tier_policy_path_map.pop(tier_id, None)

    redis_client = get_redis()
    if redis_client is None:
        _pkg_logger.warning(
            "Warm policy cache degraded: Redis unavailable; seeded L1 only",
            tiers=len(tier_policies),
        )
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
