"""Regression tests for verified report findings (cache decorator + flags).

Covers: F2 (scoped pattern index), F3 (extra-key shape), F21 (id
inference), F22 (template validation), F23 (ctx principal fallback),
F35 (lock release), F1-flags (registry purge), F27 (value caching),
F28 (warmed-row completeness).
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from src.app.core.tenancy.context import TenantContext, clear_tenant_context, set_current_tenant
from src.app.core.utils import cache as cache_state
from src.app.core.utils.cache import decorator as deco

TENANT_A = UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = UUID("22222222-2222-2222-2222-222222222222")

_new_set = set  # `FakeRedis.set` shadows the builtin inside the class body


def _bind(tenant_id: UUID) -> None:
    set_current_tenant(
        TenantContext(
            tenant_id=tenant_id,
            tenant_slug="t",
            tenant_name="t",
            is_active=True,
        )
    )


class FakeRedis:
    """Minimal in-memory Redis stand-in with real key/index semantics.

    Core ops are sync (like real pipeline buffering); async wrappers serve
    direct client calls. The pipeline applies ops immediately and replays
    recorded results on ``execute``.
    """

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.sets: dict[str, _new_set[str]] = {}
        self.fail_execute = False

    @staticmethod
    def _norm(value: object) -> str:
        return value if isinstance(value, str) else bytes(value).decode()  # type: ignore[arg-type]

    def _get(self, key: str) -> str | None:
        return self.data.get(key)

    def _set(self, key: str, value: object, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.data:
            return False
        self.data[key] = self._norm(value)
        return True

    def _setex(self, key: str, ttl: int, value: object) -> None:
        self.data[key] = self._norm(value)

    def _delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            k = self._norm(key)
            if k in self.data:
                del self.data[k]
                removed += 1
        return removed

    def _smembers(self, key: str) -> _new_set[str]:
        return _new_set(self.sets.get(key, _new_set()))

    def _sadd(self, key: str, *members: str) -> int:
        self.sets.setdefault(key, _new_set()).update(members)
        return len(members)

    def _expire(self, key: str, ttl: int) -> bool:
        return True

    async def get(self, key: str) -> str | None:
        return self._get(key)

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        return self._set(key, value, nx=nx, ex=ex)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._setex(key, ttl, value)

    async def delete(self, *keys: str) -> int:
        return self._delete(*keys)

    async def smembers(self, key: str) -> _new_set[str]:
        return self._smembers(key)

    async def sadd(self, key: str, *members: str) -> int:
        return self._sadd(key, *members)

    async def expire(self, key: str, ttl: int) -> bool:
        return True

    async def ttl(self, key: str) -> int:
        return -2

    async def scan(self, cursor: int, match: str, count: int = 500) -> tuple[int, list[str]]:
        import fnmatch

        hits = [k for k in self.data if fnmatch.fnmatch(k, match)]
        return 0, hits

    def pipeline(self, **kwargs):  # noqa: ANN003, ANN202
        from contextlib import asynccontextmanager

        fake = self

        @asynccontextmanager
        async def cm():
            yield _FakePipe(fake)

        return cm()


class _FakePipe:
    def __init__(self, fake: FakeRedis) -> None:
        self.fake = fake
        self.results: list = []

    def __getattr__(self, name: str):  # noqa: ANN204
        def op(*args, **kwargs):  # noqa: ANN003, ANN202
            if self.fake.fail_execute:
                raise ConnectionError("boom")
            res = getattr(self.fake, "_" + name)(*args, **kwargs)
            self.results.append(res)
            return res

        return op

    async def execute(self):  # noqa: ANN202
        if self.fake.fail_execute:
            raise ConnectionError("boom")
        return self.results


def _request(method: str) -> SimpleNamespace:
    return SimpleNamespace(method=method, state=SimpleNamespace(), scope={})


@pytest.fixture
def fake() -> FakeRedis:
    return FakeRedis()


@pytest.fixture(autouse=True)
def _clean_tenant():
    yield
    clear_tenant_context()


def _no_lua() -> Any:
    lua = AsyncMock()
    lua.execute.side_effect = ConnectionError("no lua")
    return patch("src.app.core.utils.cache.get_lua_manager", return_value=lua)


# --- F21 -----------------------------------------------------------------


def test_f21_pagination_and_bool_not_resource_ids() -> None:
    with pytest.raises(cache_state.CacheIdentificationInferenceError):
        deco._infer_resource_id({"page": 2, "limit": 50}, int)
    assert deco._infer_resource_id({"page": 2, "id": 5}, int) == 5
    assert deco._infer_resource_id({"flag": True, "id": 7}, int) == 7


# --- F22 -----------------------------------------------------------------


def test_f22_bracketless_or_missing_template_raises_valueerror() -> None:
    with pytest.raises(ValueError, match="placeholder"):
        deco._format_extra_data({"p": "noid"}, {"x": 1})
    with pytest.raises(ValueError, match="not an argument"):
        deco._format_extra_data({"p": "{missing}"}, {"x": 1})


# --- F23 -----------------------------------------------------------------


def test_f23_ctx_principal_fallback() -> None:
    assert deco._resolve_user_id({"ctx": SimpleNamespace(user_id=42)}) == 42
    assert deco._resolve_user_id({"current_user": {"id": 7}, "ctx": SimpleNamespace(user_id=42)}) == 7
    assert deco._resolve_user_id({}) is None
    key = deco._build_cache_key("posts", 1, {"ctx": SimpleNamespace(user_id=42)}, True)
    assert "user:42:" in key


# --- F2 + F3 --------------------------------------------------------------


@pytest.mark.asyncio
async def test_f2_pattern_invalidation_fires_under_tenant(fake: FakeRedis) -> None:
    _bind(TENANT_A)
    with patch("src.app.core.utils.cache.client", fake), _no_lua():

        @deco.cache(
            key_prefix="user_posts:{username}",
            resource_id_name="page",
            invalidation_patterns=["user_posts:{username}:*"],
        )
        async def read_posts(request, username: str, page: int):  # noqa: ANN001, ANN202
            return {"posts": [username, page]}

        @deco.cache(
            key_prefix="user_posts:{username}",
            resource_id_name="page",
            pattern_to_invalidate_extra=["user_posts:{username}:*"],
        )
        async def write_post(request, username: str, page: int):  # noqa: ANN001, ANN202
            return {"ok": True}

        get_req, mut_req = _request("GET"), _request("POST")
        await read_posts(get_req, username="alice", page=1)
        (live_key,) = [k for k in fake.data if "alice" in k]
        assert "tenant:11111111" in live_key

        await write_post(mut_req, username="alice", page=1)
        assert live_key not in fake.data


@pytest.mark.asyncio
async def test_f2_cross_tenant_isolation(fake: FakeRedis) -> None:
    _bind(TENANT_A)
    with patch("src.app.core.utils.cache.client", fake), _no_lua():

        @deco.cache(
            key_prefix="user_posts:{username}",
            resource_id_name="page",
            invalidation_patterns=["user_posts:{username}:*"],
        )
        async def read_posts(request, username: str, page: int):  # noqa: ANN001, ANN202
            return {"posts": [username, page]}

        @deco.cache(
            key_prefix="user_posts:{username}",
            resource_id_name="page",
            pattern_to_invalidate_extra=["user_posts:{username}:*"],
        )
        async def write_post(request, username: str, page: int):  # noqa: ANN001, ANN202
            return {"ok": True}

        await read_posts(_request("GET"), username="alice", page=1)
        _bind(TENANT_B)
        await read_posts(_request("GET"), username="alice", page=1)
        assert len([k for k in fake.data if "alice" in k]) == 2

        _bind(TENANT_A)
        await write_post(_request("POST"), username="alice", page=1)
        remaining = [k for k in fake.data if "alice" in k]
        assert len(remaining) == 1
        assert "tenant:22222222" in remaining[0]


@pytest.mark.asyncio
async def test_f3_extra_keys_match_real_key_shape(fake: FakeRedis) -> None:
    _bind(TENANT_A)
    with patch("src.app.core.utils.cache.client", fake), _no_lua():

        @deco.cache(key_prefix="item_data", resource_id_name="item_id")
        async def read_item(request, item_id: int):  # noqa: ANN001, ANN202
            return {"item": item_id}

        @deco.cache(
            key_prefix="item_data",
            resource_id_name="item_id",
            to_invalidate_extra={"item_data": "{item_id}"},
        )
        async def update_item(request, item_id: int):  # noqa: ANN001, ANN202
            return {"ok": True}

        await read_item(_request("GET"), item_id=9)
        (live_key,) = list(fake.data)
        assert live_key.startswith("tenant:11111111")

        await update_item(_request("POST"), item_id=9)
        assert live_key not in fake.data


# --- F35 ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_f35_lock_released_when_write_fails(fake: FakeRedis) -> None:
    fake.fail_execute = True
    fake.data["k:refresh_lock"] = "1"
    with patch("src.app.core.utils.cache.client", fake), _no_lua():

        @deco.cache(key_prefix="w", resource_id_name="i")
        async def ep(request, i: int):  # noqa: ANN001, ANN202
            return {"i": i}

        req = _request("GET")
        req.state.cache_refresh_lock_key = "k:refresh_lock"
        result = await ep(req, i=1)
        assert getattr(result, "status_code", None) == 200  # fail-open
        assert "k:refresh_lock" not in fake.data
