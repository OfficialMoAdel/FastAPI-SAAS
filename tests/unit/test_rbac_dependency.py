"""
Unit tests for RBAC permission dependency.

Tests cover:
- Superuser bypass
- Permission checking with cache hit/miss
- Permission denied scenarios
- Cache invalidation
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.app.core.rbac.dependencies import (
    get_cached_permissions,
    get_user_permissions,
    invalidate_user_permissions_cache,
)
from src.app.core.rbac.permissions import Permission
from src.app.core.rbac.utils import PERMISSIONS_CACHE_TTL, get_user_permissions_cache_key


class TestGetUserPermissionsCacheKey:
    """Tests for cache key generation."""

    def test_cache_key_format(self):
        """Cache key should follow expected format."""
        key = get_user_permissions_cache_key(user_id=123, tenant_id=456)
        assert key == "rbac:permissions:123:456"

    def test_cache_key_different_users(self):
        """Different users should have different cache keys."""
        key1 = get_user_permissions_cache_key(user_id=1, tenant_id=100)
        key2 = get_user_permissions_cache_key(user_id=2, tenant_id=100)
        assert key1 != key2

    def test_cache_key_different_tenants(self):
        """Same user in different tenants should have different cache keys."""
        key1 = get_user_permissions_cache_key(user_id=1, tenant_id=100)
        key2 = get_user_permissions_cache_key(user_id=1, tenant_id=200)
        assert key1 != key2


class TestGetUserPermissions:
    """Tests for database permission fetching."""

    @pytest.mark.asyncio
    async def test_returns_empty_set_when_no_roles(self):
        """User with no roles should have no permissions."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        permissions = await get_user_permissions(user_id=1, tenant_id=1, db=mock_db)

        assert permissions == set()
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_permissions_from_roles(self):
        """User with roles should get their permissions."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [
            "users:read",
            "roles:read",
            "roles:manage",
        ]
        mock_db.execute.return_value = mock_result

        permissions = await get_user_permissions(user_id=1, tenant_id=1, db=mock_db)

        assert permissions == {"users:read", "roles:read", "roles:manage"}


class TestGetCachedPermissions:
    """Tests for cached permission fetching."""

    @pytest.mark.asyncio
    async def test_returns_cached_permissions_on_hit(self):
        """Should return cached permissions without hitting DB."""
        mock_db = AsyncMock()
        cached_perms = ["users:read", "roles:read"]
        mock_redis = AsyncMock()
        mock_redis.get.return_value = json.dumps(cached_perms)

        # Patch at the source module where client is defined
        with patch("src.app.core.utils.cache.client", mock_redis):
            permissions = await get_cached_permissions(user_id=1, tenant_id=1, db=mock_db)

            assert permissions == set(cached_perms)
            mock_db.execute.assert_not_called()  # DB should not be queried

    @pytest.mark.asyncio
    async def test_queries_db_and_caches_on_miss(self):
        """Should query DB and cache result on cache miss."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = ["users:read"]
        mock_db.execute.return_value = mock_result

        mock_redis = AsyncMock()
        mock_redis.get.return_value = None  # Cache miss

        with patch("src.app.core.utils.cache.client", mock_redis):
            permissions = await get_cached_permissions(user_id=1, tenant_id=1, db=mock_db)

            assert permissions == {"users:read"}
            mock_db.execute.assert_called_once()
            mock_redis.set.assert_called_once()
            mock_redis.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_works_without_redis(self):
        """Should work when Redis is unavailable."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = ["users:read"]
        mock_db.execute.return_value = mock_result

        with patch("src.app.core.utils.cache.client", None):
            permissions = await get_cached_permissions(user_id=1, tenant_id=1, db=mock_db)

            assert permissions == {"users:read"}
            mock_db.execute.assert_called_once()


class TestInvalidateUserPermissionsCache:
    """Tests for cache invalidation."""

    @pytest.mark.asyncio
    async def test_deletes_cache_key(self):
        """Should delete the correct cache key."""
        mock_redis = AsyncMock()

        with patch("src.app.core.utils.cache.client", mock_redis):
            await invalidate_user_permissions_cache(user_id=1, tenant_id=1)

            expected_key = get_user_permissions_cache_key(1, 1)
            mock_redis.delete.assert_called_once_with(expected_key)

    @pytest.mark.asyncio
    async def test_handles_missing_redis_gracefully(self):
        """Should not raise when Redis is unavailable."""
        with patch("src.app.core.utils.cache.client", None):
            # Should not raise
            await invalidate_user_permissions_cache(user_id=1, tenant_id=1)


class TestRequiresPermission:
    """Tests for the requires_permission dependency factory."""

    @pytest.mark.asyncio
    async def test_superuser_bypasses_check(self):
        """Superusers should bypass all permission checks."""
        from src.app.core.rbac.dependencies import requires_permission

        checker = requires_permission(Permission.ROLES_MANAGE)

        # Mock the internal dependencies
        with (
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                return_value={"id": 1, "is_superuser": True},
            ),
            patch("src.app.core.rbac.dependencies._get_current_tenant_id", return_value=1),
            patch("src.app.core.rbac.dependencies.get_cached_permissions") as mock_perms,
        ):
            mock_db = AsyncMock()

            # Should not raise and should not check permissions
            await checker(token="fake_token", db=mock_db)
            mock_perms.assert_not_called()

    @pytest.mark.asyncio
    async def test_allows_user_with_permission(self):
        """User with required permission should pass."""
        from src.app.core.rbac.dependencies import requires_permission

        checker = requires_permission(Permission.ROLES_READ)

        with (
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                return_value={"id": 1, "is_superuser": False},
            ),
            patch("src.app.core.rbac.dependencies._get_current_tenant_id", return_value=1),
            patch(
                "src.app.core.rbac.dependencies.get_cached_permissions",
                return_value={"roles:read", "users:read"},
            ),
        ):
            mock_db = AsyncMock()

            # Should not raise
            await checker(token="fake_token", db=mock_db)

    @pytest.mark.asyncio
    async def test_denies_user_without_permission(self):
        """User without required permission should get 403."""
        from src.app.core.rbac.dependencies import requires_permission

        checker = requires_permission(Permission.ROLES_MANAGE)

        with (
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                return_value={"id": 1, "is_superuser": False},
            ),
            patch("src.app.core.rbac.dependencies._get_current_tenant_id", return_value=1),
            patch(
                "src.app.core.rbac.dependencies.get_cached_permissions",
                return_value={"roles:read"},  # Has read but not manage
            ),
        ):
            mock_db = AsyncMock()

            with pytest.raises(HTTPException) as exc_info:
                await checker(token="fake_token", db=mock_db)

            assert exc_info.value.status_code == 403
            assert "roles:manage" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_accepts_string_permission(self):
        """Should accept string permission as well as enum."""
        from src.app.core.rbac.dependencies import requires_permission

        checker = requires_permission("custom:permission")

        with (
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                return_value={"id": 1, "is_superuser": False},
            ),
            patch("src.app.core.rbac.dependencies._get_current_tenant_id", return_value=1),
            patch(
                "src.app.core.rbac.dependencies.get_cached_permissions",
                return_value={"custom:permission"},
            ),
        ):
            mock_db = AsyncMock()

            # Should not raise
            await checker(token="fake_token", db=mock_db)


class TestCacheTTL:
    """Tests for cache TTL configuration."""

    def test_cache_ttl_is_reasonable(self):
        """Cache TTL should be a reasonable value (5-30 minutes)."""
        assert 300 <= PERMISSIONS_CACHE_TTL <= 1800  # 5-30 minutes
