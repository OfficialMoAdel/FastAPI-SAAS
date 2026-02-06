"""
Unit tests for RBAC Permissions Redis Caching (P5).

Tests the Redis caching layer for role permissions that reduces
database queries on permission checks.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestRBACPermissionsCaching:
    """Tests for RoleService permission caching."""

    @pytest.fixture
    def mock_db(self) -> AsyncMock:
        """Create mock database session."""
        db = AsyncMock()
        return db

    @pytest.fixture
    def mock_redis(self) -> AsyncMock:
        """Create mock Redis client."""
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.setex = AsyncMock()
        redis.delete = AsyncMock()
        redis.scan = AsyncMock(return_value=(0, []))  # Empty scan
        return redis

    @pytest.fixture(autouse=True)
    def clear_l1_cache(self):
        """Clear L1 cache before each test."""
        from src.app.modules.rbac.service import _L1_RBAC_CACHE

        _L1_RBAC_CACHE.clear()
        yield
        _L1_RBAC_CACHE.clear()

    @pytest.mark.asyncio
    async def test_cache_hit_skips_database(self, mock_db: AsyncMock, mock_redis: AsyncMock) -> None:
        """Test that cache hit returns cached permissions without DB query."""
        from src.app.modules.rbac.service import RoleService

        cached_data = '[{"id": 1, "slug": "read:users", "description": "Read users", "created_at": "2024-01-01T00:00:00", "updated_at": "2024-01-01T00:00:00"}]'
        mock_redis.get.return_value = cached_data

        with patch("src.app.modules.rbac.service.settings") as mock_settings:
            mock_settings.RBAC_USE_REDIS_CACHE = True
            mock_settings.RBAC_CACHE_TTL_SECONDS = 3600

            with patch("src.app.modules.rbac.service.Permission") as mock_perm_class:
                mock_perm = MagicMock()
                mock_perm.slug = "read:users"
                mock_perm_class.return_value = mock_perm

                service = RoleService(db=mock_db, tenant_id=1, redis_client=mock_redis)

                permissions = await service.get_role_permissions(role_id=5)

                # Should use cached data
                mock_redis.get.assert_called_once()
                assert len(permissions) == 1
                assert permissions[0].slug == "read:users"

                # Database should NOT be queried
                mock_db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_miss_queries_database(self, mock_db: AsyncMock, mock_redis: AsyncMock) -> None:
        """Test that cache miss queries database and caches result."""
        from src.app.modules.rbac.models import Permission, Role
        from src.app.modules.rbac.service import RoleService

        # Cache miss
        mock_redis.get.return_value = None

        # Mock role query for verification
        mock_role = MagicMock(spec=Role)
        mock_role.id = 5
        mock_role.tenant_id = 1

        # Mock permission query results
        mock_permission = MagicMock(spec=Permission)
        mock_permission.id = 1
        mock_permission.slug = "read:users"
        mock_permission.description = "Read users"
        mock_permission.created_at = datetime.now()
        mock_permission.updated_at = datetime.now()

        # Setup db.execute to return role first, then permissions
        mock_result_role = MagicMock()
        mock_result_role.scalar_one_or_none.return_value = mock_role

        mock_result_perms = MagicMock()
        mock_result_perms.scalars.return_value.all.return_value = [mock_permission]

        mock_db.execute.side_effect = [mock_result_role, mock_result_perms]

        with patch("src.app.modules.rbac.service.settings") as mock_settings:
            mock_settings.RBAC_USE_REDIS_CACHE = True
            mock_settings.RBAC_CACHE_TTL_SECONDS = 3600

            service = RoleService(db=mock_db, tenant_id=1, redis_client=mock_redis)

            permissions = await service.get_role_permissions(role_id=5)

            # Should query database
            assert mock_db.execute.call_count == 2  # Role check + permissions

            # Should cache the result
            mock_redis.setex.assert_called_once()
            call_args = mock_redis.setex.call_args
            assert call_args[0][0] == "rbac:perms:1:5"  # cache key
            assert call_args[0][1] == 3600  # TTL

    @pytest.mark.asyncio
    async def test_cache_disabled_always_queries_db(self, mock_db: AsyncMock, mock_redis: AsyncMock) -> None:
        """Test that disabled cache always queries database."""
        from src.app.modules.rbac.models import Role
        from src.app.modules.rbac.service import RoleService

        # Mock role query
        mock_role = MagicMock(spec=Role)
        mock_role.id = 5
        mock_role.tenant_id = 1

        mock_result_role = MagicMock()
        mock_result_role.scalar_one_or_none.return_value = mock_role

        mock_result_perms = MagicMock()
        mock_result_perms.scalars.return_value.all.return_value = []

        mock_db.execute.side_effect = [mock_result_role, mock_result_perms]

        with patch("src.app.modules.rbac.service.settings") as mock_settings:
            mock_settings.RBAC_USE_REDIS_CACHE = False

            service = RoleService(db=mock_db, tenant_id=1, redis_client=mock_redis)

            await service.get_role_permissions(role_id=5)

            # Redis should NOT be called
            mock_redis.get.assert_not_called()
            mock_redis.setex.assert_not_called()

            # Database should be queried
            assert mock_db.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_cache_read_error_falls_back_to_db(self, mock_db: AsyncMock, mock_redis: AsyncMock) -> None:
        """Test that cache read error falls back to database."""
        from src.app.modules.rbac.models import Role
        from src.app.modules.rbac.service import RoleService

        # Redis read fails
        mock_redis.get.side_effect = Exception("Redis connection error")

        # Mock role and permissions
        mock_role = MagicMock(spec=Role)
        mock_role.id = 5
        mock_role.tenant_id = 1

        mock_result_role = MagicMock()
        mock_result_role.scalar_one_or_none.return_value = mock_role

        mock_result_perms = MagicMock()
        mock_result_perms.scalars.return_value.all.return_value = []

        mock_db.execute.side_effect = [mock_result_role, mock_result_perms]

        with patch("src.app.modules.rbac.service.settings") as mock_settings:
            mock_settings.RBAC_USE_REDIS_CACHE = True
            mock_settings.RBAC_CACHE_TTL_SECONDS = 3600

            service = RoleService(db=mock_db, tenant_id=1, redis_client=mock_redis)

            # Should not raise, should fall back to DB
            permissions = await service.get_role_permissions(role_id=5)

            # Database should be queried
            assert mock_db.execute.call_count == 2


class TestRBACCacheInvalidation:
    """Tests for RBAC cache invalidation."""

    @pytest.fixture
    def mock_db(self) -> AsyncMock:
        """Create mock database session."""
        db = AsyncMock()
        return db

    @pytest.fixture
    def mock_redis(self) -> AsyncMock:
        """Create mock Redis client."""
        redis = AsyncMock()
        redis.delete = AsyncMock()
        redis.scan = AsyncMock(return_value=(0, []))
        return redis

    @pytest.mark.asyncio
    async def test_add_permission_invalidates_cache(self, mock_db: AsyncMock, mock_redis: AsyncMock) -> None:
        """Test that adding permission invalidates role cache."""
        from src.app.modules.rbac.models import Permission, Role
        from src.app.modules.rbac.service import RoleService

        # Mock role (not system role)
        mock_role = MagicMock(spec=Role)
        mock_role.id = 5
        mock_role.tenant_id = 1
        mock_role.is_system = False

        # Mock permission
        mock_permission = MagicMock(spec=Permission)
        mock_permission.id = 10

        # Setup execute calls
        mock_result_role = MagicMock()
        mock_result_role.scalar_one_or_none.return_value = mock_role

        mock_result_perm = MagicMock()
        mock_result_perm.scalar_one_or_none.return_value = mock_permission

        mock_result_exists = MagicMock()
        mock_result_exists.scalar_one_or_none.return_value = None  # Not yet assigned

        mock_db.execute.side_effect = [mock_result_role, mock_result_perm, mock_result_exists]

        with patch("src.app.modules.rbac.service.settings") as mock_settings:
            mock_settings.RBAC_USE_REDIS_CACHE = True

            service = RoleService(db=mock_db, tenant_id=1, redis_client=mock_redis)

            await service.add_permission_to_role(role_id=5, permission_slug="write:users")

            # Cache should be invalidated
            mock_redis.delete.assert_called_with("rbac:perms:1:5")

    @pytest.mark.asyncio
    async def test_remove_permission_invalidates_cache(self, mock_db: AsyncMock, mock_redis: AsyncMock) -> None:
        """Test that removing permission invalidates role cache."""
        from src.app.modules.rbac.models import Permission, Role, RolePermission
        from src.app.modules.rbac.service import RoleService

        # Mock role (not system role)
        mock_role = MagicMock(spec=Role)
        mock_role.id = 5
        mock_role.tenant_id = 1
        mock_role.is_system = False

        # Mock permission
        mock_permission = MagicMock(spec=Permission)
        mock_permission.id = 10

        # Mock role_permission (exists)
        mock_role_perm = MagicMock(spec=RolePermission)

        # Setup execute calls
        mock_result_role = MagicMock()
        mock_result_role.scalar_one_or_none.return_value = mock_role

        mock_result_perm = MagicMock()
        mock_result_perm.scalar_one_or_none.return_value = mock_permission

        mock_result_role_perm = MagicMock()
        mock_result_role_perm.scalar_one_or_none.return_value = mock_role_perm

        mock_db.execute.side_effect = [mock_result_role, mock_result_perm, mock_result_role_perm]

        with patch("src.app.modules.rbac.service.settings") as mock_settings:
            mock_settings.RBAC_USE_REDIS_CACHE = True

            service = RoleService(db=mock_db, tenant_id=1, redis_client=mock_redis)

            await service.remove_permission_from_role(role_id=5, permission_slug="write:users")

            # Cache should be invalidated
            mock_redis.delete.assert_called_with("rbac:perms:1:5")

    @pytest.mark.asyncio
    async def test_invalidate_all_uses_scan(self, mock_db: AsyncMock, mock_redis: AsyncMock) -> None:
        """Test that invalidate_all uses SCAN (non-blocking)."""
        from src.app.modules.rbac.service import RoleService

        # SCAN returns some keys
        mock_redis.scan.side_effect = [
            (100, ["rbac:perms:1:1", "rbac:perms:1:2"]),
            (0, ["rbac:perms:1:3"]),  # cursor=0 means done
        ]

        service = RoleService(db=mock_db, tenant_id=1, redis_client=mock_redis)

        await service._invalidate_all_role_caches()

        # Should use SCAN, not KEYS
        assert mock_redis.scan.call_count == 2

        # Should delete found keys
        assert mock_redis.delete.call_count == 2
