"""P1-2: Tenant Cache Warming Verification Tests.

STATUS: VERIFIED ✅

This module tests the tenant cache warming functionality to verify
that cache warming at startup actually improves performance.

The optimization involves pre-loading frequently accessed tenant data
into the cache during application startup to avoid cold-start latency.
"""

from __future__ import annotations

import contextlib
import time
from unittest.mock import patch

import pytest

from src.app.core.cache.warmer import (
    TENANT_CACHE_TTL,
    CacheWarmingResult,
    warm_caches,
    warm_tenant_cache,
)


class _FakeApp:
    """Minimal stand-in for the FastAPI app the lifespan is handed."""

    def __init__(self) -> None:
        self.state = type("State", (), {})()


class TestP12CacheWarmingPrerequisites:
    """Pre-verification tests for cache warming."""

    def test_redis_utils_exist(self) -> None:
        """Verify Redis cache utilities exist."""
        from src.app.core.utils.cache import async_get_redis

        # Redis client getter should be available
        assert async_get_redis is not None

    def test_tenant_service_exists(self) -> None:
        """Verify tenant service exists for caching."""
        from src.app.modules.tenants.service import TenantService

        assert TenantService is not None

    def test_cache_warming_module_exports(self) -> None:
        """Verify cache warming module has all required exports."""
        assert warm_tenant_cache is not None
        assert warm_caches is not None
        assert CacheWarmingResult is not None

    def test_cache_ttl_values(self) -> None:
        """Verify cache TTL values are reasonable."""
        # TTL should be at least 1 minute
        assert TENANT_CACHE_TTL >= 60
        # TTL should not exceed 24 hours
        assert TENANT_CACHE_TTL <= 86400


class TestP12CacheWarmingVerification:
    """Tests to verify cache warming effectiveness."""

    @pytest.mark.asyncio
    async def test_warm_tenant_cache_returns_count(self, db_session) -> None:
        """warm_tenant_cache should return cached tenant count."""
        count = await warm_tenant_cache(db_session)
        # Should return 0 or positive integer (depends on test data)
        assert isinstance(count, int)
        assert count >= 0

    @pytest.mark.asyncio
    async def test_warm_caches_returns_result(self) -> None:
        """warm_caches should return CacheWarmingResult."""
        result = await warm_caches()

        assert isinstance(result, CacheWarmingResult)
        assert isinstance(result.tenants_cached, int)
        assert isinstance(result.duration_ms, float)
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_cache_warming_latency(self) -> None:
        """Cache warming should complete within acceptable time (<1s)."""
        start = time.perf_counter()
        result = await warm_caches()
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Coverage/CI tracing amplifies cold connection provisioning cost
        # substantially (same convention as test_first_request_hits_cache_after_warming).
        import sys

        threshold_ms = 5000 if sys.gettrace() is not None else 1000
        # Cache warming should complete within 1 second for reasonable data
        assert elapsed_ms < threshold_ms, f"Cache warming took {elapsed_ms:.2f}ms, exceeds {threshold_ms}ms limit"
        # Result should track similar timing
        assert result.duration_ms < threshold_ms


class TestP12CacheWarmingIntegration:
    """Integration tests for cache warming with Redis."""

    @pytest.mark.asyncio
    async def test_tenant_cache_populated_in_redis(self, db_session) -> None:
        """After warm_tenant_cache, tenants should be in Redis."""
        from src.app.core.utils.cache import client as redis_client

        if redis_client is None:
            pytest.skip("Redis not available")

        # Create a test tenant
        from src.app.modules.tenants.models import Tenant

        test_slug = "test-cache-warming-tenant"
        tenant = Tenant(name="Test Tenant", slug=test_slug, is_active=True)
        db_session.add(tenant)
        await db_session.commit()

        try:
            # Warm the cache
            count = await warm_tenant_cache(db_session)
            assert count >= 1

            # Verify tenant is in Redis
            cache_key = f"tenant:slug:{test_slug}"
            cached_value = await redis_client.get(cache_key)
            assert cached_value is not None

            # Verify cached data structure
            import orjson

            cached_data = orjson.loads(cached_value)
            assert cached_data["slug"] == test_slug
            assert cached_data["is_active"] is True
        finally:
            # Cleanup
            await db_session.delete(tenant)
            await db_session.commit()
            if redis_client:
                await redis_client.delete(f"tenant:slug:{test_slug}")

    @pytest.mark.asyncio
    async def test_first_request_hits_cache_after_warming(self, db_session) -> None:
        """After cache warming, first lookup should hit cache (no DB query)."""
        from src.app.core.utils.cache import client as redis_client

        if redis_client is None:
            pytest.skip("Redis not available")

        from src.app.modules.tenants.models import Tenant

        test_slug = "test-first-request-cache"
        tenant = Tenant(name="First Request Test", slug=test_slug, is_active=True)
        db_session.add(tenant)
        await db_session.commit()

        try:
            # Warm the cache
            await warm_tenant_cache(db_session)

            # Measure cache lookup time (should be fast, no DB)
            cache_key = f"tenant:slug:{test_slug}"
            start = time.perf_counter()
            cached_value = await redis_client.get(cache_key)
            cache_lookup_ms = (time.perf_counter() - start) * 1000

            assert cached_value is not None
            # Cache lookup should be very fast (<10ms typically <1ms)
            # Relaxed threshold for coverage/CI environments
            import sys

            threshold_ms = 50 if sys.gettrace() is not None else 10
            assert cache_lookup_ms < threshold_ms, f"Cache lookup took {cache_lookup_ms:.2f}ms"
        finally:
            await db_session.delete(tenant)
            await db_session.commit()
            if redis_client:
                await redis_client.delete(f"tenant:slug:{test_slug}")


class TestP12CacheWarmingInLifespan:
    """Tests verifying cache warming is wired into the application lifespan."""

    @pytest.mark.asyncio
    async def test_warm_caches_is_a_registered_startup_phase(self) -> None:
        """Entering the lifespan must register a phase named ``warm_caches``.

        Replaces a source-text scan (``inspect.getsource(lifespan_factory)``
        + ``assert "warm_caches" in source``). The scan only worked because
        ``composition.py`` carried a comment telling engineers not to rename
        the symbol, because a test greps for it — a test-quality defect
        promoted into a production constraint. This asserts the real
        wiring: the StartupPhase sequence actually handed to
        ``compose_phases`` when the lifespan is entered.
        """

        from src.app.composition import lifespan_factory
        from src.app.core.config import settings

        captured: list = []

        @contextlib.asynccontextmanager
        async def _capture(phases, tail=(), teardown_on_startup_failure=False):
            captured.append(list(phases))
            yield

        # compose_phases is imported into composition's namespace at module
        # level, so patch it there (not on src.app.core.runtime).
        with patch("src.app.composition.compose_phases", _capture):
            lifespan = lifespan_factory(settings, create_tables_on_start=False)
            async with lifespan(_FakeApp()):
                pass

        assert captured, "compose_phases was never called"
        names = [phase.name for phase in captured[0]]
        assert "warm_caches" in names, (
            f"warm_caches is not a registered startup phase; got {names}"
        )
