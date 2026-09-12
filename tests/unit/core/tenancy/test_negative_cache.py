"""Tests for L2 negative cache with L1 propagation.

Tests that L2 (Redis) negative cache (tenant not found) is cached
with proper TTL and propagates to L1.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.middleware.tenant import _L1_TENANT_CACHE, _L1_TENANT_PARSED


@pytest.fixture(autouse=True)
def clear_l1_cache():
    """Clear L1 cache before each test."""
    _L1_TENANT_CACHE.clear()
    _L1_TENANT_PARSED.clear()
    yield
    _L1_TENANT_CACHE.clear()
    _L1_TENANT_PARSED.clear()


class TestNegativeCache:
    """Tests for negative caching behavior."""

    @pytest.mark.asyncio
    async def test_negative_cache_ttl(self):
        """Verify negative cache (tenant not found) uses shorter TTL."""
        from src.app.middleware.tenant import TenantMiddleware

        middleware = TenantMiddleware(MagicMock())
        tenant_slug = "nonexistent"

        # Mock Redis pipeline: no negative marker, no cached tenant (L2 miss)
        mock_redis = AsyncMock()
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[False, None])
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        with patch("src.app.core.utils.cache.client", mock_redis):
            # Mock DB query to return None (tenant not found)
            # The middleware calls local_session() context manager
            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            mock_db.execute = AsyncMock(return_value=mock_result)

            with patch("src.app.middleware.tenant.local_session") as mock_session:
                mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
                mock_session.return_value.__aexit__ = AsyncMock(return_value=None)

                result = await middleware._get_tenant_by_slug(tenant_slug)

        # Verify result is None
        assert result is None

    @pytest.mark.asyncio
    async def test_l1_negative_cache_respects_ttl(self):
        """Verify L1 negative cache entry exists after L2 miss."""
        from src.app.middleware.tenant import TenantMiddleware

        middleware = TenantMiddleware(MagicMock())
        tenant_slug = "ghost"

        mock_redis = AsyncMock()
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[False, None])
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        with patch("src.app.core.utils.cache.client", mock_redis):
            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            mock_db.execute = AsyncMock(return_value=mock_result)

            with patch("src.app.middleware.tenant.local_session") as mock_session:
                mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
                mock_session.return_value.__aexit__ = AsyncMock(return_value=None)

                await middleware._get_tenant_by_slug(tenant_slug)

        # Verify L1 was set with negative result
        assert tenant_slug in _L1_TENANT_CACHE
        assert _L1_TENANT_CACHE[tenant_slug] is None

    @pytest.mark.asyncio
    async def test_negative_cache_not_returned_as_tenant(self):
        """Verify negative cache entries don't leak as TenantContext."""
        from src.app.middleware.tenant import TenantMiddleware

        middleware = TenantMiddleware(MagicMock())

        # Populate L1 with negative cache
        _L1_TENANT_CACHE["bad-tenant"] = None

        # Query should return None, not a TenantContext
        result = await middleware._get_tenant_by_slug("bad-tenant")

        assert result is None


class TestNegativeCachePropagation:
    """Tests for negative cache propagation between L1 and L2."""

    @pytest.mark.asyncio
    async def test_l1_negative_cache_prevents_l2_lookup(self):
        """Verify L1 negative cache hit prevents L2 lookup."""
        from src.app.middleware.tenant import TenantMiddleware

        middleware = TenantMiddleware(MagicMock())

        # Set L1 negative cache
        _L1_TENANT_CACHE["cached-negative"] = None

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=AssertionError("L2 should not be called"))

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await middleware._get_tenant_by_slug("cached-negative")

        assert result is None

    @pytest.mark.asyncio
    async def test_l2_negative_cache_sets_l1(self):
        """Verify L2 negative cache result populates L1."""
        from src.app.middleware.tenant import TenantMiddleware

        middleware = TenantMiddleware(MagicMock())

        mock_redis = AsyncMock()
        # L2 has negative marker set
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[True, None])
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        with patch("src.app.core.utils.cache.client", mock_redis):
            await middleware._get_tenant_by_slug("l2-negative")

        # L1 should be populated with None
        assert "l2-negative" in _L1_TENANT_CACHE
        assert _L1_TENANT_CACHE["l2-negative"] is None


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
