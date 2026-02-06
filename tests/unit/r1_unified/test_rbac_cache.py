"""Unit tests for P2-3: RBAC Redis Cache Optimization."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.rbac.models import Permission
from src.app.modules.rbac.service import RoleService


@pytest.fixture(autouse=True)
def clear_l1_cache():
    """Clear L1 cache before each test."""
    from src.app.modules.rbac.service import _L1_RBAC_CACHE

    _L1_RBAC_CACHE.clear()
    yield
    _L1_RBAC_CACHE.clear()


@pytest.mark.asyncio
async def test_cache_hit_avoids_database_query(mock_redis_client, mock_db_session, mock_settings):
    """Test that cache hit returns permissions without database query."""
    service = RoleService(db=mock_db_session, tenant_id=1, redis_client=mock_redis_client)

    # Mock cached permissions
    cached_data = [
        {
            "id": 1,
            "slug": "users.read",
            "description": "Read users",
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
        },
        {
            "id": 2,
            "slug": "users.write",
            "description": "Write users",
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
        },
    ]
    mock_redis_client.get = AsyncMock(
        return_value='[{"id":1,"slug":"users.read","description":"Read users","created_at":"2024-01-01T00:00:00+00:00","updated_at":"2024-01-01T00:00:00+00:00"},{"id":2,"slug":"users.write","description":"Write users","created_at":"2024-01-01T00:00:00+00:00","updated_at":"2024-01-01T00:00:00+00:00"}]'
    )

    # Mock database for fallback (in case cache deserialization fails)
    mock_role = MagicMock()
    mock_role.id = 1
    mock_role.tenant_id = 1

    mock_perm1 = MagicMock(spec=Permission)
    mock_perm1.id = 1
    mock_perm1.slug = "users.read"
    mock_perm1.description = "Read users"
    mock_perm1.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    mock_perm1.updated_at = datetime(2024, 1, 1, tzinfo=UTC)

    mock_perm2 = MagicMock(spec=Permission)
    mock_perm2.id = 2
    mock_perm2.slug = "users.write"
    mock_perm2.description = "Write users"
    mock_perm2.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    mock_perm2.updated_at = datetime(2024, 1, 1, tzinfo=UTC)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_role
    mock_result.scalars.return_value.all.return_value = [mock_perm1, mock_perm2]
    mock_db_session.execute = AsyncMock(return_value=mock_result)

    with patch("src.app.modules.rbac.service.settings", mock_settings):
        # Patch Permission class to allow instantiation with id parameter
        def mock_permission_init(**kwargs):
            perm = MagicMock(spec=Permission)
            for key, value in kwargs.items():
                if key in ["created_at", "updated_at"] and isinstance(value, str):
                    value = datetime.fromisoformat(value)
                setattr(perm, key, value)
            return perm

        with patch("src.app.modules.rbac.service.Permission", side_effect=mock_permission_init):
            with patch("src.app.modules.rbac.service.record_rbac_cache_hit") as mock_hit:
                permissions = await service.get_role_permissions(role_id=1)

    # Should return permissions from cache
    assert len(permissions) == 2
    assert permissions[0].slug == "users.read"
    assert permissions[1].slug == "users.write"

    # Cache hit should be recorded
    mock_hit.assert_called_once()

    # Database should NOT be queried
    mock_db_session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_cache_miss_queries_database(mock_redis_client, mock_db_session, mock_settings):
    """Test that cache miss queries database and caches result."""
    service = RoleService(db=mock_db_session, tenant_id=1, redis_client=mock_redis_client)

    # Mock cache miss
    mock_redis_client.get = AsyncMock(return_value=None)
    mock_redis_client.setex = AsyncMock()

    # Mock database query for role verification
    mock_role = MagicMock()
    mock_role.id = 1
    mock_role.tenant_id = 1

    # Mock database query for permissions
    mock_perm1 = MagicMock(spec=Permission)
    mock_perm1.id = 1
    mock_perm1.slug = "users.read"
    mock_perm1.description = "Read users"
    mock_perm1.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    mock_perm1.updated_at = datetime(2024, 1, 1, tzinfo=UTC)

    mock_perm2 = MagicMock(spec=Permission)
    mock_perm2.id = 2
    mock_perm2.slug = "users.write"
    mock_perm2.description = "Write users"
    mock_perm2.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    mock_perm2.updated_at = datetime(2024, 1, 1, tzinfo=UTC)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_role
    mock_result.scalars.return_value.all.return_value = [mock_perm1, mock_perm2]
    mock_db_session.execute = AsyncMock(return_value=mock_result)

    with patch("src.app.modules.rbac.service.settings", mock_settings):
        with patch("src.app.modules.rbac.service.record_rbac_cache_miss") as mock_miss:
            permissions = await service.get_role_permissions(role_id=1)

    # Should return permissions from database
    assert len(permissions) == 2
    assert permissions[0].slug == "users.read"
    assert permissions[1].slug == "users.write"

    # Cache miss should be recorded
    mock_miss.assert_called_once()

    # Database should be queried
    assert mock_db_session.execute.call_count == 2  # Role verification + permissions query

    # Result should be cached
    mock_redis_client.setex.assert_called_once()


@pytest.mark.asyncio
async def test_cache_key_format(mock_redis_client, mock_db_session, mock_settings):
    """Test that cache keys use correct format: rbac:perms:tenant_id:role_id."""
    service = RoleService(db=mock_db_session, tenant_id=123, redis_client=mock_redis_client)

    # Mock cache miss
    mock_redis_client.get = AsyncMock(return_value=None)
    mock_redis_client.setex = AsyncMock()

    # Mock database
    mock_role = MagicMock()
    mock_role.id = 456
    mock_role.tenant_id = 123

    mock_perm = MagicMock(spec=Permission)
    mock_perm.id = 1
    mock_perm.slug = "test.perm"
    mock_perm.description = "Test"
    mock_perm.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    mock_perm.updated_at = datetime(2024, 1, 1, tzinfo=UTC)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_role
    mock_result.scalars.return_value.all.return_value = [mock_perm]
    mock_db_session.execute = AsyncMock(return_value=mock_result)

    with patch("src.app.modules.rbac.service.settings", mock_settings):
        await service.get_role_permissions(role_id=456)

    # Verify cache key format
    mock_redis_client.get.assert_called_once_with("rbac:perms:123:456")
    mock_redis_client.setex.assert_called_once()
    call_args = mock_redis_client.setex.call_args[0]
    assert call_args[0] == "rbac:perms:123:456"


@pytest.mark.asyncio
async def test_cache_ttl_configured(mock_redis_client, mock_db_session, mock_settings):
    """Test that cache TTL is configurable via settings."""
    service = RoleService(db=mock_db_session, tenant_id=1, redis_client=mock_redis_client)

    # Mock cache miss
    mock_redis_client.get = AsyncMock(return_value=None)
    mock_redis_client.setex = AsyncMock()

    # Mock database
    mock_role = MagicMock()
    mock_role.id = 1
    mock_role.tenant_id = 1

    mock_perm = MagicMock(spec=Permission)
    mock_perm.id = 1
    mock_perm.slug = "test.perm"
    mock_perm.description = "Test"
    mock_perm.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    mock_perm.updated_at = datetime(2024, 1, 1, tzinfo=UTC)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_role
    mock_result.scalars.return_value.all.return_value = [mock_perm]
    mock_db_session.execute = AsyncMock(return_value=mock_result)

    # Set custom TTL
    mock_settings.RBAC_CACHE_TTL_SECONDS = 7200

    with patch("src.app.modules.rbac.service.settings", mock_settings):
        await service.get_role_permissions(role_id=1)

    # Verify TTL is used
    mock_redis_client.setex.assert_called_once()
    call_args = mock_redis_client.setex.call_args[0]
    assert call_args[1] == 7200  # TTL in seconds


@pytest.mark.asyncio
async def test_feature_flag_disables_cache(mock_redis_client, mock_db_session, mock_settings):
    """Test that feature flag controls cache usage."""
    service = RoleService(db=mock_db_session, tenant_id=1, redis_client=mock_redis_client)

    # Disable cache
    mock_settings.RBAC_USE_REDIS_CACHE = False

    # Mock database
    mock_role = MagicMock()
    mock_role.id = 1
    mock_role.tenant_id = 1

    mock_perm = MagicMock(spec=Permission)
    mock_perm.id = 1
    mock_perm.slug = "test.perm"
    mock_perm.description = "Test"
    mock_perm.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    mock_perm.updated_at = datetime(2024, 1, 1, tzinfo=UTC)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_role
    mock_result.scalars.return_value.all.return_value = [mock_perm]
    mock_db_session.execute = AsyncMock(return_value=mock_result)

    with patch("src.app.modules.rbac.service.settings", mock_settings):
        with patch("src.app.modules.rbac.service.record_rbac_cache_miss") as mock_miss:
            permissions = await service.get_role_permissions(role_id=1)

    # Should query database
    assert len(permissions) == 1

    # Cache miss should be recorded
    mock_miss.assert_called_once()

    # Redis should NOT be accessed
    mock_redis_client.get.assert_not_called()
    mock_redis_client.setex.assert_not_called()


@pytest.mark.asyncio
async def test_cache_read_error_fallback(mock_redis_client, mock_db_session, mock_settings):
    """Test that cache read errors fall back to database."""
    service = RoleService(db=mock_db_session, tenant_id=1, redis_client=mock_redis_client)

    # Mock cache read error
    mock_redis_client.get = AsyncMock(side_effect=Exception("Redis connection error"))

    # Mock database
    mock_role = MagicMock()
    mock_role.id = 1
    mock_role.tenant_id = 1

    mock_perm = MagicMock(spec=Permission)
    mock_perm.id = 1
    mock_perm.slug = "test.perm"
    mock_perm.description = "Test"
    mock_perm.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    mock_perm.updated_at = datetime(2024, 1, 1, tzinfo=UTC)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_role
    mock_result.scalars.return_value.all.return_value = [mock_perm]
    mock_db_session.execute = AsyncMock(return_value=mock_result)

    with patch("src.app.modules.rbac.service.settings", mock_settings):
        permissions = await service.get_role_permissions(role_id=1)

    # Should fall back to database
    assert len(permissions) == 1
    assert permissions[0].slug == "test.perm"


@pytest.mark.asyncio
async def test_cache_write_error_continues(mock_redis_client, mock_db_session, mock_settings):
    """Test that cache write errors don't break the operation."""
    service = RoleService(db=mock_db_session, tenant_id=1, redis_client=mock_redis_client)

    # Mock cache miss
    mock_redis_client.get = AsyncMock(return_value=None)
    # Mock cache write error
    mock_redis_client.setex = AsyncMock(side_effect=Exception("Redis write error"))

    # Mock database
    mock_role = MagicMock()
    mock_role.id = 1
    mock_role.tenant_id = 1

    mock_perm = MagicMock(spec=Permission)
    mock_perm.id = 1
    mock_perm.slug = "test.perm"
    mock_perm.description = "Test"
    mock_perm.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    mock_perm.updated_at = datetime(2024, 1, 1, tzinfo=UTC)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_role
    mock_result.scalars.return_value.all.return_value = [mock_perm]
    mock_db_session.execute = AsyncMock(return_value=mock_result)

    with patch("src.app.modules.rbac.service.settings", mock_settings):
        permissions = await service.get_role_permissions(role_id=1)

    # Should return permissions despite cache write error
    assert len(permissions) == 1
    assert permissions[0].slug == "test.perm"


@pytest.mark.asyncio
async def test_cache_invalidation_on_permission_add(mock_redis_client, mock_db_session, mock_settings):
    """Test that cache is invalidated when permissions are added to role."""
    service = RoleService(db=mock_db_session, tenant_id=1, redis_client=mock_redis_client)

    mock_redis_client.delete = AsyncMock()

    # Mock database for role and permission
    mock_role = MagicMock()
    mock_role.id = 1
    mock_role.tenant_id = 1
    mock_role.is_system = False

    mock_permission = MagicMock()
    mock_permission.id = 1
    mock_permission.slug = "test.perm"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.side_effect = [
        mock_role,
        mock_permission,
        None,
    ]  # Role, permission, no existing assignment
    mock_db_session.execute = AsyncMock(return_value=mock_result)
    mock_db_session.add = MagicMock()
    mock_db_session.commit = AsyncMock()

    with patch("src.app.modules.rbac.service.settings", mock_settings):
        await service.add_permission_to_role(role_id=1, permission_slug="test.perm")

    # Cache should be invalidated
    mock_redis_client.delete.assert_called_once_with("rbac:perms:1:1")


@pytest.mark.asyncio
async def test_cache_invalidation_on_permission_remove(mock_redis_client, mock_db_session, mock_settings):
    """Test that cache is invalidated when permissions are removed from role."""
    service = RoleService(db=mock_db_session, tenant_id=1, redis_client=mock_redis_client)

    mock_redis_client.delete = AsyncMock()

    # Mock database
    mock_role = MagicMock()
    mock_role.id = 1
    mock_role.tenant_id = 1
    mock_role.is_system = False

    mock_permission = MagicMock()
    mock_permission.id = 1
    mock_permission.slug = "test.perm"

    mock_role_permission = MagicMock()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.side_effect = [mock_role, mock_permission, mock_role_permission]
    mock_db_session.execute = AsyncMock(return_value=mock_result)
    mock_db_session.delete = AsyncMock()
    mock_db_session.commit = AsyncMock()

    with patch("src.app.modules.rbac.service.settings", mock_settings):
        await service.remove_permission_from_role(role_id=1, permission_slug="test.perm")

    # Cache should be invalidated
    mock_redis_client.delete.assert_called_once_with("rbac:perms:1:1")


@pytest.mark.asyncio
async def test_tenant_isolation(mock_redis_client, mock_db_session, mock_settings):
    """Test that cache keys are tenant-isolated."""
    service1 = RoleService(db=mock_db_session, tenant_id=1, redis_client=mock_redis_client)
    service2 = RoleService(db=mock_db_session, tenant_id=2, redis_client=mock_redis_client)

    # Mock cache miss for both
    mock_redis_client.get = AsyncMock(return_value=None)
    mock_redis_client.setex = AsyncMock()

    # Mock database
    mock_role = MagicMock()
    mock_role.id = 1
    mock_role.tenant_id = 1

    mock_perm = MagicMock(spec=Permission)
    mock_perm.id = 1
    mock_perm.slug = "test.perm"
    mock_perm.description = "Test"
    mock_perm.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    mock_perm.updated_at = datetime(2024, 1, 1, tzinfo=UTC)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_role
    mock_result.scalars.return_value.all.return_value = [mock_perm]
    mock_db_session.execute = AsyncMock(return_value=mock_result)

    with patch("src.app.modules.rbac.service.settings", mock_settings):
        await service1.get_role_permissions(role_id=1)
        await service2.get_role_permissions(role_id=1)

    # Verify different cache keys for different tenants
    get_calls = mock_redis_client.get.call_args_list
    assert get_calls[0][0][0] == "rbac:perms:1:1"
    assert get_calls[1][0][0] == "rbac:perms:2:1"


@pytest.mark.asyncio
async def test_empty_permissions_not_cached(mock_redis_client, mock_db_session, mock_settings):
    """Test that empty permission lists are not cached."""
    service = RoleService(db=mock_db_session, tenant_id=1, redis_client=mock_redis_client)

    # Mock cache miss
    mock_redis_client.get = AsyncMock(return_value=None)
    mock_redis_client.setex = AsyncMock()

    # Mock database with empty permissions
    mock_role = MagicMock()
    mock_role.id = 1
    mock_role.tenant_id = 1

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_role
    mock_result.scalars.return_value.all.return_value = []  # Empty permissions
    mock_db_session.execute = AsyncMock(return_value=mock_result)

    with patch("src.app.modules.rbac.service.settings", mock_settings):
        permissions = await service.get_role_permissions(role_id=1)

    # Should return empty list
    assert len(permissions) == 0

    # Empty result should NOT be cached
    mock_redis_client.setex.assert_not_called()
