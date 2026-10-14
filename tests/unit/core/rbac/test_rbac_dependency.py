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

from src.app.core.rbac.dependencies import (
    PERMISSIONS_CACHE_TTL,
    _get_bitmask_cache_key,
    get_user_permissions,
    has_permission_cached,
    invalidate_user_permissions_cache,
    requires_permission,
)
from src.app.core.rbac.permissions import Permission
from src.app.shared.exceptions import ForbiddenError


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
        from tests.helpers.auth import build_test_auth_context

        checker = requires_permission(Permission.ROLES_MANAGE)
        ctx = build_test_auth_context(
            {"id": 1, "is_superuser": True, "tenant_id": 1},
            {"id": 1, "slug": "test-tenant"},
        )

        with patch("src.app.core.rbac.dependencies.get_cached_permissions_bitmask") as mock_perms:
            mock_db = AsyncMock()

            # Should not raise and should not check permissions
            await checker(request=None, db=mock_db, auth_context=ctx)
            mock_perms.assert_not_called()

    @pytest.mark.asyncio
    async def test_allows_user_with_permission(self):
        """User with required permission should pass."""
        from tests.helpers.auth import build_test_auth_context

        checker = requires_permission(Permission.ROLES_READ)
        tenant_id = uuid.uuid4()
        ctx = build_test_auth_context(
            {"id": 1, "is_superuser": False, "tenant_id": tenant_id},
            {"id": tenant_id, "slug": "test-tenant"},
        )

        with patch(
            "src.app.core.rbac.dependencies.has_permission_cached",
            return_value=True,
        ):
            mock_db = AsyncMock()

            # Should not raise
            await checker(request=None, db=mock_db, auth_context=ctx)

    @pytest.mark.asyncio
    async def test_denies_user_without_permission(self):
        """User without required permission should get 403."""
        from tests.helpers.auth import build_test_auth_context

        checker = requires_permission(Permission.ROLES_MANAGE)
        tenant_id = uuid.uuid4()
        ctx = build_test_auth_context(
            {"id": 1, "is_superuser": False, "tenant_id": tenant_id},
            {"id": tenant_id, "slug": "test-tenant"},
        )

        with patch(
            "src.app.core.rbac.dependencies.has_permission_cached",
            return_value=False,
        ):
            mock_db = AsyncMock()
            with pytest.raises(ForbiddenError) as exc_info:
                await checker(request=None, db=mock_db, auth_context=ctx)

            assert "roles:manage" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_accepts_string_permission(self):
        """Should accept string permission as well as enum."""
        from tests.helpers.auth import build_test_auth_context

        checker = requires_permission("roles:read")  # Use valid permission slug
        tenant_id = uuid.uuid4()
        ctx = build_test_auth_context(
            {"id": 1, "is_superuser": False, "tenant_id": tenant_id},
            {"id": tenant_id, "slug": "test-tenant"},
        )

        with patch(
            "src.app.core.rbac.dependencies.has_permission_cached",
            return_value=True,
        ):
            mock_db = AsyncMock()

            # Should not raise
            await checker(request=None, db=mock_db, auth_context=ctx)

    @pytest.mark.asyncio
    async def test_a_prefetched_mask_that_denies_is_confirmed_against_the_db(self):
        """A zero prefetched mask is a cache MISS, not a denial.

        The composite auth prefetch (core/security/auth_prefetch.py) reads
        perms:bitmask:{tenant}:{user}:{version} and yields 0 on a cache miss
        rather than consulting the database, so "mask is zero" is
        indistinguishable from "this user has no permissions" while meaning the
        opposite -- "nobody has computed this mask yet". Before the fix, every
        RBAC-gated endpoint failed closed on a cold key for every user in every
        environment while the DB-backed call returned True for the same user
        (bead FastAPI_saas-nymp). This test exists to keep that closed.
        """
        import types

        from tests.helpers.auth import build_test_auth_context

        checker = requires_permission("users:read")
        tenant_id = uuid.uuid4()
        ctx = build_test_auth_context(
            {"id": 7, "is_superuser": False, "tenant_id": tenant_id},
            {"id": tenant_id, "slug": "test-tenant"},
        )
        # The prefetch hands the dependency a mask it computed for this user
        # and tenant -- which happens to be zero because nothing has been
        # cached yet.
        request = types.SimpleNamespace(
            state=types.SimpleNamespace(cached_permissions_bitmask=(7, str(tenant_id), 0)),
        )

        with patch(
            "src.app.core.rbac.dependencies.has_permission_cached",
            return_value=True,
        ) as mock_db_check:
            # Must NOT raise: the mask denies, the DB grants.
            await checker(request=request, db=AsyncMock(), auth_context=ctx)
            mock_db_check.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_prefetched_mask_that_grants_skips_the_db(self):
        """The allow fast path is unchanged -- no DB lookup when the mask grants."""
        import types

        from tests.helpers.auth import build_test_auth_context

        checker = requires_permission("users:read")
        tenant_id = uuid.uuid4()
        ctx = build_test_auth_context(
            {"id": 7, "is_superuser": False, "tenant_id": tenant_id},
            {"id": tenant_id, "slug": "test-tenant"},
        )
        request = types.SimpleNamespace(
            state=types.SimpleNamespace(
                cached_permissions_bitmask=(7, str(tenant_id), 67108863),
            ),
        )

        with patch(
            "src.app.core.rbac.dependencies.has_permission_cached",
            return_value=True,
        ) as mock_db_check:
            await checker(request=request, db=AsyncMock(), auth_context=ctx)
            mock_db_check.assert_not_awaited()


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
