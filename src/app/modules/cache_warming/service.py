"""Cache warming service for preloading frequently accessed data.

Provides safe execution wrapper for cache warming operations with:
- Graceful error handling
- Individual warmer isolation
- Comprehensive result tracking
"""

import asyncio
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.cache.warmer import (
    CacheWarmingResult,
    warm_tenant_cache,
    warm_tier_cache,
)
from src.app.core.logger import get_logger
from src.app.modules.feature_flags.service import warm_feature_flags

logger = get_logger(__name__)

CACHE_WARMING_ENABLED = True
CACHE_WARMING_DELAY = timedelta(seconds=2)

DEFAULT_WARMERS: list[tuple[str, Callable[..., Any]]] = [
    ("tenant", warm_tenant_cache),
    ("tier", warm_tier_cache),
    ("feature_flags", warm_feature_flags),
]


async def warm_cache_safely(
    warmer_name: str,
    warmer_func: Any,
    session: AsyncSession,
    timeout: float = 30.0,
) -> tuple[bool, int | None, Exception | None]:
    """Execute a cache warmer safely with error isolation.

    Args:
        warmer_name: Name of the cache warmer for logging.
        warmer_func: Async function that performs cache warming.
        session: Database session to use for warming.
        timeout: Maximum time to wait for warming to complete.

    Returns:
        Tuple of (success, cached_count, error).
    """

    try:
        cached_count: int = await asyncio.wait_for(warmer_func(session), timeout=timeout)
        return True, cached_count, None
    except TimeoutError as e:
        logger.error("Cache warming timeout for :", warmer_name=warmer_name, error=e)
        return False, None, e
    except Exception as e:
        logger.error("Cache warming failed for :", warmer_name=warmer_name, error=e)
        return False, None, e


async def warm_all_caches(
    warmers: Any | None = None,
    session: AsyncSession | None = None,
    enabled: bool | None = None,
) -> CacheWarmingResult:
    """Warm all caches with safe execution wrapper.

    Args:
        warmers: List of (name, warmer_func) tuples. Defaults to DEFAULT_WARMERS.
        session: Database session. If None, creates a new session.
        enabled: Whether cache warming is enabled. Defaults to CACHE_WARMING_ENABLED.

    Returns:
        CacheWarmingResult with detailed results.
    """
    if enabled is None:
        enabled = CACHE_WARMING_ENABLED

    if not enabled:
        logger.info("Cache warming is disabled")
        return CacheWarmingResult()

    if warmers is None:
        warmers = DEFAULT_WARMERS

    from src.app.core.db.database import local_session

    result = CacheWarmingResult()
    errors: list[str] = []

    if session is None:
        async with local_session() as sess:
            for name, warmer in warmers:
                success, count, error = await warm_cache_safely(name, warmer, sess)
                if success and count is not None:
                    if name == "tenant":
                        result.tenants_cached = count
                    elif name == "tier":
                        result.tiers_cached = count
                elif error:
                    errors.append(f"{name}: {str(error)}")
    else:
        for name, warmer in warmers:
            success, count, error = await warm_cache_safely(name, warmer, session)
            if success and count is not None:
                if name == "tenant":
                    result.tenants_cached = count
                elif name == "tier":
                    result.tiers_cached = count
            elif error:
                errors.append(f"{name}: {str(error)}")

    result.errors = errors if errors else None

    logger.info(
        "Cache warming complete | tenants=%d | tiers=%d | errors=%d",
        result.tenants_cached,
        result.tiers_cached,
        len(errors) if errors else 0,
    )

    return result


async def warm_specific_cache(
    cache_type: str,
    session: AsyncSession,
) -> CacheWarmingResult:
    """Warm a specific cache type.

    Args:
        cache_type: Type of cache to warm ('tenant' or 'tier').
        session: Database session to use.

    Returns:
        CacheWarmingResult with results.
    """
    warmer_map = dict(DEFAULT_WARMERS)

    if cache_type not in warmer_map:
        logger.error("Unknown cache type:", cache_type=cache_type)
        return CacheWarmingResult(errors=[f"Unknown cache type: {cache_type}"])

    warmer = warmer_map[cache_type]
    result = CacheWarmingResult()
    errors: list[str] = []

    success, count, error = await warm_cache_safely(cache_type, warmer, session)

    if success and count is not None:
        if cache_type == "tenant":
            result.tenants_cached = count
        elif cache_type == "tier":
            result.tiers_cached = count
    elif error:
        errors.append(f"{cache_type}: {str(error)}")

    result.errors = errors if errors else None

    return result


__all__ = [
    "warm_cache_safely",
    "warm_all_caches",
    "warm_specific_cache",
    "CacheWarmingResult",
]
