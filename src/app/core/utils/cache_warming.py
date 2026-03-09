"""Cache warming utility for preloading frequently accessed data on startup.

Reduces cold-start latency by populating Redis cache with:
- Active tenant slugs (for middleware validation)
- Tier names and configurations (for rate limiting)
"""

import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import local_session
from src.app.core.logger import get_logger
from src.app.modules.tenants.models import Tenant
from src.app.modules.tiers.models import Tier

logger = get_logger(__name__)

# Cache TTLs (in seconds)
TENANT_CACHE_TTL = 3600  # 1 hour
TIER_CACHE_TTL = 3600  # 1 hour


@dataclass
class CacheWarmingResult:
    """Results from cache warming operation."""

    tenants_cached: int = 0
    tiers_cached: int = 0
    duration_ms: float = 0.0
    errors: list[str] | None = None


async def warm_tenant_cache(session: AsyncSession) -> int:
    """Preload all active tenant slugs into Redis cache.

    Returns
    -------
        Number of tenants cached.
    """
    from src.app.core.utils.cache import client as redis_client

    if redis_client is None:
        logger.warning("Redis client not available, skipping tenant cache warming")
        return 0

    import orjson  # OPT-8: Use orjson for faster serialization

    stmt = select(Tenant).where(Tenant.is_active.is_(True))
    result = await session.execute(stmt)
    tenants = result.scalars().all()

    cached_count = 0
    try:
        async with redis_client.pipeline() as pipe:
            for tenant in tenants:
                cache_key = f"tenant:slug:{tenant.slug}"
                # P2-3: Keep as bytes - Redis setex() accepts bytes directly
                cache_data = orjson.dumps(
                    {
                        "id": tenant.id,
                        "slug": tenant.slug,
                        "is_active": tenant.is_active,
                    }
                )
                pipe.setex(cache_key, TENANT_CACHE_TTL, cache_data)
            await pipe.execute()
        cached_count = len(tenants)
        logger.info("Cache warming completed: tenants", cached_count=cached_count)
    except Exception as e:
        logger.error("Cache warming failed:", error=e)
        return 0

    return cached_count


async def warm_tier_cache(session: AsyncSession) -> int:
    """Preload all tier names into Redis cache.

    Returns
    -------
        Number of tiers cached.
    """
    from src.app.core.utils.cache import client as redis_client

    if redis_client is None:
        logger.warning("Redis client not available, skipping tier cache warming")
        return 0

    stmt = select(Tier)
    result = await session.execute(stmt)
    tiers = result.scalars().all()

    cached_count = 0
    try:
        async with redis_client.pipeline() as pipe:
            for tier in tiers:
                cache_key = f"tier:name:{tier.id}"
                pipe.setex(cache_key, TIER_CACHE_TTL, tier.name)
            await pipe.execute()
        cached_count = len(tiers)
        logger.info("Tier cache warming completed: tiers", cached_count=cached_count)
    except Exception as e:
        logger.error("Tier cache warming failed:", error=e)
        return 0

    return cached_count


async def warm_caches() -> CacheWarmingResult:
    """Warm all caches on application startup.

    Returns
    -------
        CacheWarmingResult with counts and timing.
    """
    start_time = time.perf_counter()
    result = CacheWarmingResult()
    errors: list[str] = []

    try:
        async with local_session() as session:
            # Warm tenant cache
            try:
                result.tenants_cached = await warm_tenant_cache(session)
                logger.info("Cached %d active tenants", result.tenants_cached)
            except Exception as e:
                error_msg = f"Tenant cache warming failed: {e}"
                logger.error(error_msg)
                errors.append(error_msg)

            # Warm tier cache
            try:
                result.tiers_cached = await warm_tier_cache(session)
                logger.info("Cached %d tiers", result.tiers_cached)
            except Exception as e:
                error_msg = f"Tier cache warming failed: {e}"
                logger.error(error_msg)
                errors.append(error_msg)

    except Exception as e:
        error_msg = f"Cache warming failed: {e}"
        logger.error(error_msg)
        errors.append(error_msg)

    result.duration_ms = (time.perf_counter() - start_time) * 1000
    result.errors = errors if errors else None

    logger.info(
        "Cache warming complete | tenants=%d | tiers=%d | duration=%.2fms",
        result.tenants_cached,
        result.tiers_cached,
        result.duration_ms,
    )

    return result
