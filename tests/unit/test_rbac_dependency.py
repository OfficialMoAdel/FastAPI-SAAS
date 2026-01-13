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
    get_user_permissions_cache_key,
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


class TestHasPermissionCached:
    """Tests for O(1) SISMEMBER permission check."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = ["users:read", "users:write"]
        db.execute.return_value = mock_result
        return db

    @pytest.mark.asyncio
    async def test_uses_sismember_for_cached_permissions(self, mock_db):
        """Should use O(1) SISMEMBER when cache exists."""
        mock_redis = MagicMock()
        mock_redis.exists = AsyncMock(return_value=True)
        mock_redis.sismember = AsyncMock(return_value=1)  # 1 = member exists

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await has_permission_cached(
                user_id=1,
                tenant_id=1,
                permission_slug="users:read",
                db=mock_db,
            )

            assert result is True
            mock_redis.sismember.assert_called_once()
            # Should NOT call smembers or database
            mock_db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_false_for_missing_permission(self, mock_db):
        """Should return False when permission not in set."""
        mock_redis = MagicMock()
        mock_redis.exists = AsyncMock(return_value=True)
        mock_redis.sismember = AsyncMock(return_value=0)  # 0 = not a member

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await has_permission_cached(
                user_id=1,
                tenant_id=1,
                permission_slug="admin:delete",
                db=mock_db,
            )

            assert result is False
            mock_redis.sismember.assert_called_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_db_when_cache_empty(self, mock_db):
        """Should query DB and check when cache doesn't exist."""
        mock_redis = MagicMock()
        mock_redis.exists = AsyncMock(return_value=False)  # Cache miss
        mock_pipeline = MagicMock()
        mock_pipeline.execute = AsyncMock()
        mock_redis.pipeline.return_value = mock_pipeline

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await has_permission_cached(
                user_id=1,
                tenant_id=1,
                permission_slug="users:read",
                db=mock_db,
            )

            assert result is True  # users:read is in mock DB permissions
            mock_db.execute.assert_called_once()
            # Should NOT use sismember when cache doesn't exist
            mock_redis.sismember.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_on_redis_error(self, mock_db):
        """Should fall back to DB query on Redis error."""
        mock_redis = MagicMock()
        mock_redis.exists = AsyncMock(side_effect=Exception("Redis connection lost"))
        mock_pipeline = MagicMock()
        mock_pipeline.execute = AsyncMock()
        mock_redis.pipeline.return_value = mock_pipeline

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
                permission_slug="users:write",
                db=mock_db,
            )

            assert result is True
            mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_false_for_nonexistent_permission(self, mock_db):
        """Should return False when checking non-existent permission."""
        mock_redis = MagicMock()
        mock_redis.exists = AsyncMock(return_value=False)

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await has_permission_cached(
                user_id=1,
                tenant_id=1,
                permission_slug="nonexistent:permission",
                db=mock_db,
            )

            assert result is False
