"""Tests for src.app.core.utils.cache covering tenant-scoped cache,
stampede protection, the @cache decorator, pattern invalidation, and helpers.

Redis is mocked: the module-level ``client`` is patched to a MagicMock so the
Redis-backed branches execute. Pure helpers are tested without Redis.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.asyncio import Redis

from src.app.core.exceptions.cache_exceptions import (
    CacheIdentificationInferenceError,
    InvalidRequestError,
    MissingClientError,
)
from src.app.core.tenancy.context import TenantContext
from src.app.core.utils import cache as cache_mod
from src.app.core.utils.cache import (
    _build_cache_key,
    _construct_data_dict,
    _delete_keys_by_pattern,
    _extract_data_inside_brackets,
    _format_extra_data,
    _format_prefix,
    _infer_resource_id,
    _pattern_index_key,
    _refresh_cache,
    _refresh_cache_with_lock,
    _tenant_index_key,
    _tenant_prefix_index_key,
    add_to_tenant_index,
    async_get_redis,
    build_tenant_key,
    cache,
    delete_tenant_cache,
    get_tenant_cache,
    get_tenant_index_size,
    get_with_lock,
    get_with_stampede_protection,
    invalidate_by_pattern,
    invalidate_tenant_cache,
    migrate_tenant_cache_index,
    register_key_patterns,
    remove_from_tenant_index,
    set_cache_with_patterns,
    set_tenant_cache,
    tenant_cache_key,
    tenant_cache_pattern,
)


def _make_client() -> MagicMock:
    """Build a MagicMock standing in for a redis.asyncio.Redis client."""
    client = MagicMock(spec=Redis)
    # Pipeline context manager returns an AsyncMock pipe
    pipe = AsyncMock()
    pipe.execute = AsyncMock(return_value=[1])
    client.pipeline = MagicMock(return_value=AsyncMockCM(pipe))
    client.smembers = AsyncMock(return_value=set())
    client.scan = AsyncMock(return_value=(0, []))
    client.get = AsyncMock(return_value=None)
    client.ttl = AsyncMock(return_value=-1)
    client.set = AsyncMock(return_value=True)
    client.setex = AsyncMock(return_value=True)
    client.delete = AsyncMock(return_value=1)
    client.sadd = AsyncMock(return_value=1)
    client.srem = AsyncMock(return_value=1)
    client.scard = AsyncMock(return_value=0)
    return client


class AsyncMockCM:
    """Async context manager wrapper returning the given object on enter."""

    def __init__(self, obj: Any) -> None:
        self._obj = obj

    async def __aenter__(self) -> Any:
        return self._obj

    async def __aexit__(self, *exc: Any) -> None:
        return None


@pytest.fixture
def client() -> MagicMock:
    return _make_client()


@pytest.fixture(autouse=True)
def patch_client(client: MagicMock) -> AsyncGenerator[None, None]:
    """Patch the module-level client to a mock for Redis-backed tests."""
    with patch.object(cache_mod, "client", client):
        yield


# ---------------------------------------------------------------------------
# Pure helpers (no Redis)
# ---------------------------------------------------------------------------


def test_build_tenant_key_with_tenant() -> None:
    assert build_tenant_key("user:1", 5) == "tenant:5:user:1"


def test_build_tenant_key_already_scoped() -> None:
    assert build_tenant_key("tenant:5:user:1", 5) == "tenant:5:user:1"


def test_build_tenant_key_no_tenant() -> None:
    assert build_tenant_key("user:1") == "user:1"


def test_tenant_cache_key() -> None:
    assert tenant_cache_key(1, "user", 42) == "tenant:1:user:42"
    assert tenant_cache_key(1, "permissions", "admin") == "tenant:1:permissions:admin"


def test_tenant_cache_pattern_default() -> None:
    assert tenant_cache_pattern(1) == "tenant:1:*"


def test_tenant_cache_pattern_prefix() -> None:
    assert tenant_cache_pattern(1, "user") == "tenant:1:user:*"


def test_tenant_index_key() -> None:
    assert _tenant_index_key(7) == "tenant_index:7"


def test_tenant_prefix_index_key() -> None:
    assert _tenant_prefix_index_key(7, "user") == "tenant_index:7:user"


def test_extract_data_inside_brackets() -> None:
    assert _extract_data_inside_brackets("The {quick} brown {fox}") == ["quick", "fox"]


def test_construct_data_dict() -> None:
    assert _construct_data_dict(["a", "b"], {"a": 1, "b": 2}) == {"a": 1, "b": 2}
    assert _construct_data_dict(["a"], {}) == {"a": "_a_missing_"}


def test_format_prefix() -> None:
    assert _format_prefix("user:{id}", {"id": 42}) == "user:42"


def test_format_extra_data() -> None:
    result = _format_extra_data({"user_items": "{user_id}"}, {"user_id": 9})
    assert result == {"user_items": 9}


def test_infer_resource_id_found() -> None:
    assert _infer_resource_id({"id": 5, "x": "y"}, int) == 5


def test_infer_resource_id_str() -> None:
    assert _infer_resource_id({"slug": "abc"}, str) == "abc"


def test_infer_resource_id_missing() -> None:
    with pytest.raises(CacheIdentificationInferenceError):
        _infer_resource_id({"x": "str"}, int)


def test_pattern_index_key() -> None:
    assert _pattern_index_key("user:*:profile") == "idx_pattern:user:_STAR_:profile"


# ---------------------------------------------------------------------------
# Tenant-scoped cache (Redis-backed)
# ---------------------------------------------------------------------------


async def test_invalidate_tenant_cache_no_client() -> None:
    with patch.object(cache_mod, "client", None):
        assert await invalidate_tenant_cache(1) == 0


async def test_invalidate_tenant_cache_empty(client: MagicMock) -> None:
    client.smembers = AsyncMock(return_value=set())
    assert await invalidate_tenant_cache(1) == 0


async def test_invalidate_tenant_cache_with_prefix(client: MagicMock) -> None:
    client.smembers = AsyncMock(return_value={b"tenant:1:user:42"})
    client.scan = AsyncMock(return_value=(0, []))
    result = await invalidate_tenant_cache(1, prefix="user")
    assert result == 1


async def test_invalidate_tenant_cache_global(client: MagicMock) -> None:
    client.smembers = AsyncMock(return_value={b"tenant:1:user:42"})
    client.scan = AsyncMock(return_value=(0, []))
    result = await invalidate_tenant_cache(1)
    assert result == 1


async def test_invalidate_tenant_cache_global_sharded(client: MagicMock) -> None:
    # global invalidation scans and deletes sharded index sets (line 173)
    client.smembers = AsyncMock(return_value={b"tenant:1:user:42"})
    client.scan = AsyncMock(return_value=(0, [b"tenant_index:1:user"]))
    result = await invalidate_tenant_cache(1)
    assert result == 1


async def test_add_to_tenant_index_no_client() -> None:
    with patch.object(cache_mod, "client", None):
        assert await add_to_tenant_index(1, "tenant:1:user:42") is False


async def test_add_to_tenant_index_basic(client: MagicMock) -> None:
    assert await add_to_tenant_index(1, "tenant:1:user:42") is True


async def test_add_to_tenant_index_with_ttl(client: MagicMock) -> None:
    assert await add_to_tenant_index(1, "tenant:1:user:42", ttl=100) is True


async def test_remove_from_tenant_index_no_client() -> None:
    with patch.object(cache_mod, "client", None):
        assert await remove_from_tenant_index(1, "tenant:1:user:42") is False


async def test_remove_from_tenant_index_basic(client: MagicMock) -> None:
    assert await remove_from_tenant_index(1, "tenant:1:user:42") is True


async def test_set_tenant_cache_no_client() -> None:
    with patch.object(cache_mod, "client", None):
        assert await set_tenant_cache(1, "user", (42,), "v") is False


async def test_set_tenant_cache_ok(client: MagicMock) -> None:
    assert await set_tenant_cache(1, "user", (42,), "v") is True


async def test_get_tenant_cache_no_client() -> None:
    with patch.object(cache_mod, "client", None):
        assert await get_tenant_cache(1, "user", 42) is None


async def test_get_tenant_cache_ok(client: MagicMock) -> None:
    client.get = AsyncMock(return_value="value")
    assert await get_tenant_cache(1, "user", 42) == "value"


async def test_delete_tenant_cache_no_client() -> None:
    with patch.object(cache_mod, "client", None):
        assert await delete_tenant_cache(1, "user", 42) is False


async def test_delete_tenant_cache_ok(client: MagicMock) -> None:
    assert await delete_tenant_cache(1, "user", 42) is True


async def test_get_tenant_index_size_no_client() -> None:
    with patch.object(cache_mod, "client", None):
        assert await get_tenant_index_size(1) == 0


async def test_get_tenant_index_size_ok(client: MagicMock) -> None:
    client.scard = AsyncMock(return_value=5)
    assert await get_tenant_index_size(1) == 5


async def test_migrate_tenant_cache_index_no_client() -> None:
    with patch.object(cache_mod, "client", None):
        assert await migrate_tenant_cache_index() == {"tenants_migrated": 0, "keys_indexed": 0}


async def test_migrate_tenant_cache_index_ok(client: MagicMock) -> None:
    # str keys (decode_responses=True scenario) to exercise index logic
    client.scan = AsyncMock(return_value=(0, ["tenant:3:user:1"]))
    result = await migrate_tenant_cache_index()
    assert result == {"tenants_migrated": 1, "keys_indexed": 1}


async def test_migrate_tenant_cache_index_iteration_limit(client: MagicMock) -> None:
    # cursor never reaches 0 -> hits MAX_SCAN_ITERATIONS guard
    client.scan = AsyncMock(return_value=(1, []))
    result = await migrate_tenant_cache_index()
    assert result == {"tenants_migrated": 0, "keys_indexed": 0}


# ---------------------------------------------------------------------------
# Stampede protection
# ---------------------------------------------------------------------------


async def test_get_with_stampede_no_client() -> None:
    with patch.object(cache_mod, "client", None):
        fetch = AsyncMock(return_value={"a": 1})
        assert await get_with_stampede_protection("k", fetch) == {"a": 1}


async def test_get_with_stampede_hit(client: MagicMock) -> None:
    client.get = AsyncMock(return_value='{"a": 1}')
    client.ttl = AsyncMock(return_value=100)
    with (
        patch.object(cache_mod, "settings") as settings,
        patch("src.app.core.utils.cache.random.random", return_value=1.0),
    ):
        settings.XFETCH_USE_LUA_SCRIPT = False
        result = await get_with_stampede_protection("k", AsyncMock(), ttl=3600)
    assert result == {"a": 1}


async def test_get_with_stampede_miss_refresh(client: MagicMock) -> None:
    client.get = AsyncMock(return_value=None)
    client.ttl = AsyncMock(return_value=-1)
    client.set = AsyncMock(return_value=True)
    fetch = AsyncMock(return_value={"b": 2})
    with patch.object(cache_mod, "settings") as settings:
        settings.XFETCH_USE_LUA_SCRIPT = False
        result = await get_with_stampede_protection("k", fetch, ttl=3600)
    assert result == {"b": 2}


async def test_get_with_stampede_lua_path(client: MagicMock) -> None:
    client.get = AsyncMock(return_value='{"a": 1}')
    client.ttl = AsyncMock(return_value=100)
    lua = AsyncMock()
    lua.execute = AsyncMock(return_value=[b'{"a": 1}', 100])
    with (
        patch.object(cache_mod, "settings") as settings,
        patch("src.app.core.utils.cache.get_lua_manager", return_value=lua),
        patch("src.app.core.utils.cache.random.random", return_value=1.0),
    ):
        settings.XFETCH_USE_LUA_SCRIPT = True
        result = await get_with_stampede_protection("k", AsyncMock(), ttl=3600)
    assert result == {"a": 1}


async def test_get_with_stampede_lua_fallback(client: MagicMock) -> None:
    client.get = AsyncMock(return_value='{"a": 1}')
    client.ttl = AsyncMock(return_value=100)
    lua = AsyncMock()
    lua.execute = AsyncMock(side_effect=RuntimeError("boom"))
    with (
        patch.object(cache_mod, "settings") as settings,
        patch("src.app.core.utils.cache.get_lua_manager", return_value=lua),
        patch("src.app.core.utils.cache.random.random", return_value=1.0),
    ):
        settings.XFETCH_USE_LUA_SCRIPT = True
        result = await get_with_stampede_protection("k", AsyncMock(), ttl=3600)
    assert result == {"a": 1}


async def test_get_with_stampede_lock_timeout(client: MagicMock) -> None:
    # cache miss, lock not acquired, polling finds nothing -> fetch anyway
    client.get = AsyncMock(return_value=None)
    client.ttl = AsyncMock(return_value=-1)
    client.set = AsyncMock(return_value=False)  # lock not acquired
    fetch = AsyncMock(return_value={"c": 3})
    with (
        patch.object(cache_mod, "settings") as settings,
        patch("src.app.core.utils.cache.asyncio.sleep", new=AsyncMock()),
    ):
        settings.XFETCH_USE_LUA_SCRIPT = False
        result = await get_with_stampede_protection("k", fetch, ttl=3600)
    assert result == {"c": 3}


async def test_refresh_cache_no_client() -> None:
    with patch.object(cache_mod, "client", None):
        fetch = AsyncMock(return_value={"x": 1})
        assert await _refresh_cache("k", fetch, 100) == {"x": 1}


async def test_refresh_cache_ok(client: MagicMock) -> None:
    fetch = AsyncMock(return_value={"x": 1})
    assert await _refresh_cache("k", fetch, 100) == {"x": 1}


async def test_refresh_cache_with_lock_no_client() -> None:
    with patch.object(cache_mod, "client", None):
        fetch = AsyncMock(return_value={"x": 1})
        assert await _refresh_cache_with_lock("k", fetch, 100, "lk") == {"x": 1}


async def test_refresh_cache_with_lock_ok(client: MagicMock) -> None:
    fetch = AsyncMock(return_value={"x": 1})
    assert await _refresh_cache_with_lock("k", fetch, 100, "lk") == {"x": 1}


async def test_get_with_lock_no_client() -> None:
    with patch.object(cache_mod, "client", None):
        fetch = AsyncMock(return_value={"a": 1})
        assert await get_with_lock("k", fetch) == {"a": 1}


async def test_get_with_lock_hit(client: MagicMock) -> None:
    client.get = AsyncMock(return_value='{"a": 1}')
    assert await get_with_lock("k", AsyncMock()) == {"a": 1}


async def test_get_with_lock_miss_acquired(client: MagicMock) -> None:
    # lock acquired, double-check get returns None -> fetch + cache (lines 687-691)
    client.get = AsyncMock(side_effect=[None, None])
    client.set = AsyncMock(return_value=True)
    fetch = AsyncMock(return_value={"b": 2})
    result = await get_with_lock("k", fetch, ttl=100)
    assert result == {"b": 2}


async def test_get_with_lock_double_check_hit(client: MagicMock) -> None:
    # lock acquired, double-check get finds cached -> returns cached (line 684)
    client.get = AsyncMock(side_effect=[None, b'{"cached": 1}'])
    client.set = AsyncMock(return_value=True)
    fetch = AsyncMock(return_value={"fresh": 2})
    result = await get_with_lock("k", fetch, ttl=100)
    assert result == {"cached": 1}
    fetch.assert_not_awaited()


async def test_get_with_lock_miss_locked_then_available(client: MagicMock) -> None:
    client.get = AsyncMock(side_effect=[None, None, '{"c": 3}'])
    client.set = AsyncMock(return_value=False)  # lock not acquired
    fetch = AsyncMock(return_value={"c": 3})
    with patch("src.app.core.utils.cache.asyncio.sleep", new=AsyncMock()):
        result = await get_with_lock("k", fetch, ttl=100, lock_timeout=0.01)
    assert result == {"c": 3}


async def test_get_with_lock_miss_timeout_fetch(client: MagicMock) -> None:
    client.get = AsyncMock(return_value=None)
    client.set = AsyncMock(return_value=False)
    fetch = AsyncMock(return_value={"d": 4})
    with patch("src.app.core.utils.cache.asyncio.sleep", new=AsyncMock()):
        result = await get_with_lock("k", fetch, ttl=100, lock_timeout=0.01)
    assert result == {"d": 4}


# ---------------------------------------------------------------------------
# _delete_keys_by_pattern
# ---------------------------------------------------------------------------


async def test_delete_keys_by_pattern_no_client() -> None:
    with patch.object(cache_mod, "client", None):
        await _delete_keys_by_pattern("user:*")  # should not raise


async def test_delete_keys_by_pattern_ok(client: MagicMock) -> None:
    client.scan = AsyncMock(return_value=(0, [b"user:1", b"user:2"]))
    await _delete_keys_by_pattern("user:*")


async def test_delete_keys_by_pattern_iteration_limit(client: MagicMock) -> None:
    client.scan = AsyncMock(return_value=(1, []))
    await _delete_keys_by_pattern("user:*")


# ---------------------------------------------------------------------------
# _build_cache_key
# ---------------------------------------------------------------------------


def test_build_cache_key_no_tenant() -> None:
    with patch("src.app.core.utils.cache.get_current_tenant", return_value=None):
        key = _build_cache_key("user", 42, {}, False)
    assert key == "user:42"


def test_build_cache_key_with_tenant() -> None:
    tenant = TenantContext(
        tenant_id=__import__("uuid").UUID(int=1),
        tenant_slug="s",
        tenant_name="n",
        is_active=True,
    )
    with patch("src.app.core.utils.cache.get_current_tenant", return_value=tenant):
        key = _build_cache_key("user", 42, {}, False)
    assert key == "tenant:00000000-0000-0000-0000-000000000001:user:42"


def test_build_cache_key_user_scoped_dict() -> None:
    with patch("src.app.core.utils.cache.get_current_tenant", return_value=None):
        key = _build_cache_key("user", 42, {"current_user": {"id": 7}}, True)
    assert key == "user:7:user:42"


def test_build_cache_key_user_scoped_obj() -> None:
    class U:
        id = 9

    with patch("src.app.core.utils.cache.get_current_tenant", return_value=None):
        key = _build_cache_key("user", 42, {"current_user": U()}, True)
    assert key == "user:9:user:42"


# ---------------------------------------------------------------------------
# @cache decorator
# ---------------------------------------------------------------------------


async def test_cache_no_client_raises() -> None:
    with patch.object(cache_mod, "client", None):

        @cache(key_prefix="p", resource_id_name="rid")
        async def handler(request, rid):
            return {"ok": True}

        with pytest.raises(MissingClientError):
            await handler(MagicMock(), rid=1)


async def test_cache_get_hit(client: MagicMock) -> None:
    from fastapi import Request

    client.get = AsyncMock(return_value=b'{"cached": true}')
    client.ttl = AsyncMock(return_value=100)
    request = MagicMock(spec=Request)
    request.method = "GET"
    request.state = MagicMock()

    @cache(key_prefix="p", resource_id_name="rid")
    async def handler(request, rid):
        return {"fresh": True}

    with (
        patch("src.app.core.utils.cache.get_current_tenant", return_value=None),
        patch("src.app.core.utils.cache.random.random", return_value=1.0),
    ):
        resp = await handler(request, rid=1)
    assert resp.body == b'{"cached": true}'


async def test_cache_get_miss_and_set(client: MagicMock) -> None:
    from fastapi import Request

    client.get = AsyncMock(return_value=None)
    client.ttl = AsyncMock(return_value=-1)
    request = MagicMock(spec=Request)
    request.method = "GET"
    request.state = MagicMock()

    @cache(key_prefix="p", resource_id_name="rid")
    async def handler(request, rid):
        return {"fresh": True}

    with patch("src.app.core.utils.cache.get_current_tenant", return_value=None):
        resp = await handler(request, rid=1)
    assert resp.body == b'{"fresh":true}'


async def test_cache_get_with_invalidation_patterns(client: MagicMock) -> None:
    from fastapi import Request

    client.get = AsyncMock(return_value=None)
    client.ttl = AsyncMock(return_value=-1)
    request = MagicMock(spec=Request)
    request.method = "GET"
    request.state = MagicMock()

    @cache(key_prefix="p", resource_id_name="rid", invalidation_patterns=["p:*"])
    async def handler(request, rid):
        return {"fresh": True}

    with patch("src.app.core.utils.cache.get_current_tenant", return_value=None):
        resp = await handler(request, rid=1)
    assert resp.body == b'{"fresh":true}'


async def test_cache_get_with_tenant_index(client: MagicMock) -> None:
    from fastapi import Request

    client.get = AsyncMock(return_value=None)
    client.ttl = AsyncMock(return_value=-1)
    request = MagicMock(spec=Request)
    request.method = "GET"
    request.state = MagicMock()
    tenant = TenantContext(
        tenant_id=__import__("uuid").UUID(int=1),
        tenant_slug="s",
        tenant_name="n",
        is_active=True,
    )

    @cache(key_prefix="p", resource_id_name="rid")
    async def handler(request, rid):
        return {"fresh": True}

    with patch("src.app.core.utils.cache.get_current_tenant", return_value=tenant):
        resp = await handler(request, rid=1)
    assert resp.body == b'{"fresh":true}'


async def test_cache_get_with_to_invalidate_extra_raises() -> None:
    from fastapi import Request

    request = MagicMock(spec=Request)
    request.method = "GET"

    @cache(key_prefix="p", resource_id_name="rid", to_invalidate_extra={"x": "{rid}"})
    async def handler(request, rid):
        return {}

    with patch("src.app.core.utils.cache.get_current_tenant", return_value=None):
        with pytest.raises(InvalidRequestError):
            await handler(request, rid=1)


async def test_cache_non_get_invalidate_extra(client: MagicMock) -> None:
    from fastapi import Request

    client.get = AsyncMock(return_value=None)
    request = MagicMock(spec=Request)
    request.method = "POST"
    request.state = MagicMock()

    @cache(
        key_prefix="p",
        resource_id_name="rid",
        to_invalidate_extra={"other": "{rid}"},
    )
    async def handler(request, rid):
        return {"done": True}

    with patch("src.app.core.utils.cache.get_current_tenant", return_value=None):
        result = await handler(request, rid=1)
    assert result == {"done": True}


async def test_cache_non_get_pattern_invalidate(client: MagicMock) -> None:
    from fastapi import Request

    client.get = AsyncMock(return_value=None)
    client.scan = AsyncMock(return_value=(0, []))
    request = MagicMock(spec=Request)
    request.method = "PUT"
    request.state = MagicMock()

    @cache(
        key_prefix="p",
        resource_id_name="rid",
        pattern_to_invalidate_extra=["other:*"],
    )
    async def handler(request, rid):
        return {"done": True}

    with patch("src.app.core.utils.cache.get_current_tenant", return_value=None):
        result = await handler(request, rid=1)
    assert result == {"done": True}


async def test_cache_infer_resource_id(client: MagicMock) -> None:
    from fastapi import Request

    client.get = AsyncMock(return_value=None)
    client.ttl = AsyncMock(return_value=-1)
    request = MagicMock(spec=Request)
    request.method = "GET"
    request.state = MagicMock()

    @cache(key_prefix="p")
    async def handler(request, rid):
        return {"fresh": True}

    with patch("src.app.core.utils.cache.get_current_tenant", return_value=None):
        resp = await handler(request, rid=1)
    assert resp.body == b'{"fresh":true}'


# ---------------------------------------------------------------------------
# Pattern-based invalidation
# ---------------------------------------------------------------------------


async def test_register_key_patterns_no_client() -> None:
    with patch.object(cache_mod, "client", None):
        await register_key_patterns("k", ["p:*"], 100)  # no-op


async def test_register_key_patterns_empty() -> None:
    await register_key_patterns("k", [], 100)  # no client needed


async def test_register_key_patterns_ok(client: MagicMock) -> None:
    await register_key_patterns("k", ["p:*"], 100)


async def test_invalidate_by_pattern_no_client() -> None:
    with patch.object(cache_mod, "client", None):
        assert await invalidate_by_pattern("p:*") == 0


async def test_invalidate_by_pattern_empty(client: MagicMock) -> None:
    client.smembers = AsyncMock(return_value=set())
    assert await invalidate_by_pattern("p:*") == 0


async def test_invalidate_by_pattern_ok(client: MagicMock) -> None:
    client.smembers = AsyncMock(return_value={b"k1", b"k2"})
    pipe = AsyncMock()
    pipe.execute = AsyncMock(return_value=[2, 1])
    client.pipeline = MagicMock(return_value=AsyncMockCM(pipe))
    assert await invalidate_by_pattern("p:*") == 2


async def test_set_cache_with_patterns_no_client() -> None:
    with patch.object(cache_mod, "client", None):
        assert await set_cache_with_patterns("k", {"a": 1}) is False


async def test_set_cache_with_patterns_ok(client: MagicMock) -> None:
    assert await set_cache_with_patterns("k", {"a": 1}, patterns=["p:*"]) is True


# ---------------------------------------------------------------------------
# async_get_redis
# ---------------------------------------------------------------------------


async def test_async_get_redis_no_client() -> None:
    with patch.object(cache_mod, "client", None):
        with pytest.raises(MissingClientError):
            async for _ in async_get_redis():
                pass


async def test_async_get_redis_ok(client: MagicMock) -> None:
    async for c in async_get_redis():
        assert c is client
        break


# ---------------------------------------------------------------------------
# Extra branch coverage (stampede background refresh, lock polling, decorator edges)
# ---------------------------------------------------------------------------


async def test_get_with_stampede_background_refresh(client: MagicMock) -> None:
    client.get = AsyncMock(return_value=b'{"a": 1}')
    client.ttl = AsyncMock(return_value=10)  # delta = 3600-10 > 0
    client.set = AsyncMock(return_value=True)  # lock acquired
    fetch = AsyncMock(return_value={"refreshed": True})
    with (
        patch.object(cache_mod, "settings") as settings,
        patch("src.app.core.utils.cache.random.random", return_value=0.0),
    ):
        settings.XFETCH_USE_LUA_SCRIPT = False
        result = await get_with_stampede_protection("k", fetch, ttl=3600)
    assert result == {"a": 1}
    # allow the background task to run/cleanup
    await asyncio.sleep(0.05)


async def test_get_with_stampede_background_refresh_locked(client: MagicMock) -> None:
    client.get = AsyncMock(return_value=b'{"a": 1}')
    client.ttl = AsyncMock(return_value=10)
    client.set = AsyncMock(return_value=False)  # lock NOT acquired
    with (
        patch.object(cache_mod, "settings") as settings,
        patch("src.app.core.utils.cache.random.random", return_value=0.0),
    ):
        settings.XFETCH_USE_LUA_SCRIPT = False
        result = await get_with_stampede_protection("k", AsyncMock(), ttl=3600)
    assert result == {"a": 1}


async def test_get_with_stampede_miss_polling_hit(client: MagicMock) -> None:
    # cache miss, lock not acquired, polling eventually finds value
    client.get = AsyncMock(side_effect=[None, None, b'{"c": 3}'])
    client.ttl = AsyncMock(return_value=-1)
    client.set = AsyncMock(return_value=False)
    with (
        patch.object(cache_mod, "settings") as settings,
        patch("src.app.core.utils.cache.asyncio.sleep", new=AsyncMock()),
    ):
        settings.XFETCH_USE_LUA_SCRIPT = False
        result = await get_with_stampede_protection("k", AsyncMock(), ttl=3600)
    assert result == {"c": 3}


async def test_get_with_lock_polling_hit(client: MagicMock) -> None:
    client.get = AsyncMock(side_effect=[None, b'{"c": 3}'])
    client.set = AsyncMock(return_value=False)  # lock not acquired
    with patch("src.app.core.utils.cache.asyncio.sleep", new=AsyncMock()):
        result = await get_with_lock("k", AsyncMock(), ttl=100, lock_timeout=0.01)
    assert result == {"c": 3}


async def test_cache_get_refresh_lock_path(client: MagicMock) -> None:
    from fastapi import Request

    client.get = AsyncMock(return_value=b'{"a": 1}')
    client.ttl = AsyncMock(return_value=10)  # delta > 0
    client.set = AsyncMock(return_value=True)  # lock acquired -> force refresh
    request = MagicMock(spec=Request)
    request.method = "GET"
    request.state = MagicMock()

    @cache(key_prefix="p", resource_id_name="rid")
    async def handler(request, rid):
        return {"fresh": True}

    with (
        patch("src.app.core.utils.cache.get_current_tenant", return_value=None),
        patch("src.app.core.utils.cache.random.random", return_value=0.0),
    ):
        resp = await handler(request, rid=1)
    # refresh-lock forces synchronous re-fetch of fresh data
    assert resp.body == b'{"fresh":true}'
    await asyncio.sleep(0.05)


async def test_cache_non_get_pattern_invalidate_user_scoped(client: MagicMock) -> None:
    from fastapi import Request

    client.get = AsyncMock(return_value=None)
    client.scan = AsyncMock(return_value=(0, []))
    request = MagicMock(spec=Request)
    request.method = "DELETE"
    request.state = MagicMock()

    @cache(
        key_prefix="p",
        resource_id_name="rid",
        user_scoped=True,
        pattern_to_invalidate_extra=["other:*"],
    )
    async def handler(request, rid, current_user):
        return {"done": True}

    with patch("src.app.core.utils.cache.get_current_tenant", return_value=None):
        result = await handler(request, rid=1, current_user={"id": 7})
    assert result == {"done": True}


async def test_cache_non_get_pattern_invalidate_user_obj(client: MagicMock) -> None:
    from fastapi import Request

    client.get = AsyncMock(return_value=None)
    client.scan = AsyncMock(return_value=(0, []))

    class U:
        id = 7

    request = MagicMock(spec=Request)
    request.method = "DELETE"
    request.state = MagicMock()

    @cache(
        key_prefix="p",
        resource_id_name="rid",
        user_scoped=True,
        pattern_to_invalidate_extra=["other:*"],
    )
    async def handler(request, rid, current_user):
        return {"done": True}

    with patch("src.app.core.utils.cache.get_current_tenant", return_value=None):
        result = await handler(request, rid=1, current_user=U())
    assert result == {"done": True}
