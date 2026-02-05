"""Cache Warmer for managing cache warming operations.

Provides a registry-based system for registering and executing cache warmers
with error handling and statistics tracking.
"""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.utils.cache_warming import CacheWarmingResult

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


class CacheWarmer:
    """Registry-based cache warming system.

    Manages a collection of cache warmers with safe execution,
    error isolation, and comprehensive result tracking.
    """

    def __init__(self) -> None:
        self._warmers: dict[str, Any] = {}

    def register(self, name: str) -> Any:
        """Decorator to register a cache warmer.

        Args:
            name: Unique name for the warmer.

        Returns:
            Decorator function.

        Example:
            @warmer.register("tenant")
            async def warm_tenants(session: AsyncSession) -> int:
                ...
        """

        def decorator(func: Any) -> Any:
            if name in self._warmers:
                logger.warning(f"Overwriting existing warmer: {name}")
            self._warmers[name] = func
            logger.debug(f"Registered cache warmer: {name}")
            return func

        return decorator

    async def warm_all(
        self,
        session: AsyncSession,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> CacheWarmingResult:
        """Warm all registered caches.

        Args:
            session: Database session to use for warming.
            timeout: Maximum time per warmer in seconds.

        Returns:
            CacheWarmingResult with detailed results.
        """
        import asyncio
        import time

        start_time = time.time()
        result = CacheWarmingResult()
        errors: list[str] = []

        logger.info(f"[START] Cache warming started at {start_time:.2f} | warmers={len(self._warmers)}")

        for name, warmer in self._warmers.items():
            warmer_start = time.time()
            try:
                cached_count: int = await asyncio.wait_for(warmer(session), timeout=timeout)
                warmer_duration = time.time() - warmer_start
                logger.info(
                    "[SUCCESS] Warmer=%s | items=%d | duration=%.2fs",
                    name,
                    cached_count,
                    warmer_duration,
                )

                if name == "tenant":
                    result.tenants_cached = cached_count
                elif name == "tier":
                    result.tiers_cached = cached_count

            except TimeoutError:
                warmer_duration = time.time() - warmer_start
                error_msg = f"{name}: Timeout after {timeout}s"
                logger.error(
                    "[TIMEOUT] Warmer=%s | duration=%.2fs | error=%s",
                    name,
                    warmer_duration,
                    error_msg,
                )
                errors.append(error_msg)
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
                errors.append(error_msg)

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
        """Warm a specific cache.

        Args:
            name: Name of the warmer to execute.
            session: Database session to use.
            timeout: Maximum time in seconds.

        Returns:
            CacheWarmingResult with results.
        """
        import asyncio
        import time

        result = CacheWarmingResult()
        errors: list[str] = []

        if name not in self._warmers:
            error_msg = f"Unknown cache warmer: {name}"
            logger.error(f"[UNKNOWN] Warmer={name} | error={error_msg}")
            return CacheWarmingResult(errors=[error_msg])

        warmer = self._warmers[name]
        start_time = time.time()

        try:
            cached_count: int = await asyncio.wait_for(warmer(session), timeout=timeout)
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
        """Get list of registered warmer names.

        Returns:
            List of warmer names.
        """
        return list(self._warmers.keys())

    def has_warmer(self, name: str) -> bool:
        """Check if a warmer is registered.

        Args:
            name: Name of the warmer.

        Returns:
            True if warmer exists, False otherwise.
        """
        return name in self._warmers


warmer = CacheWarmer()


__all__ = [
    "CacheWarmer",
    "warmer",
]
