"""The invalidation ORDER: the L2 delete lands before the L1 publish.

WHAT THIS PINS

GATE 93vx (the owner's decision of 2026-10-20): the two invalidation paths
now agree - the Redis DELETE lands FIRST and the cross-worker L1 publish
FOLLOWS it.

  * src/app/modules/users/cache.py::invalidate_user_cache_batch used to
    publish BEFORE the delete;
  * src/app/core/rbac/dependencies.py::invalidate_users_permissions_cache_batch
    already deleted first, and still does.

WHY the delete goes first: a publish tells the peers to drop their copy, but
the row it is invalidating is still in Redis until the delete lands, so a peer
that re-reads in that window re-caches the value that is being removed, and
that resurrected value then lives until the L1 TTL.

WHAT A FAILED DELETE MEANS: the peers are never told and the local caches
were already cleared. No stale value is served anywhere - the row was never
deleted, so what the caches hold is still the truth - but the INVALIDATION
itself failed: the token stays valid. That is the security-relevant outcome
and it is surfaced by the exception that reaches the caller.

HOW THE ORDER IS OBSERVED

At the wire, not in the source. The fake Redis below records every command
in the order it was issued, so the delete and the publish are compared as a
RUNTIME SEQUENCE. A test that grepped the source for the order would pass
with the two lines swapped, which is why it would not be evidence.

WHAT THIS IS NOT

Not an endpoint measurement: no request is issued and the app's lifespan is
not run. The subject is the order of two Redis operations inside one
invalidation.
"""

from __future__ import annotations

from typing import Any

import pytest
from cachetools import TTLCache

from src.app.core.cache import invalidation_channel as invalidation_channel_module
from src.app.core.cache import l1 as l1_module
from src.app.core.cache.l1 import L1Cache, RawKeyCodec
from src.app.modules.users import cache as users_cache
from src.app.modules.users.cache import UserCacheInvalidator


class _RecordingPipeline:
    """A Redis pipeline that records the commands it is given."""

    def __init__(self, redis: _RecordingRedis) -> None:
        self._redis = redis
        self._has_delete = False

    async def __aenter__(self) -> _RecordingPipeline:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def delete(self, *keys: str) -> None:
        self._has_delete = True
        self._redis.commands.append(("delete", keys))

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._redis.commands.append(("set", (key,)))

    def publish(self, channel: str, message: str) -> None:
        self._redis.commands.append(("publish", (channel, message)))

    async def execute(self) -> list[Any]:
        if self._has_delete and self._redis.fail_delete:
            raise RuntimeError("the delete failed")
        self._redis.commands.append(("execute", ()))
        return []


class _RecordingRedis:
    """A Redis stand-in that records the command sequence."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, tuple[Any, ...]]] = []
        self.fail_delete = False

    def pipeline(self, transaction: bool = False) -> _RecordingPipeline:
        return _RecordingPipeline(self)

    def __getattr__(self, name: str) -> Any:
        async def _record(*args: Any, **kwargs: Any) -> Any:
            self.commands.append((name, args))
            return None

        return _record


class _RecordingChannel:
    """Stands in for the seam's channel, so no wire traffic happens."""

    def __init__(self) -> None:
        self.published: list[str | None] = []
        self.published_batches: list[list[str]] = []

    async def publish(self, payload: str | None = None) -> None:
        self.published.append(payload)

    async def publish_many(self, payloads: list[str]) -> None:
        self.published_batches.append(list(payloads))


@pytest.fixture(autouse=True)
def _no_registry_pollution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(l1_module, "register_l1_channel", lambda *args, **kwargs: None)


def _seam() -> tuple[L1Cache, TTLCache, _RecordingChannel]:
    store: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=8, ttl=60.0)
    cache = L1Cache(
        name="order-test-seam",
        channel="order-test-channel",
        codec=RawKeyCodec(empty_is_broadcast=True),
        caches=[store],
    )
    channel = _RecordingChannel()
    cache._channel = channel  # type: ignore[assignment]
    return cache, store, channel


class TestTheSeamSplitsTheDropFromThePublish:
    async def test_drop_locally_drops_and_returns_the_payloads_without_publishing(self) -> None:
        cache, store, channel = _seam()
        store["user:auth:global:a@x.io"] = {"id": 1}

        payloads = cache.drop_locally(["user:auth:global:a@x.io"])

        assert payloads == ["user:auth:global:a@x.io"]
        assert "user:auth:global:a@x.io" not in store
        assert channel.published == []
        assert channel.published_batches == []

    async def test_invalidate_many_still_drops_and_publishes(self) -> None:
        cache, store, channel = _seam()
        store["user:auth:global:a@x.io"] = {"id": 1}

        await cache.invalidate_many(["user:auth:global:a@x.io"])

        assert "user:auth:global:a@x.io" not in store
        assert channel.published_batches == [["user:auth:global:a@x.io"]]


class TestTheUsersPathDeletesBeforeItPublishes:
    async def test_the_delete_is_issued_before_the_publish(self, monkeypatch: pytest.MonkeyPatch) -> None:
        redis = _RecordingRedis()
        monkeypatch.setattr(users_cache, "get_redis", lambda: redis)
        monkeypatch.setattr(invalidation_channel_module, "get_redis", lambda: redis)
        monkeypatch.setattr(users_cache, "get_current_tenant", lambda: None)

        await UserCacheInvalidator.invalidate_user_cache_batch(
            [
                {"id": 1, "email": "a@x.io", "username": "a", "tenant_id": None, "token_version": 0},
                {"id": 2, "email": "b@x.io", "username": "b", "tenant_id": None, "token_version": 0},
            ]
        )

        verbs = [verb for verb, _ in redis.commands]
        assert "delete" in verbs, "the batch must delete"
        assert "publish" in verbs, "the batch must publish the auth-info invalidation"
        assert verbs.index("delete") < verbs.index("publish"), (
            f"the L2 delete must land BEFORE the L1 publish, got {verbs}"
        )

    async def test_a_failed_delete_never_publishes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        redis = _RecordingRedis()
        redis.fail_delete = True
        monkeypatch.setattr(users_cache, "get_redis", lambda: redis)
        monkeypatch.setattr(invalidation_channel_module, "get_redis", lambda: redis)
        monkeypatch.setattr(users_cache, "get_current_tenant", lambda: None)

        await UserCacheInvalidator.invalidate_user_cache_batch(
            [{"id": 1, "email": "a@x.io", "username": "a", "tenant_id": None, "token_version": 0}]
        )

        verbs = [verb for verb, _ in redis.commands]
        assert "publish" not in verbs, (
            f"a failed delete must not tell the peers anything, got {verbs}"
        )
        assert any(
            verb == "set" and "invalidation_failed" in str(args) for verb, args in redis.commands
        ), f"the exhaustion policy must write the poison marker, got {redis.commands}"
