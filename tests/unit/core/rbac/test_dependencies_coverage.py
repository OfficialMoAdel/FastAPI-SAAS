"""Unit tests for RBAC permission dependencies (core/rbac/dependencies.py)."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.core.rbac import dependencies as rbac_deps
from src.app.core.rbac.bitmask import PermissionBitmask
from src.app.core.rbac.permissions import Permission
from src.app.core.tenancy.context import TenantContext

TENANT_ID = uuid4()
from src.app.core.utils import cache as cache_module


class _User:
    def __init__(self, *, is_superuser=False, user_id=1, tenant_id=TENANT_ID):
        self.is_superuser = is_superuser
        self.id = user_id
        self.tenant_id = tenant_id


class _AuthContext:
    def __init__(self, user, tenant, is_dependency_override=False):
        self.user = user
        self.tenant = tenant
        self.is_dependency_override = is_dependency_override


@pytest.fixture
def tenant_ctx():
    return TenantContext(
        tenant_id=TENANT_ID,
        tenant_slug="slug",
        tenant_name="name",
        is_active=True,
    )


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    return db


def _make_request(path="/x", path_params=None):
    request = MagicMock()
    request.url.path = path
    request.path_params = path_params or {}
    request.state = MagicMock()
    request.headers = {}
    request.app = MagicMock()
    return request


async def test_superuser_bypass(tenant_ctx, mock_db):
    user = _User(is_superuser=True, tenant_id=TENANT_ID)
    ctx = _AuthContext(user, tenant_ctx)
    dep = rbac_deps.requires_permission(Permission.USERS_READ)
    await dep(request=_make_request(), token="tok", db=mock_db, auth_context=ctx)


async def test_tenant_mismatch_forbidden(tenant_ctx, mock_db):
    user = _User(is_superuser=False, tenant_id="other-tenant")
    ctx = _AuthContext(user, tenant_ctx)
    dep = rbac_deps.requires_permission(Permission.USERS_READ)
    with pytest.raises(Exception):
        await dep(request=_make_request(), token="tok", db=mock_db, auth_context=ctx)


async def test_tenant_context_required(mock_db):
    user = _User()
    ctx = _AuthContext(user, None)
    dep = rbac_deps.requires_permission(Permission.USERS_READ)
    with pytest.raises(Exception):
        await dep(request=_make_request(), token="tok", db=mock_db, auth_context=ctx)


async def test_user_id_required(tenant_ctx, mock_db):
    user = _User(user_id=None)
    ctx = _AuthContext(user, tenant_ctx)
    dep = rbac_deps.requires_permission(Permission.USERS_READ)
    with pytest.raises(Exception):
        await dep(request=_make_request(), token="tok", db=mock_db, auth_context=ctx)


async def test_permission_granted_via_cache(tenant_ctx, mock_db):
    user = _User(tenant_id=TENANT_ID)
    ctx = _AuthContext(user, tenant_ctx)
    mask = PermissionBitmask.get_bit(Permission.USERS_READ.value)
    with patch.object(rbac_deps, "get_cached_permissions_bitmask", AsyncMock(return_value=mask)):
        dep = rbac_deps.requires_permission(Permission.USERS_READ)
        await dep(request=_make_request(), token="tok", db=mock_db, auth_context=ctx)


async def test_permission_denied_403(tenant_ctx, mock_db):
    user = _User(tenant_id=TENANT_ID)
    ctx = _AuthContext(user, tenant_ctx)
    with patch.object(rbac_deps, "get_cached_permissions_bitmask", AsyncMock(return_value=0)):
        dep = rbac_deps.requires_permission(Permission.USERS_READ)
        with pytest.raises(Exception):
            await dep(request=_make_request(), token="tok", db=mock_db, auth_context=ctx)


async def test_get_user_permissions_query(mock_db):
    result = MagicMock()
    result.scalars.return_value.all.return_value = ["users:read", "roles:read"]
    mock_db.execute.return_value = result
    perms = await rbac_deps.get_user_permissions(1, TENANT_ID, mock_db)
    assert perms == {"users:read", "roles:read"}
    mock_db.execute.assert_awaited_once()


async def test_get_cached_permissions_bitmask_cache_hit(mock_db):
    fake_redis = AsyncMock()
    fake_redis.get.return_value = b"42"
    with patch.object(cache_module, "client", fake_redis):
        mask = await rbac_deps.get_cached_permissions_bitmask(1, TENANT_ID, mock_db)
    assert mask == 42
    fake_redis.setex.assert_not_called()


async def test_get_cached_permissions_bitmask_cache_miss_db(mock_db):
    fake_redis = AsyncMock()
    fake_redis.get.return_value = None
    with (
        patch.object(cache_module, "client", fake_redis),
        patch.object(rbac_deps, "get_user_permissions", AsyncMock(return_value={"users:read"})),
    ):
        mask = await rbac_deps.get_cached_permissions_bitmask(1, TENANT_ID, mock_db)
    assert mask == PermissionBitmask.get_bit(Permission.USERS_READ.value)
    fake_redis.setex.assert_awaited_once()


async def test_get_cached_permissions_bitmask_no_redis(mock_db):
    with (
        patch.object(cache_module, "client", None),
        patch.object(rbac_deps, "get_user_permissions", AsyncMock(return_value=set())),
    ):
        mask = await rbac_deps.get_cached_permissions_bitmask(1, TENANT_ID, mock_db)
    assert mask == 0


async def test_has_permission_cached(mock_db):
    mask = PermissionBitmask.get_bit(Permission.USERS_READ.value)
    with patch.object(rbac_deps, "get_cached_permissions_bitmask", AsyncMock(return_value=mask)):
        ok = await rbac_deps.has_permission_cached(1, TENANT_ID, Permission.USERS_READ.value, mock_db)
    assert ok is True


async def test_invalidate_user_permissions_cache_no_redis():
    with patch.object(cache_module, "client", None):
        await rbac_deps.invalidate_user_permissions_cache(1, TENANT_ID)


async def test_invalidate_user_permissions_cache_with_redis():
    fake_redis = AsyncMock()
    with patch.object(cache_module, "client", fake_redis):
        await rbac_deps.invalidate_user_permissions_cache(1, TENANT_ID)
    fake_redis.delete.assert_awaited_once()


async def test_invalidate_users_permissions_cache_batch_empty():
    await rbac_deps.invalidate_users_permissions_cache_batch([], TENANT_ID)


async def test_invalidate_users_permissions_cache_batch_with_redis():
    fake_redis = AsyncMock()
    pipe = AsyncMock()
    pipe_cm = AsyncMock()
    pipe_cm.__aenter__.return_value = pipe
    fake_redis.pipeline = MagicMock(return_value=pipe_cm)
    with patch.object(cache_module, "client", fake_redis):
        await rbac_deps.invalidate_users_permissions_cache_batch([1, 2], TENANT_ID)
    assert pipe.delete.called
    pipe.execute.assert_awaited_once()


async def test_get_current_user_from_token_success(mock_db):
    auth_ctx = MagicMock()
    auth_ctx.user.model_dump.return_value = {"id": 1, "is_superuser": False}
    with patch("src.app.api.deps.get_auth_context", AsyncMock(return_value=auth_ctx)):
        result = await rbac_deps._get_current_user_from_token("tok", mock_db)
    assert result == {"id": 1, "is_superuser": False}


async def test_get_current_user_from_token_error(mock_db):
    with patch("src.app.api.deps.get_auth_context", AsyncMock(side_effect=ValueError("boom"))):
        with pytest.raises(Exception):
            await rbac_deps._get_current_user_from_token("tok", mock_db)


def test_get_bitmask_cache_key():
    key = rbac_deps._get_bitmask_cache_key(1, TENANT_ID)
    assert key.startswith(f"perms:bitmask:{TENANT_ID}:1:")
