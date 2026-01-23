"""
Unit tests for RBAC permission dependency.

Tests cover:
- Superuser bypass
- Permission checking with cache hit/miss
- Permission denied scenarios
- Cache invalidation
- Redis Sets operations (smembers, sadd, pipeline)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.app.core.rbac.dependencies import (
    get_cached_permissions,
    get_user_permissions,
    has_permission_cached,
    invalidate_user_permissions_cache,
    requires_permission,
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
    """Tests for cached permission fetching using Redis Sets."""

    @pytest.mark.asyncio
    async def test_returns_cached_permissions_on_hit(self):
        """Should return cached permissions from Redis Set without hitting DB."""
        mock_db = AsyncMock()
        cached_perms = {b"users:read", b"roles:read"}  # Redis returns bytes
        mock_redis = AsyncMock()
        mock_redis.exists.return_value = True
        mock_redis.smembers.return_value = cached_perms

        # Patch at the source module where client is defined
        with patch("src.app.core.utils.cache.client", mock_redis):
            permissions = await get_cached_permissions(user_id=1, tenant_id=1, db=mock_db)

            assert permissions == {"users:read", "roles:read"}
            mock_db.execute.assert_not_called()  # DB should not be queried
            mock_redis.smembers.assert_called_once()

    @pytest.mark.asyncio
    async def test_queries_db_and_caches_on_miss(self):
        """Should query DB and cache result using Redis Set on cache miss."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = ["users:read", "roles:read"]
        mock_db.execute.return_value = mock_result

        # Redis client with async methods except pipeline() which is sync
        mock_redis = MagicMock()
        mock_redis.exists = AsyncMock(return_value=False)  # Cache miss
        # Pipeline returns a sync object with async execute
        mock_pipeline = MagicMock()
        mock_pipeline.execute = AsyncMock()
        mock_redis.pipeline.return_value = mock_pipeline

        with patch("src.app.core.utils.cache.client", mock_redis):
            permissions = await get_cached_permissions(user_id=1, tenant_id=1, db=mock_db)

            assert permissions == {"users:read", "roles:read"}
            mock_db.execute.assert_called_once()
            # Verify pipeline operations for atomic set caching
            mock_redis.pipeline.assert_called_once()
            mock_pipeline.delete.assert_called_once()
            mock_pipeline.sadd.assert_called_once()
            mock_pipeline.expire.assert_called_once()
            mock_pipeline.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_string_permissions_from_redis(self):
        """Should handle both bytes and string returns from Redis."""
        mock_db = AsyncMock()
        # Some Redis clients return strings instead of bytes
        cached_perms = {"users:read", "roles:read"}
        mock_redis = AsyncMock()
        mock_redis.exists.return_value = True
        mock_redis.smembers.return_value = cached_perms

        with patch("src.app.core.utils.cache.client", mock_redis):
            permissions = await get_cached_permissions(user_id=1, tenant_id=1, db=mock_db)

            assert permissions == {"users:read", "roles:read"}

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

    @pytest.mark.asyncio
    async def test_handles_cache_read_error_gracefully(self):
        """Should fall back to DB on cache read error."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = ["users:read"]
        mock_db.execute.return_value = mock_result

        mock_redis = AsyncMock()
        mock_redis.exists.side_effect = Exception("Redis connection error")
        mock_pipeline = AsyncMock()
        mock_redis.pipeline.return_value = mock_pipeline

        with patch("src.app.core.utils.cache.client", mock_redis):
            permissions = await get_cached_permissions(user_id=1, tenant_id=1, db=mock_db)

            assert permissions == {"users:read"}
            mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_cache_write_error_gracefully(self):
        """Should return permissions even if cache write fails."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = ["users:read"]
        mock_db.execute.return_value = mock_result

        mock_redis = MagicMock()
        mock_redis.exists = AsyncMock(return_value=False)
        mock_pipeline = MagicMock()
        mock_pipeline.execute = AsyncMock(side_effect=Exception("Redis write error"))
        mock_redis.pipeline.return_value = mock_pipeline

        with patch("src.app.core.utils.cache.client", mock_redis):
            permissions = await get_cached_permissions(user_id=1, tenant_id=1, db=mock_db)

            # Should still return permissions despite cache error
            assert permissions == {"users:read"}

    @pytest.mark.asyncio
    async def test_does_not_cache_empty_permissions(self):
        """Should not cache when user has no permissions."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        mock_redis = AsyncMock()
        mock_redis.exists.return_value = False

        with patch("src.app.core.utils.cache.client", mock_redis):
            permissions = await get_cached_permissions(user_id=1, tenant_id=1, db=mock_db)

            assert permissions == set()
            mock_redis.pipeline.assert_not_called()  # No caching for empty set


class TestInvalidateUserPermissionsCache:
    """Tests for cache invalidation."""

    @pytest.mark.asyncio
    async def test_deletes_cache_key(self):
        """Should delete both set and bitmask cache keys via pipeline."""
        mock_redis = MagicMock()
        mock_pipeline = MagicMock()
        mock_pipeline.execute = AsyncMock()
        mock_redis.pipeline.return_value = mock_pipeline

        with patch("src.app.core.utils.cache.client", mock_redis):
            await invalidate_user_permissions_cache(user_id=1, tenant_id=1)

            # Should use pipeline to delete both cache types atomically
            mock_redis.pipeline.assert_called_once()
            assert mock_pipeline.delete.call_count == 2
            mock_pipeline.execute.assert_called_once()

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

        checker = requires_permission(Permission.ROLES_READ)

        with (
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                return_value={"id": 1, "is_superuser": False},
            ),
            patch("src.app.core.tenancy.context.get_tenant_context") as mock_tenant,
            patch(
                "src.app.core.rbac.dependencies.has_permission_cached",
                return_value=True,
            ),
        ):
            mock_tenant.return_value = MagicMock(id=1)
            mock_db = AsyncMock()

            # Should not raise
            await checker(token="fake_token", db=mock_db)

    @pytest.mark.asyncio
    async def test_denies_user_without_permission(self):
        """User without required permission should get 403."""

        checker = requires_permission(Permission.ROLES_MANAGE)

        with (
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                return_value={"id": 1, "is_superuser": False},
            ),
            patch("src.app.core.tenancy.context.get_tenant_context") as mock_tenant,
            patch(
                "src.app.core.rbac.dependencies.has_permission_cached",
                return_value=False,
            ),
        ):
            mock_tenant.return_value = MagicMock(id=1)
            mock_db = AsyncMock()

            with pytest.raises(HTTPException) as exc_info:
                await checker(token="fake_token", db=mock_db)

            assert exc_info.value.status_code == 403
            assert "roles:manage" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_accepts_string_permission(self):
        """Should accept string permission as well as enum."""

        checker = requires_permission("roles:read")  # Use valid permission slug

        with (
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                return_value={"id": 1, "is_superuser": False},
            ),
            patch("src.app.core.tenancy.context.get_tenant_context") as mock_tenant,
            patch(
                "src.app.core.rbac.dependencies.has_permission_cached",
                return_value=True,
            ),
        ):
            mock_tenant.return_value = MagicMock(id=1)
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
