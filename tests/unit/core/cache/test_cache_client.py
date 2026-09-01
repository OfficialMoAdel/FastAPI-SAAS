"""CacheClient seam tests (architecture candidate 2).

Two adapters justify the seam: RedisCacheAdapter (production) and an
in-memory fake (tests). These tests pin the interface contract —
get/set/delete/exists/tenant_key — and prove the Redis adapter delegates
to the shared client at call time (observing test patches of
``utils.cache.client``, the same contract as ``redis_provider.get_redis``).
"""

import pytest

from src.app.core.cache.client import RedisCacheAdapter, cache_client


class InMemoryCacheAdapter:
    """Second adapter: the in-memory fake tests use in place of Redis."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        self._store[key] = value  # ttl ignored — fake has no expiry

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
    async def test_set_without_ttl_no_ex(self, _redis_mock):
        await cache_client.set("k", "v")
        _redis_mock.set.assert_awaited_once_with("k", "v")

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

    def test_singleton_exists(self):
        assert isinstance(cache_client, RedisCacheAdapter)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
