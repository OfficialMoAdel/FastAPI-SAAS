"""Unit tests for L1 RBAC Cache (R2-HYBRID MED-2).

Tests the in-memory TTLCache layer for role permissions.
"""

import warnings
from unittest.mock import AsyncMock, patch

import pytest

from src.app.modules.rbac.service import _L1_RBAC_CACHE, RoleService


@pytest.fixture(autouse=True)
def clear_l1_cache():
    """Clear L1 cache before each test."""
    _L1_RBAC_CACHE.clear()
    yield
    _L1_RBAC_CACHE.clear()


class TestL1RBACCacheDirectAccess:
    """Test L1 RBAC cache direct access."""

    def test_l1_cache_stores_permissions(self):
        """Verify L1 cache can store permission dicts."""
        perms = [
            {"id": 1, "slug": "users:read", "description": "Read users"},
            {"id": 2, "slug": "users:write", "description": "Write users"},
        ]

        _L1_RBAC_CACHE["tenant-1:role-5"] = perms

        assert "tenant-1:role-5" in _L1_RBAC_CACHE
        assert len(_L1_RBAC_CACHE["tenant-1:role-5"]) == 2

    def test_l1_cache_has_maxsize(self):
        """Verify L1 cache has maxsize configured."""
        assert _L1_RBAC_CACHE.maxsize == 500

    def test_l1_cache_has_ttl(self):
        """Verify L1 cache has TTL configured."""
        assert _L1_RBAC_CACHE.ttl == 30.0


class TestL1CacheHit:
    """Test L1 cache hit scenarios."""

    @pytest.mark.asyncio
    async def test_l1_hit_skips_redis_and_db(self):
        """Verify L1 hit returns permissions without Redis/DB calls."""
        # Arrange - populate L1 cache
        tenant_id = "tenant-abc"
        role_id = 10
        perm_dicts = [
            {
                "id": 1,
                "slug": "read",
                "description": "Read",
                "created_at": "2024-01-01T00:00:00",
                "updated_at": "2024-01-01T00:00:00",
            },
        ]
        _L1_RBAC_CACHE[f"{tenant_id}:{role_id}"] = perm_dicts

        # Create service with mocked dependencies
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=AssertionError("DB should not be called"))

        with patch("src.app.modules.rbac.service.record_rbac_cache_hit") as mock_hit:
            service = RoleService(db=mock_db, tenant_id=tenant_id)

            # Act
            permissions = await service.get_role_permissions(role_id)

        # Assert
        assert len(permissions) == 1
        assert permissions[0].slug == "read"
        mock_hit.assert_called_once()
        mock_db.execute.assert_not_called()


class TestCacheInvalidation:
    """Test cache invalidation clears L1 only."""

    @pytest.mark.asyncio
    async def test_invalidate_role_clears_l1(self):
        """Verify role invalidation clears L1 without touching Redis."""
        tenant_id = "tenant-inv"
        role_id = 30
        l1_key = f"{tenant_id}:{role_id}"

        # Populate L1 cache
        _L1_RBAC_CACHE[l1_key] = [{"id": 1, "slug": "test"}]
        assert l1_key in _L1_RBAC_CACHE

        # Mock DB
        mock_db = AsyncMock()

        service = RoleService(db=mock_db, tenant_id=tenant_id)

        # Act
        await service._invalidate_role_cache(role_id)

        # Assert
        assert l1_key not in _L1_RBAC_CACHE

    @pytest.mark.asyncio
    async def test_invalidate_all_clears_tenant_entries(self):
        """Verify invalidate_all clears all L1 entries for tenant."""
        tenant_id = "tenant-all"

        # Populate L1 with multiple entries
        _L1_RBAC_CACHE[f"{tenant_id}:1"] = [{"id": 1}]
        _L1_RBAC_CACHE[f"{tenant_id}:2"] = [{"id": 2}]
        _L1_RBAC_CACHE["other-tenant:1"] = [{"id": 3}]  # Should not be cleared

        assert f"{tenant_id}:1" in _L1_RBAC_CACHE
        assert f"{tenant_id}:2" in _L1_RBAC_CACHE

        mock_db = AsyncMock()

        service = RoleService(db=mock_db, tenant_id=tenant_id)

        # Act
        await service._invalidate_all_role_caches()

        # Assert - tenant entries cleared, other tenant preserved
        assert f"{tenant_id}:1" not in _L1_RBAC_CACHE
        assert f"{tenant_id}:2" not in _L1_RBAC_CACHE
        assert "other-tenant:1" in _L1_RBAC_CACHE


class TestRedisClientDeprecation:
    """The L2 Redis tier is gone; the constructor argument must say so."""

    def test_redis_client_argument_warns(self):
        """Passing a client warns instead of silently pretending to use it."""
        with pytest.warns(DeprecationWarning, match="redis_client"):
            RoleService(db=AsyncMock(), tenant_id="tenant-x", redis_client=AsyncMock())

    def test_omitting_redis_client_is_silent(self):
        """The supported construction path emits no warning."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            RoleService(db=AsyncMock(), tenant_id="tenant-x")
