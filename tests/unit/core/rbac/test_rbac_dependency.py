"""
Unit tests for RBAC permission dependency.

Tests cover:
- Superuser bypass
- Permission checking with cache hit/miss
- Permission denied scenarios
- Cache invalidation
- Redis Sets operations (smembers, sadd, pipeline)
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.app.core.rbac.dependencies import (
    PERMISSIONS_CACHE_TTL,
    _get_bitmask_cache_key,
    get_user_permissions,
    has_permission_cached,
    invalidate_user_permissions_cache,
    requires_permission,
)
from src.app.core.rbac.permissions import Permission


def _acm(pipe):
    """Make a pipeline mock support 'async with' returning itself."""

    async def __aenter__(*a):
        return pipe

    async def __aexit__(*a):
        return False

    pipe.__aenter__ = __aenter__
    pipe.__aexit__ = __aexit__
    return pipe


class TestGetUserPermissionsCacheKey:
    """Tests for cache key generation."""

    def test_cache_key_format(self):
        """Cache key should follow expected format."""
        from src.app.core.rbac.bitmask import PermissionBitmask
        version = PermissionBitmask.get_permission_version_hash()
        key = _get_bitmask_cache_key(user_id=123, tenant_id=456)
        assert key == f"perms:bitmask:456:123:{version}"

    def test_cache_key_different_users(self):
        """Different users should have different cache keys."""
        key1 = _get_bitmask_cache_key(user_id=1, tenant_id=100)
        key2 = _get_bitmask_cache_key(user_id=2, tenant_id=100)
        assert key1 != key2

    def test_cache_key_different_tenants(self):
        """Same user in different tenants should have different cache keys."""
        key1 = _get_bitmask_cache_key(user_id=1, tenant_id=100)
        key2 = _get_bitmask_cache_key(user_id=1, tenant_id=200)
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





class TestInvalidateUserPermissionsCache:
    """Tests for cache invalidation."""

    @pytest.mark.asyncio
    async def test_deletes_cache_key(self):
        """Should delete bitmask cache key directly."""
        mock_redis = AsyncMock()

        with patch("src.app.core.utils.cache.client", mock_redis):
            await invalidate_user_permissions_cache(user_id=1, tenant_id=1)

            mock_redis.delete.assert_called_once()

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

        checker = requires_permission(Permission.ROLES_MANAGE)

        # Mock the internal dependencies
        class DummyTenant:
            id = 1

        with (
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                return_value={"id": 1, "is_superuser": True},
            ),
            patch("src.app.core.tenancy.context.get_current_tenant", return_value=DummyTenant()),
            patch("src.app.core.rbac.dependencies.get_cached_permissions_bitmask") as mock_perms,
        ):
            mock_db = AsyncMock()

            # Should not raise and should not check permissions
            await checker(token="fake_token", db=mock_db)
            mock_perms.assert_not_called()

    @pytest.mark.asyncio
    async def test_allows_user_with_permission(self):
        """User with required permission should pass."""

        checker = requires_permission(Permission.ROLES_READ)

        class DummyTenant:
            id = uuid.uuid4()

        with (
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                return_value={"id": 1, "is_superuser": False},
            ),
            patch("src.app.core.tenancy.context.get_current_tenant") as mock_tenant,
            patch(
                "src.app.core.rbac.dependencies.has_permission_cached",
                return_value=True,
            ),
        ):
            mock_tenant.return_value = DummyTenant()
            mock_db = AsyncMock()

            # Should not raise
            await checker(token="fake_token", db=mock_db)

    @pytest.mark.asyncio
    async def test_denies_user_without_permission(self):
        """User without required permission should get 403."""

        checker = requires_permission(Permission.ROLES_MANAGE)

        class DummyTenant:
            id = uuid.uuid4()

        with (
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                return_value={"id": 1, "is_superuser": False},
            ),
            patch("src.app.core.tenancy.context.get_current_tenant") as mock_tenant,
            patch(
                "src.app.core.rbac.dependencies.has_permission_cached",
                return_value=False,
            ),
        ):
            mock_tenant.return_value = DummyTenant()
            mock_db = AsyncMock()

            with pytest.raises(HTTPException) as exc_info:
                await checker(token="fake_token", db=mock_db)

            assert exc_info.value.status_code == 403
            assert "roles:manage" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_accepts_string_permission(self):
        """Should accept string permission as well as enum."""

        checker = requires_permission("roles:read")  # Use valid permission slug

        class DummyTenant:
            id = uuid.uuid4()

        with (
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                return_value={"id": 1, "is_superuser": False},
            ),
            patch("src.app.core.tenancy.context.get_current_tenant") as mock_tenant,
            patch(
                "src.app.core.rbac.dependencies.has_permission_cached",
                return_value=True,
            ),
        ):
            mock_tenant.return_value = DummyTenant()
            mock_db = AsyncMock()

            # Should not raise
            await checker(token="fake_token", db=mock_db)


class TestCacheTTL:
    """Tests for cache TTL configuration."""

    def test_cache_ttl_is_reasonable(self):
        """Cache TTL should be a reasonable value (5-30 minutes)."""
        assert 300 <= PERMISSIONS_CACHE_TTL <= 1800  # 5-30 minutes


class TestHasPermissionCached:
    """Tests for O(1) bitmask permission check."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = ["users:read", "users:manage"]
        db.execute.return_value = mock_result
        return db

    @pytest.mark.asyncio
    async def test_uses_bitmask_for_cached_permissions(self, mock_db):
        """Should use O(1) bitmask GET when cache exists."""
        from src.app.core.rbac.bitmask import PermissionBitmask

        # Pre-compute expected bitmask for users:read + users:manage
        expected_mask = PermissionBitmask.encode({"users:read", "users:manage"})

        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=str(expected_mask).encode())

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await has_permission_cached(
                user_id=1,
                tenant_id=1,
                permission_slug="users:read",
                db=mock_db,
            )

            assert result is True
            mock_redis.get.assert_called_once()
            # Should NOT call database when cache hit
            mock_db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_false_for_missing_permission(self, mock_db):
        """Should return False when permission not in bitmask."""
        from src.app.core.rbac.bitmask import PermissionBitmask

        # Bitmask with only users:read
        mask = PermissionBitmask.encode({"users:read"})

        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=str(mask).encode())

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await has_permission_cached(
                user_id=1,
                tenant_id=1,
                permission_slug="users:manage",  # Not in bitmask
                db=mock_db,
            )

            assert result is False

    @pytest.mark.asyncio
    async def test_falls_back_to_db_when_cache_empty(self, mock_db):
        """Should query DB and encode bitmask when cache doesn't exist."""
        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=None)  # Cache miss
        mock_redis.setex = AsyncMock()

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await has_permission_cached(
                user_id=1,
                tenant_id=1,
                permission_slug="users:read",
                db=mock_db,
            )

            assert result is True  # users:read is in mock DB permissions
            mock_db.execute.assert_called_once()
            # Should cache the bitmask
            mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_falls_back_on_redis_error(self, mock_db):
        """Should fall back to DB query on Redis error."""
        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(side_effect=Exception("Redis connection lost"))
        mock_redis.setex = AsyncMock()

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await has_permission_cached(
                user_id=1,
                tenant_id=1,
                permission_slug="users:read",
                db=mock_db,
            )

            assert result is True
            mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_works_without_redis(self, mock_db):
        """Should work when Redis is unavailable."""
        with patch("src.app.core.utils.cache.client", None):
            result = await has_permission_cached(
                user_id=1,
                tenant_id=1,
                permission_slug="users:manage",  # Valid permission
                db=mock_db,
            )

            assert result is True
            mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_false_for_nonexistent_permission(self, mock_db):
        """Should return False when checking non-existent permission."""
        # Bitmask.has_permission returns False for unknown slugs (KeyError caught)
        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock()

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await has_permission_cached(
                user_id=1,
                tenant_id=1,
                permission_slug="nonexistent:permission",
                db=mock_db,
            )

            # Should return False because permission slug doesn't exist in Permission enum
            assert result is False
