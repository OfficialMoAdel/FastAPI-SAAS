"""Cache Warmer for managing cache warming operations.

Provides a registry-based system for registering and executing cache warmers
with error handling and statistics tracking.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import local_session
from src.app.core.logger import get_logger
from src.app.core.tenancy.cache import TENANT_CACHE_TTL as _TENANT_CACHE_TTL
from src.app.modules.rate_limits.models import Tier
from src.app.modules.tenants.models import Tenant

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 30.0

# Cache TTLs (in seconds)
# B4: tenant entries use the canonical 300s TTL (see core/tenancy/cache.py)
# so a warm entry cannot outlive a concurrent deactivation's re-cache by an
# hour. Tier entries keep their own 1h TTL.
TENANT_CACHE_TTL = _TENANT_CACHE_TTL
TIER_CACHE_TTL = 3600  # 1 hour


@dataclass
class CacheWarmingResult:
    """Results from cache warming operation."""

    tenants_cached: int = 0
    tiers_cached: int = 0
    duration_ms: float = 0.0
    errors: list[str] | None = None


class CacheWarmer:
    """Registry-based cache warming system.

    Manages a collection of cache warmers with safe execution,
    error isolation, and comprehensive result tracking.
    """

    def __init__(self) -> None:
        self._warmers: dict[str, Callable[[AsyncSession], Awaitable[int]]] = {}

    def register(
        self, name: str
    ) -> Callable[[Callable[[AsyncSession], Awaitable[int]]], Callable[[AsyncSession], Awaitable[int]]]:
        """Decorator to register a cache warmer."""

        def decorator(func: Callable[[AsyncSession], Awaitable[int]]) -> Callable[[AsyncSession], Awaitable[int]]:
            if name in self._warmers:
                logger.warning("Overwriting existing warmer:", name=name)
            self._warmers[name] = func
            logger.debug("Registered cache warmer:", name=name)
            return func

        return decorator

    async def warm_all(
        self,
        session: AsyncSession | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> CacheWarmingResult:
        """Warm all registered caches.

        A single AsyncSession cannot serve concurrent operations, so when a
        caller-provided session is given the warmers run sequentially on it.
        When no session is provided, warmers run concurrently (P3-5
        Optimization), each with its own session.
        """
        start_time = time.time()
        result = CacheWarmingResult()
        errors: list[str] = []

        logger.info("[START] Cache warming started at | warmers=", start_time=start_time, val=len(self._warmers))

        async def _run_single_warmer(
            name: str, warmer_fn: Any, warmer_session: AsyncSession | None
        ) -> tuple[str, int | None, str | None]:
            warmer_start = time.time()
            try:
                if warmer_session is not None:
                    cached_count = await asyncio.wait_for(warmer_fn(warmer_session), timeout=timeout)
                else:
                    from src.app.core.db.database import async_session_maker

                    async with async_session_maker() as owned_session:
                        cached_count = await asyncio.wait_for(warmer_fn(owned_session), timeout=timeout)

                warmer_duration = time.time() - warmer_start
                logger.info(
                    "[SUCCESS] Warmer=%s | items=%d | duration=%.2fs",
                    name,
                    cached_count,
                    warmer_duration,
                )
                return name, cached_count, None

            except TimeoutError:
                warmer_duration = time.time() - warmer_start
                error_msg = f"{name}: Timeout after {timeout}s"
                logger.error(
                    "[TIMEOUT] Warmer=%s | duration=%.2fs | error=%s",
                    name,
                    warmer_duration,
                    error_msg,
                )
                return name, None, error_msg
            except Exception as e:
                warmer_duration = time.time() - warmer_start
                error_msg = f"{name}: {str(e)}"
                logger.error(
                    "[ERROR] Warmer=%s | duration=%.2fs | error=%s",
                    name,
                    warmer_duration,
                    error_msg,
                    exc_info=True,
                )
                return name, None, error_msg

        # Run warmers sequentially on the caller's session (an AsyncSession must
        # not be shared across concurrent operations), or concurrently with
        # per-warmer sessions when none was provided.
        if session is not None:
            outcomes = [await _run_single_warmer(name, fn, session) for name, fn in self._warmers.items()]
        else:
            outcomes = await asyncio.gather(*(_run_single_warmer(name, fn, None) for name, fn in self._warmers.items()))

        for name, count, err in outcomes:
            if err is not None:
                errors.append(err)
            elif count is not None:
                if name == "tenant":
                    result.tenants_cached = count
                elif name == "tier":
                    result.tiers_cached = count

        result.errors = errors if errors else None
        total_duration = time.time() - start_time

        logger.info(
            "[COMPLETE] Cache warming finished | total_duration=%.2fs | tenants=%d | tiers=%d | errors=%d",
            total_duration,
            result.tenants_cached,
            result.tiers_cached,
            len(errors) if errors else 0,
        )

        return result

    async def warm_specific(
        self,
        name: str,
        session: AsyncSession,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> CacheWarmingResult:
        """Warm a specific cache."""
        result = CacheWarmingResult()
        errors: list[str] = []

        if name not in self._warmers:
            error_msg = f"Unknown cache warmer: {name}"
            logger.error("[UNKNOWN] Warmer= | error=", name=name, error_msg=error_msg)
            return CacheWarmingResult(errors=[error_msg])

        warmer_fn = self._warmers[name]
        start_time = time.time()

        try:
            cached_count: int = await asyncio.wait_for(warmer_fn(session), timeout=timeout)
            duration = time.time() - start_time
            logger.info(
                "[SUCCESS] Warmer=%s | items=%d | duration=%.2fs",
                name,
                cached_count,
                duration,
            )

            if name == "tenant":
                result.tenants_cached = cached_count
            elif name == "tier":
                result.tiers_cached = cached_count

        except TimeoutError:
            duration = time.time() - start_time
            error_msg = f"{name}: Timeout after {timeout}s"
            logger.error(
                "[TIMEOUT] Warmer=%s | duration=%.2fs | error=%s",
                name,
                duration,
                error_msg,
            )
            errors.append(error_msg)
        except Exception as e:
            duration = time.time() - start_time
            error_msg = f"{name}: {str(e)}"
            logger.error(
                "[ERROR] Warmer=%s | duration=%.2fs | error=%s",
                name,
                duration,
                error_msg,
                exc_info=True,
            )
            errors.append(error_msg)

        result.errors = errors if errors else None

        return result

    def list_warmers(self) -> list[str]:
        """Get list of registered warmer names."""
        return list(self._warmers.keys())

    def has_warmer(self, name: str) -> bool:
        """Check if a warmer is registered."""
        return name in self._warmers


warmer = CacheWarmer()


# Specific warming functions


@warmer.register("tenant")
async def warm_tenant_cache(session: AsyncSession) -> int:
    """Preload all active tenant slugs into Redis cache."""
    from src.app.core.redis_provider import get_redis

    redis_client = get_redis()

    if redis_client is None:
        logger.warning("Redis client not available, skipping tenant cache warming")
        return 0

    import orjson

    stmt = select(Tenant).where(Tenant.is_active.is_(True))
    result = await session.execute(stmt)
    tenants = list(result.scalars().all())
    # M4: cap unbounded warmer load
    if len(tenants) > 1000:
        logger.warning("Warmer tenant fetch capped", total=len(tenants), cap=1000)
        tenants = tenants[:1000]

    cached_count = 0
    try:
        async with redis_client.pipeline() as pipe:
            for tenant in tenants:
                cache_key = f"tenant:slug:{tenant.slug}"
                cache_data = orjson.dumps(
                    {
                        "id": str(tenant.id),
                        "slug": tenant.slug,
                        "name": tenant.name,
                        "is_active": tenant.is_active,
                        "feature_flags": tenant.feature_flags,
                        "storage_usage_bytes": tenant.storage_usage_bytes,
                        "file_count": tenant.file_count,
                    }
                )
                pipe.setex(cache_key, TENANT_CACHE_TTL, cache_data)
            await pipe.execute()
        cached_count = len(tenants)
        logger.info("Cache warming completed: tenants", cached_count=cached_count)
    except Exception as e:
        logger.error("Cache warming failed", error=e)
        return 0

    return cached_count


@warmer.register("tier")
async def warm_tier_cache(session: AsyncSession) -> int:
    """Preload all tier names into Redis cache."""
    from src.app.core.redis_provider import get_redis

    redis_client = get_redis()

    if redis_client is None:
        logger.warning("Redis client not available, skipping tier cache warming")
        return 0

    stmt = select(Tier)
    result = await session.execute(stmt)
    tiers = list(result.scalars().all())
    # M4: cap unbounded warmer load
    if len(tiers) > 1000:
        logger.warning("Warmer tier fetch capped", total=len(tiers), cap=1000)
        tiers = tiers[:1000]

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
        logger.error("Tier cache warming failed", error=e)
        return 0

    return cached_count


@warmer.register("feature_flags")
async def warm_feature_flags_cache(session: AsyncSession) -> int:
    """Preload tenant feature-flag overrides into the cache."""
    # Late import: the feature_flags service imports cache utils at module
    # level, which import this registry — a top-level import here would be
    # circular.
    from src.app.modules.feature_flags.service import warm_feature_flags

    return await warm_feature_flags(session)


async def warm_caches() -> CacheWarmingResult:
    """Warm all caches on application startup."""
    try:
        async with local_session() as session:
            return await warmer.warm_all(session)
    except Exception as e:
        error_msg = f"Cache warming failed: {e}"
        logger.error("Cache warming failed", error=str(e))
        return CacheWarmingResult(errors=[error_msg])


__all__ = [
    "CacheWarmer",
    "warmer",
    "CacheWarmingResult",
    "warm_tenant_cache",
    "warm_tier_cache",
    "warm_caches",
]
