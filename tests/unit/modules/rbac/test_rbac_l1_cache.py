"""Unit tests for L1 RBAC Cache (R2-HYBRID MED-2).

Tests the in-memory TTLCache layer for role permissions.
"""

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
        assert _L1_RBAC_CACHE.ttl == 5.0


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
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=AssertionError("Redis should not be called"))
        mock_db.execute = AsyncMock(side_effect=AssertionError("DB should not be called"))

        with patch("src.app.modules.rbac.service.settings") as mock_settings:
            with patch("src.app.modules.rbac.service.record_rbac_cache_hit"):
                mock_settings.r1_unified.RBAC_USE_REDIS_CACHE = True

                service = RoleService(db=mock_db, tenant_id=tenant_id, redis_client=mock_redis)

                # Act
                permissions = await service.get_role_permissions(role_id)

        # Assert
        assert len(permissions) == 1
        assert permissions[0].slug == "read"


class TestL1CacheMissL2Hit:
    """Test L1 miss but L2 (Redis) hit."""

    @pytest.mark.asyncio
    async def test_l2_hit_populates_l1(self):
        """Verify L2 hit populates L1 cache."""
        tenant_id = "tenant-xyz"
        role_id = 20
        l1_key = f"{tenant_id}:{role_id}"

        # L1 is empty
        assert l1_key not in _L1_RBAC_CACHE

        # Mock Redis with cached data
        mock_db = AsyncMock()
        mock_redis = AsyncMock()
        cached_data = '[{"id": 5, "slug": "admin", "description": "Admin", "created_at": "2024-01-01T00:00:00", "updated_at": "2024-01-01T00:00:00"}]'
        mock_redis.get = AsyncMock(return_value=cached_data)

        with patch("src.app.modules.rbac.service.settings") as mock_settings:
            with patch("src.app.modules.rbac.service.record_rbac_cache_hit"):
                mock_settings.r1_unified.RBAC_USE_REDIS_CACHE = True

                service = RoleService(db=mock_db, tenant_id=tenant_id, redis_client=mock_redis)

                # Act
                permissions = await service.get_role_permissions(role_id)

        # Assert - L1 should now be populated
        assert l1_key in _L1_RBAC_CACHE
        assert len(permissions) == 1
        assert permissions[0].slug == "admin"


class TestCacheInvalidation:
    """Test cache invalidation clears both L1 and L2."""

    @pytest.mark.asyncio
    async def test_invalidate_role_clears_l1_and_l2(self):
        """Verify role invalidation clears both L1 and L2 caches."""
        tenant_id = "tenant-inv"
        role_id = 30
        l1_key = f"{tenant_id}:{role_id}"

        # Populate L1 cache
        _L1_RBAC_CACHE[l1_key] = [{"id": 1, "slug": "test"}]
        assert l1_key in _L1_RBAC_CACHE

        # Mock Redis
        mock_db = AsyncMock()
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock()

        service = RoleService(db=mock_db, tenant_id=tenant_id, redis_client=mock_redis)

        # Act
        await service._invalidate_role_cache(role_id)

        # Assert
        assert l1_key not in _L1_RBAC_CACHE
        mock_redis.delete.assert_called_once()

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
        mock_redis = AsyncMock()
        mock_redis.scan = AsyncMock(return_value=(0, []))

        service = RoleService(db=mock_db, tenant_id=tenant_id, redis_client=mock_redis)

        # Act
        await service._invalidate_all_role_caches()

        # Assert - tenant entries cleared, other tenant preserved
        assert f"{tenant_id}:1" not in _L1_RBAC_CACHE
        assert f"{tenant_id}:2" not in _L1_RBAC_CACHE
        assert "other-tenant:1" in _L1_RBAC_CACHE
