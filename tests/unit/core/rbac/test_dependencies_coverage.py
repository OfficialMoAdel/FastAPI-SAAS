"""Unit tests for RBAC permission dependencies (core/rbac/dependencies.py)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest

from src.app.core.rbac import dependencies as rbac_deps
from src.app.core.rbac.bitmask import PermissionBitmask
from src.app.core.rbac.permissions import Permission
from src.app.core.tenancy.context import TenantContext, TenantContextError

TENANT_ID = uuid4()
# R1-14: requires_permission now does a positive isinstance(AuthContext) check,
# so tests must construct the real typed contract instead of duck-typed fakes.
from src.app.api.deps.auth import AuthContext, AuthenticatedUser
from src.app.core.utils import cache as cache_module


def _User(*, is_superuser=False, user_id=1, tenant_id=TENANT_ID) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id,
        email="test@example.com",
        username="testuser",
        is_superuser=is_superuser,
        tenant_id=tenant_id,
    )


def _AuthContext(user, tenant) -> AuthContext:
    return AuthContext(
        user=user,
        token_data=None,
        tenant=tenant,
    )


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
    await dep(request=_make_request(), db=mock_db, auth_context=ctx)


async def test_tenant_mismatch_forbidden(tenant_ctx, mock_db):
    user = _User(is_superuser=False, tenant_id="other-tenant")
    ctx = _AuthContext(user, tenant_ctx)
    dep = rbac_deps.requires_permission(Permission.USERS_READ)
    with pytest.raises(Exception):
        await dep(request=_make_request(), db=mock_db, auth_context=ctx)


async def test_tenant_context_required(mock_db):
    # Candidate 2: the no-tenant refusal is the one no-tenant contract
    # (P3-#14) — TenantContextError (403), not ValidationError (400).
    user = _User()
    ctx = _AuthContext(user, None)
    dep = rbac_deps.requires_permission(Permission.USERS_READ)
    with pytest.raises(TenantContextError):
        await dep(request=_make_request(), db=mock_db, auth_context=ctx)


async def test_user_id_required(tenant_ctx, mock_db):
    # R1-14: AuthenticatedUser.id is a required int, so the defensive
    # "User ID is required" branch is only reachable via an unvalidated
    # instance. model_construct bypasses validation to exercise it.
    user = AuthenticatedUser.model_construct(
        id=None, email="test@example.com", username="testuser", is_superuser=False, tenant_id=TENANT_ID
    )
    ctx = _AuthContext(user, tenant_ctx)
    dep = rbac_deps.requires_permission(Permission.USERS_READ)
    with pytest.raises(Exception):
        await dep(request=_make_request(), db=mock_db, auth_context=ctx)


async def test_permission_granted_via_cache(tenant_ctx, mock_db):
    user = _User(tenant_id=TENANT_ID)
    ctx = _AuthContext(user, tenant_ctx)
    mask = PermissionBitmask.get_bit(Permission.USERS_READ.value)
    with patch.object(rbac_deps, "get_cached_permissions_bitmask", AsyncMock(return_value=mask)):
        dep = rbac_deps.requires_permission(Permission.USERS_READ)
        await dep(request=_make_request(), db=mock_db, auth_context=ctx)


async def test_permission_denied_403(tenant_ctx, mock_db):
    user = _User(tenant_id=TENANT_ID)
    ctx = _AuthContext(user, tenant_ctx)
    with patch.object(rbac_deps, "get_cached_permissions_bitmask", AsyncMock(return_value=0)):
        dep = rbac_deps.requires_permission(Permission.USERS_READ)
        with pytest.raises(Exception):
            await dep(request=_make_request(), db=mock_db, auth_context=ctx)


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


async def test_get_cached_permissions_bitmask_concurrent_miss_singleflight(mock_db):
    """P1-7: concurrent misses on one key share a single join + setex."""
    fake_redis = AsyncMock()
    fake_redis.get.return_value = None
    gate = asyncio.Event()
    calls = 0

    async def _slow_query(*args):
        nonlocal calls
        calls += 1
        await gate.wait()
        return {"users:read"}

    with (
        patch.object(cache_module, "client", fake_redis),
        patch.object(rbac_deps, "get_user_permissions", AsyncMock(side_effect=_slow_query)),
    ):
        tasks = [asyncio.create_task(rbac_deps.get_cached_permissions_bitmask(1, TENANT_ID, mock_db)) for _ in range(5)]
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert calls == 1
        gate.set()
        masks = await asyncio.gather(*tasks)
    expected = PermissionBitmask.get_bit(Permission.USERS_READ.value)
    assert masks == [expected] * 5
    fake_redis.setex.assert_awaited_once()


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


def _fake_pipeline() -> MagicMock:
    """A pipeline whose only awaitable is ``execute``, and whose commands record.

    ``AsyncMock`` is deliberately not used here: it makes every attribute a child
    ``AsyncMock``, so a plain recording spy on ``publish`` cannot be substituted
    without it becoming awaitable - and the code under test calls ``publish``
    synchronously while awaiting only ``execute``.
    """
    pipe = MagicMock()
    pipe.execute = AsyncMock()
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=None)
    return pipe


def _fake_pipeline_cm(pipe: MagicMock) -> MagicMock:
    """The context manager ``redis.pipeline()`` hands back for ``async with``."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=pipe)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


async def test_invalidate_users_permissions_cache_batch_with_redis():
    """S-6: local L1 first, then ONE delete pipeline, then ONE publish pipeline.

    Two pipelines, not one: the local L1 drops must lead the fan-out so this
    process cannot serve a stale mask, and the publishes have to travel together
    so a role change on a wide audience is two round trips rather than two per
    user. Both statements are load-bearing, so both are asserted.
    """
    fake_redis = AsyncMock()
    delete_pipe, publish_pipe = _fake_pipeline(), _fake_pipeline()
    fake_redis.pipeline = MagicMock(side_effect=[_fake_pipeline_cm(delete_pipe), _fake_pipeline_cm(publish_pipe)])

    with patch.object(cache_module, "client", fake_redis):
        await rbac_deps.invalidate_users_permissions_cache_batch([1, 2], TENANT_ID)

    # Exactly two round trips: one to drop the Redis bitmask keys, one to fan the
    # L1 invalidations out. Not one per user.
    assert fake_redis.pipeline.call_args_list == [call(transaction=False), call(transaction=False)]

    delete_pipe.execute.assert_awaited_once()
    deleted = [c.args[0] for c in delete_pipe.delete.call_args_list]
    assert deleted == [
        rbac_deps._get_bitmask_cache_key(1, TENANT_ID),
        rbac_deps._get_bitmask_cache_key(2, TENANT_ID),
    ]
    # The publish round trip must preserve the caller's ordering so subscribers
    # observe the payloads in the order the users were passed.
    assert delete_pipe.publish.call_count == 0
    published = [c.args[1] for c in publish_pipe.publish.call_args_list]
    assert published == [f"1:{TENANT_ID}", f"2:{TENANT_ID}"]
    publish_pipe.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalidate_users_permissions_cache_batch_drops_local_l1_first():
    """The local L1 must be empty BEFORE the cross-process fan-out starts.

    Pub/sub is not an echo: a subscriber does not receive its own message, so
    if the local drop happened after the publish, this process could serve a
    stale mask until its own message came back around.
    """
    fake_redis = AsyncMock()
    delete_pipe, publish_pipe = _fake_pipeline(), _fake_pipeline()
    l1_at_publish: list[dict] = []

    def _publish(*_args, **_kwargs):
        # Snapshot what the local L1 looks like at the moment of the fan-out.
        l1_at_publish.append(dict(rbac_deps._bitmask_l1))

    publish_pipe.publish.side_effect = _publish
    fake_redis.pipeline = MagicMock(side_effect=[_fake_pipeline_cm(delete_pipe), _fake_pipeline_cm(publish_pipe)])

    with patch.object(cache_module, "client", fake_redis):
        for user_id in (1, 2):
            rbac_deps._bitmask_l1[rbac_deps._bitmask_l1_key(user_id, TENANT_ID)] = 1
        try:
            await rbac_deps.invalidate_users_permissions_cache_batch([1, 2], TENANT_ID)
        finally:
            rbac_deps._bitmask_l1.clear()

    assert len(l1_at_publish) == 2
    # Both users' entries were already gone when the first publish was issued.
    assert l1_at_publish[0] == {}


def test_get_bitmask_cache_key():
    key = rbac_deps._get_bitmask_cache_key(1, TENANT_ID)
    assert key.startswith(f"perms:bitmask:{TENANT_ID}:1:")
