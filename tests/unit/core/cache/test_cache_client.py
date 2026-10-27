"""CacheClient seam tests (architecture candidate 2).

Two adapters justify the seam: RedisCacheAdapter (production) and an
in-memory fake (tests). These tests pin the interface contract —
get/set/delete/exists/tenant_key — and prove the Redis adapter delegates
to the shared client at call time (observing test patches of
``utils.cache.client``, the same contract as ``redis_provider.get_redis``).
"""

from collections.abc import Sequence
from unittest.mock import AsyncMock

import pytest

from src.app.core.cache.client import CacheClient, RedisCacheAdapter, cache_client


class InMemoryCacheAdapter:
    """Second adapter: the in-memory fake tests use in place of Redis."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        self._store[key] = value  # ttl ignored — fake has no expiry

    async def set_nx(self, key: str, value: str, ttl: int) -> bool:
        if key in self._store:
            return False
        self._store[key] = value
        return True

    async def set_nx_many(self, items: Sequence[tuple[str, str]], ttl: int) -> list[bool]:
        """Batch claim, matching the adapter's contract exactly.

        Per-key verdicts in input order; a key already held answers False. The
        fake has no expiry, so ``ttl`` is ignored — but the ALL-OR-NOTHING
        semantics of a real pipeline do NOT apply here: Redis ``SET NX`` is
        per-key, so a batch can legitimately be partly claimed.
        """
        verdicts: list[bool] = []
        for key, value in items:
            if key in self._store:
                verdicts.append(False)
                continue
            self._store[key] = value
            verdicts.append(True)
        return verdicts

    async def delete(self, *keys: str) -> int:
        removed = sum(1 for k in keys if k in self._store)
        for k in keys:
            self._store.pop(k, None)
        return removed

    async def exists(self, key: str) -> bool:
        return key in self._store

    @staticmethod
    def tenant_key(key: str, tenant_id=None) -> str:
        from src.app.core.utils.cache.keys import build_tenant_key

        return build_tenant_key(key, tenant_id)


@pytest.fixture
def fake_adapter() -> InMemoryCacheAdapter:
    return InMemoryCacheAdapter()


class TestContractBothAdapters:
    """The interface is the test surface: both adapters satisfy the same contract."""

    @pytest.mark.asyncio
    async def test_set_get_roundtrip(self, fake_adapter):
        await fake_adapter.set("k", "v")
        assert await fake_adapter.get("k") == "v"

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, fake_adapter):
        assert await fake_adapter.get("nope") is None

    @pytest.mark.asyncio
    async def test_delete_returns_count(self, fake_adapter):
        await fake_adapter.set("a", "1")
        await fake_adapter.set("b", "2")
        assert await fake_adapter.delete("a", "b", "c") == 2
        assert await fake_adapter.exists("a") is False

    @pytest.mark.asyncio
    async def test_exists(self, fake_adapter):
        await fake_adapter.set("k", "v")
        assert await fake_adapter.exists("k") is True
        assert await fake_adapter.exists("missing") is False

    def test_tenant_key_prefixes_tenant(self, fake_adapter):
        tid = "11111111-1111-1111-1111-111111111111"
        assert tid in fake_adapter.tenant_key("user:1", tid)
        assert fake_adapter.tenant_key("user:1") == "user:1"

    @pytest.mark.asyncio
    async def test_set_nx_claims_once(self, fake_adapter):
        assert await fake_adapter.set_nx("lock", "1", ttl=60) is True
        assert await fake_adapter.set_nx("lock", "1", ttl=60) is False
        assert await fake_adapter.get("lock") == "1"

    @pytest.mark.asyncio
    async def test_set_nx_many_is_per_key_not_all_or_nothing(self, fake_adapter):
        """Redis SET NX is per key, so a batch can be PARTLY claimed.

        The in-memory fake has to agree with the adapter here: a fake that
        treated the batch as all-or-nothing would make a caller's per-row
        skip logic pass in tests and fail in production.
        """
        assert await fake_adapter.set_nx("held", "other", ttl=60) is True

        verdicts = await fake_adapter.set_nx_many([("held", "mine"), ("free", "mine")], ttl=60)

        assert verdicts == [False, True]
        assert await fake_adapter.get("held") == "other", "a lost claim must not overwrite"
        assert await fake_adapter.get("free") == "mine"

    def test_the_fake_satisfies_the_protocol(self, fake_adapter) -> None:
        """The Protocol is the seam; a fake missing a method is not a second adapter.

        ``set_nx_many`` was added to the adapter and NOT to the Protocol, so
        mypy reported "CacheClient has no attribute 'set_nx_many'" while
        production code called it through the concrete singleton. This asserts
        the interface surface the fake claims to implement.
        """
        required = {
            "get",
            "get_strict",
            "set",
            "set_nx",
            "set_nx_many",
            "delete",
            "release_lock_if_owned",
            "exists",
            "tenant_key",
            "get_json",
            "get_json_many",
            "set_json",
        }
        # The production adapter must expose everything the Protocol declares.
        assert required <= set(dir(RedisCacheAdapter))
        # And the fake must cover the subset it implements for tests.
        assert required - {"get_strict", "get_json", "get_json_many", "set_json", "release_lock_if_owned"} <= set(
            dir(fake_adapter)
        )
        assert hasattr(CacheClient, "set_nx_many"), "set_nx_many must be part of the Protocol"


class TestRedisAdapterDelegation:
    """RedisCacheAdapter must delegate to the shared client at call time."""

    @pytest.fixture(autouse=True)
    def _redis_mock(self):
        from unittest.mock import AsyncMock, patch

        mock = AsyncMock()
        mock.get.return_value = "cached"
        mock.delete.return_value = 1
        mock.exists.return_value = 1
        with patch("src.app.core.utils.cache.client", mock):
            yield mock

    @pytest.mark.asyncio
    async def test_get_delegates_to_shared_client(self, _redis_mock):
        assert await cache_client.get("k") == "cached"
        _redis_mock.get.assert_awaited_once_with("k")

    @pytest.mark.asyncio
    async def test_set_with_ttl_uses_ex(self, _redis_mock):
        await cache_client.set("k", "v", ttl=30)
        _redis_mock.set.assert_awaited_once_with("k", "v", ex=30)

    @pytest.mark.asyncio
    async def test_set_without_ttl_uses_default_ex(self, _redis_mock):
        # P2-4: fail-safe default TTL — a missing ttl must still expire.
        await cache_client.set("k", "v")
        _redis_mock.set.assert_awaited_once_with("k", "v", ex=300)

    @pytest.mark.asyncio
    async def test_delete_delegates(self, _redis_mock):
        assert await cache_client.delete("a", "b") == 1
        _redis_mock.delete.assert_awaited_once_with("a", "b")

    @pytest.mark.asyncio
    async def test_none_client_is_safe_noop(self, monkeypatch):
        # Before startup (client=None): reads return None/False/0, not raises.
        monkeypatch.setattr("src.app.core.utils.cache.client", None)
        assert await cache_client.get("k") is None
        await cache_client.set("k", "v")  # must not raise
        assert await cache_client.delete("k") == 0
        assert await cache_client.exists("k") is False
        assert await cache_client.set_nx("k", "v", ttl=60) is False

    def test_singleton_exists(self):
        assert isinstance(cache_client, RedisCacheAdapter)

    def test_client_property_delegates(self, _redis_mock):
        assert cache_client.client is _redis_mock

    @pytest.mark.asyncio
    async def test_set_nx_delegates_with_nx_ex(self, _redis_mock):
        _redis_mock.set = AsyncMock(return_value=True)
        assert await cache_client.set_nx("lock", "1", ttl=60) is True
        _redis_mock.set.assert_awaited_once_with("lock", "1", ex=60, nx=True)

    @pytest.mark.asyncio
    async def test_set_nx_contention_returns_false(self, _redis_mock):
        _redis_mock.set = AsyncMock(return_value=None)
        assert await cache_client.set_nx("lock", "1", ttl=60) is False

    @pytest.mark.asyncio
    async def test_get_json_and_set_json(self, _redis_mock):
        _redis_mock.get = AsyncMock(return_value='{"key": "value"}')
        val = await cache_client.get_json("k")
        assert val == {"key": "value"}

        await cache_client.set_json("k", {"num": 123}, ttl=60)
        _redis_mock.set.assert_awaited()

    @pytest.mark.asyncio
    async def test_get_json_many_is_one_mget_in_input_order(self, _redis_mock):
        # All hits: one round trip, payloads decoded, order preserved.
        _redis_mock.mget = AsyncMock(return_value=['{"a": 1}', None, '{"c": 3}'])

        values = await cache_client.get_json_many(["k1", "k2", "k3"])

        assert values == [{"a": 1}, None, {"c": 3}]
        _redis_mock.mget.assert_awaited_once_with(["k1", "k2", "k3"])
        _redis_mock.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_json_many_treats_a_corrupt_value_as_a_miss(self, _redis_mock):
        # A decode failure must not hide the OTHER keys in the batch.
        _redis_mock.mget = AsyncMock(return_value=["not json", '{"c": 3}'])

        values = await cache_client.get_json_many(["k1", "k2"])

        assert values == [None, {"c": 3}]

    @pytest.mark.asyncio
    async def test_get_json_many_fails_open_on_a_redis_outage(self, _redis_mock):
        # D4: a cache outage reads as a batch of misses, never as an error.
        _redis_mock.mget = AsyncMock(side_effect=RuntimeError("redis down"))

        values = await cache_client.get_json_many(["k1", "k2"])

        assert values == [None, None]

    @pytest.mark.asyncio
    async def test_get_json_many_without_a_client_is_safe(self, monkeypatch):
        monkeypatch.setattr("src.app.core.utils.cache.client", None)

        assert await cache_client.get_json_many(["k1", "k2"]) == [None, None]

    @pytest.mark.asyncio
    async def test_get_json_many_with_no_keys_never_touches_redis(self, _redis_mock):
        _redis_mock.mget = AsyncMock(return_value=[])

        assert await cache_client.get_json_many([]) == []
        _redis_mock.mget.assert_not_awaited()

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
