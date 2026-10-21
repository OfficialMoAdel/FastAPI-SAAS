"""The cache seam fails OPEN — a Redis outage must not fail a request.

The cache is a read-ahead optimisation and a best-effort write. Before the
fix, every adapter method let a ``ConnectionError`` escape: a read raised
instead of reading as a miss, and a write AFTER a succeeded business
operation turned that success into a 500.

The claim primitive is the one place where "fail open" has a sharp edge:
``set_nx`` returning False means "the claim did NOT happen", so a caller
must not treat it as "the claim succeeded".
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.cache.client import RedisCacheAdapter


def _broken_client() -> MagicMock:
    """A client whose every command raises a connection error."""
    client = MagicMock()
    for name in ("get", "set", "exists", "delete"):
        setattr(client, name, AsyncMock(side_effect=ConnectionError("redis is down")))
    return client


class TestFailsOpen:
    async def test_get_reads_as_a_miss(self) -> None:
        adapter = RedisCacheAdapter()
        with patch("src.app.core.redis_provider.get_redis", return_value=_broken_client()):
            assert await adapter.get("k") is None

    async def test_get_strict_propagates_the_read_error(self) -> None:
        """Security-sensitive callers must be able to observe the outage."""
        adapter = RedisCacheAdapter()
        with patch("src.app.core.redis_provider.get_redis", return_value=_broken_client()):
            with pytest.raises(ConnectionError):
                await adapter.get_strict("k")

    async def test_set_does_not_raise(self) -> None:
        """The business operation already succeeded — the write is best effort."""
        adapter = RedisCacheAdapter()
        with patch("src.app.core.redis_provider.get_redis", return_value=_broken_client()):
            await adapter.set("k", "v", ttl=60)  # must not raise

    async def test_set_nx_propagates_the_claim_error(self) -> None:
        """The claim is the ONE operation that does not fail open.

        "The key was taken" and "the claim could not be made" are different
        answers. The billing webhook lock falls back to the DB idempotency
        check when the claim ERRORS, and that fallback is what stops a
        Stripe event from being processed twice — so the error must not be
        collapsed into False.
        """
        adapter = RedisCacheAdapter()
        with patch("src.app.core.redis_provider.get_redis", return_value=_broken_client()):
            with pytest.raises(ConnectionError):
                await adapter.set_nx("k", "v", ttl=60)

    async def test_delete_reports_nothing_removed(self) -> None:
        adapter = RedisCacheAdapter()
        with patch("src.app.core.redis_provider.get_redis", return_value=_broken_client()):
            assert await adapter.delete("k") == 0

    async def test_exists_reports_absent(self) -> None:
        adapter = RedisCacheAdapter()
        with patch("src.app.core.redis_provider.get_redis", return_value=_broken_client()):
            assert await adapter.exists("k") is False


class TestReleaseLockIfOwned:
    async def test_it_releases_only_when_owned(self) -> None:
        """The ownership-token Lua is the primitive the repo already owns."""
        client = MagicMock()
        lua = MagicMock()
        lua.execute = AsyncMock(return_value=1)
        adapter = RedisCacheAdapter()

        with (
            patch("src.app.core.redis_provider.get_redis", return_value=client),
            patch("src.app.core.utils.lua_manager.get_lua_manager", return_value=lua),
        ):
            released = await adapter.release_lock_if_owned("lock:1", "token-a")

        assert released is True
        assert lua.execute.await_args.kwargs["keys"] == ["lock:1"]
        assert lua.execute.await_args.kwargs["args"] == ["token-a"]

    async def test_it_reports_that_the_lock_was_not_owned(self) -> None:
        client = MagicMock()
        lua = MagicMock()
        lua.execute = AsyncMock(return_value=0)
        adapter = RedisCacheAdapter()

        with (
            patch("src.app.core.redis_provider.get_redis", return_value=client),
            patch("src.app.core.utils.lua_manager.get_lua_manager", return_value=lua),
        ):
            released = await adapter.release_lock_if_owned("lock:1", "token-a")

        assert released is False

    async def test_it_never_blind_deletes(self) -> None:
        """A blind delete would remove a lock another worker now owns."""
        client = MagicMock()
        client.delete = AsyncMock()
        lua = MagicMock()
        lua.execute = AsyncMock(side_effect=RuntimeError("lua unavailable"))
        adapter = RedisCacheAdapter()

        with (
            patch("src.app.core.redis_provider.get_redis", return_value=client),
            patch("src.app.core.utils.lua_manager.get_lua_manager", return_value=lua),
        ):
            released = await adapter.release_lock_if_owned("lock:1", "token-a")

        assert released is False
        client.delete.assert_not_awaited()
