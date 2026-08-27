"""Unit tests for L1 Tenant Cache (R2-HYBRID MED-1).

Tests the in-memory TTLCache layer that reduces Redis calls for hot tenants.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.middleware.tenant import _L1_TENANT_CACHE


@pytest.fixture(autouse=True)
def clear_l1_cache():
    """Clear L1 cache before each test."""
    _L1_TENANT_CACHE.clear()
    yield
    _L1_TENANT_CACHE.clear()


class TestL1CacheDirectAccess:
    """Test L1 cache direct access (no middleware, pure cache logic)."""

    def test_l1_cache_stores_and_retrieves_tenant_data(self):
        """Verify L1 cache can store and retrieve tenant data."""
        tenant_data = {"id": "tenant-123", "slug": "acme", "is_active": True}

        # Store
        _L1_TENANT_CACHE["acme"] = tenant_data

        # Retrieve
        assert "acme" in _L1_TENANT_CACHE
        assert _L1_TENANT_CACHE["acme"]["id"] == "tenant-123"
        assert _L1_TENANT_CACHE["acme"]["is_active"] is True

    def test_l1_cache_stores_negative_cache(self):
        """Verify L1 cache can store None for negative caching."""
        # Store None (negative cache)
        _L1_TENANT_CACHE["nonexistent"] = None

        # Retrieve - key exists but value is None
        assert "nonexistent" in _L1_TENANT_CACHE
        assert _L1_TENANT_CACHE["nonexistent"] is None

    def test_l1_cache_miss(self):
        """Verify L1 cache returns None for missing keys."""
        assert _L1_TENANT_CACHE.get("unknown") is None
        assert "unknown" not in _L1_TENANT_CACHE

    def test_l1_cache_has_maxsize(self):
        """Verify L1 cache respects maxsize limit."""
        from src.app.middleware.tenant import _L1_TENANT_CACHE

        # Should be configured with maxsize=1000
        assert _L1_TENANT_CACHE.maxsize == 1000

    def test_l1_cache_has_ttl(self):
        """Verify L1 cache has TTL configured."""
        from src.app.middleware.tenant import _L1_TENANT_CACHE

        # Should have TTL of 60 seconds
        assert _L1_TENANT_CACHE.ttl == 60.0


class TestL1CacheIntegration:
    """Test L1 cache integration with middleware (mocked dependencies)."""

    @pytest.mark.asyncio
    async def test_l1_cache_hit_skips_redis(self):
        """Verify L1 cache hit skips Redis lookup."""
        from uuid import UUID

        from src.app.middleware.tenant import TenantMiddleware

        # Populate L1 cache
        _L1_TENANT_CACHE["cached-tenant"] = {
            "id": "00000000-0000-0000-0000-000000000001",
            "slug": "cached-tenant",
            "is_active": True,
        }

        # Create middleware with mocked app
        middleware = TenantMiddleware(MagicMock())

        # Mock Redis to track if it was called
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=AssertionError("Redis should not be called on L1 hit"))

        # Act - call internal method with mocked Redis
        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await middleware._get_tenant_by_slug("cached-tenant")

        # Assert - got result from L1 cache (returns TenantContext)
        # _get_tenant_from_cache_data converts string id to UUID
        assert result is not None
        assert result.tenant_id == UUID("00000000-0000-0000-0000-000000000001")
        assert result.tenant_slug == "cached-tenant"

    @pytest.mark.asyncio
    async def test_l1_negative_cache_hit_skips_redis(self):
        """Verify L1 negative cache hit skips Redis lookup."""
        from src.app.middleware.tenant import TenantMiddleware

        # Populate L1 with negative cache
        _L1_TENANT_CACHE["ghost-tenant"] = None

        middleware = TenantMiddleware(MagicMock())
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=AssertionError("Redis should not be called"))

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await middleware._get_tenant_by_slug("ghost-tenant")

        assert result is None

    @pytest.mark.asyncio
    async def test_l2_hit_populates_l1(self):
        """Verify L2 (Redis) hit populates L1 cache."""
        from uuid import UUID

        from src.app.middleware.tenant import TenantMiddleware

        # L1 cache is empty
        assert "redis-tenant" not in _L1_TENANT_CACHE

        middleware = TenantMiddleware(MagicMock())
        mock_redis = AsyncMock()
        # P7: L2 lookup is a single pipeline (exists + get)
        mock_pipe = MagicMock()
        cached_json = '{"id": "00000000-0000-0000-0000-000000000002", "slug": "redis-tenant", "is_active": true}'
        mock_pipe.execute = AsyncMock(return_value=[False, cached_json])
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await middleware._get_tenant_by_slug("redis-tenant")

        # Assert L1 is now populated
        assert "redis-tenant" in _L1_TENANT_CACHE
        assert _L1_TENANT_CACHE["redis-tenant"]["id"] == "00000000-0000-0000-0000-000000000002"
        # Result is TenantContext, _get_tenant_from_cache_data converts string id to UUID
        assert result.tenant_id == UUID("00000000-0000-0000-0000-000000000002")


class TestSlugValidation:
    """Test slug validation before cache lookup."""

    @pytest.mark.asyncio
    async def test_slug_too_long_rejected(self):
        """Verify slugs > 64 chars are rejected before cache lookup."""
        from src.app.middleware.tenant import TenantMiddleware

        long_slug = "a" * 65
        middleware = TenantMiddleware(MagicMock())

        result = await middleware._get_tenant_by_slug(long_slug)

        assert result is None
        # Should not be cached
        assert long_slug not in _L1_TENANT_CACHE
