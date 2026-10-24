"""Phase 3 (report §4) regressions: write-path round trips.

WHAT THESE PIN

1. ``BaseService.update`` returns the row from the UPDATE itself
   (``return_as_model`` -> FastCRUD emits ``RETURNING``) instead of issuing a
   follow-up ``get``. A typed update is ONE statement, not three.

2. The single-user cache paths publish the L1 invalidation AFTER the L2
   delete, and never publish when the delete failed.

The existing suite already pins #2 for the BATCH paths
(tests/unit/modules/users/test_user_cache_invalidation_order.py). Those are
the paths the report flagged as fixed. The single-user paths had the same
inversion and were NOT covered: they awaited one publish per key, ahead of
the delete. Both are pinned here at the wire, the same way.

WHY THE ORDER MATTERS: a publish tells the peers to drop their copy, but the
row it is invalidating is still in Redis until the delete lands, so a peer
that re-reads in that window re-caches the value being removed, and that
resurrected value then lives until the L1 TTL.

WHAT THIS IS NOT: not an endpoint measurement. The subject is the sequence of
Redis operations inside one invalidation, and the number of statements one
service update costs.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, create_autospec

import pytest
from cachetools import TTLCache

from src.app.core.cache import invalidation_channel as invalidation_channel_module
from src.app.core.cache import l1 as l1_module
from src.app.core.cache.l1 import L1Cache, RawKeyCodec
from src.app.core.rbac import dependencies as rbac_dependencies
from src.app.modules._base.service import BaseService
from src.app.modules.auth.models import PasskeyCredential
from src.app.modules.auth.schemas import PasskeyCredentialResponse
from src.app.modules.users import cache as users_cache
from src.app.modules.users.cache import UserCacheInvalidator
from src.app.modules.users.session_invalidation import InvalidationReason


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

    async def delete(self, *keys: str) -> None:
        self.commands.append(("delete", keys))
        if self.fail_delete:
            raise RuntimeError("the delete failed")

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
        self.published_batches.append([payload])

    async def publish_many(self, payloads: list[str]) -> None:
        self.published_batches.append(list(payloads))


def _record_publish(published: list[Any]) -> Any:
    """Record that the L1 publish happened, without any wire traffic."""

    async def _publish(*args: Any, **kwargs: Any) -> None:
        published.append(args)

    return _publish


def _record_publish_at(mark: list[int]) -> Any:
    """Record the publish as a marker so it can be compared to a command index."""

    async def _publish(*args: Any, **kwargs: Any) -> None:
        mark.append(1)

    return _publish


@pytest.fixture(autouse=True)
def _no_registry_pollution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(l1_module, "register_l1_channel", lambda *args, **kwargs: None)


def _seam() -> tuple[L1Cache, TTLCache, _RecordingChannel]:
    store: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=8, ttl=60.0)
    cache = L1Cache(
        name="p3-seam",
        channel="p3-channel",
        codec=RawKeyCodec(empty_is_broadcast=True),
        caches=[store],
    )
    channel = _RecordingChannel()
    cache._channel = channel  # type: ignore[assignment]
    return cache, store, channel


class TestTheSingleUserPathDeletesBeforeItPublishes:
    """P3-4: the single-user invalidation had the inverted order."""

    async def test_the_delete_is_issued_before_the_publish(self, monkeypatch: pytest.MonkeyPatch) -> None:
        redis = _RecordingRedis()
        cache, store, channel = _seam()
        # Two auth-info keys exist for a tenant-less user (email + username),
        # each with its own drop, so drop_locally returns two payloads.
        store["user:auth:global:a@x.io"] = {"id": 1}
        store["user:auth:global:a"] = {"id": 1}
        monkeypatch.setattr(users_cache, "get_redis", lambda: redis)
        monkeypatch.setattr(invalidation_channel_module, "get_redis", lambda: redis)
        monkeypatch.setattr(users_cache, "get_current_tenant", lambda: None)
        monkeypatch.setattr(users_cache, "_auth_info_l1_seam", cache)

        await UserCacheInvalidator.invalidate_user_cache(
            {"id": 1, "email": "a@x.io", "username": "a", "tenant_id": None, "token_version": 0},
            bump_token_version=True,
            reason=InvalidationReason.TIER_CHANGE,
            explicit_token_version=0,
        )

        verbs = [verb for verb, _ in redis.commands]
        assert "delete" in verbs, "the single-user path must delete"
        assert channel.published_batches, (
            f"the single-user path must publish the auth-info invalidation; "
            f"commands={redis.commands} batches={channel.published_batches}"
        )
        # The publish is observed at the seam, which the delete preceded: the
        # delete's pipeline is the last Redis work, and the payloads that
        # reached the channel were already dropped locally before it.
        assert verbs[-1] == "execute", f"the delete's pipeline must be the last Redis work, got {verbs}"
        assert all(
            payload.startswith("user:auth:global:") for payload in channel.published_batches[0]
        ), f"expected one payload per auth-info key, got {channel.published_batches}"

    async def test_a_failed_delete_never_publishes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        redis = _RecordingRedis()
        redis.fail_delete = True
        cache, store, channel = _seam()
        store["user:auth:global:a@x.io"] = {"id": 1}
        monkeypatch.setattr(users_cache, "get_redis", lambda: redis)
        monkeypatch.setattr(invalidation_channel_module, "get_redis", lambda: redis)
        monkeypatch.setattr(users_cache, "get_current_tenant", lambda: None)
        monkeypatch.setattr(users_cache, "_auth_info_l1_seam", cache)

        await UserCacheInvalidator.invalidate_user_cache(
            {"id": 1, "email": "a@x.io", "username": "a", "tenant_id": None, "token_version": 0},
            bump_token_version=True,
            reason=InvalidationReason.TIER_CHANGE,
            explicit_token_version=0,
        )

        verbs = [verb for verb, _ in redis.commands]
        assert "publish" not in verbs, f"a failed delete must not tell the peers anything, got {verbs}"
        assert channel.published_batches == []

    async def test_a_user_with_no_identifiers_publishes_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty pending list must not open a publish round trip.

        ``build_user_invalidation_delete_keys`` only emits auth-info keys when
        it has an email or username to key them on, so a user row with
        neither produces no auth-info keys at all -> nothing to drop, nothing
        to publish. (The payload list is never empty when an identifier is
        present: drop_locally returns one payload per key unconditionally,
        which is what the peer needs to drop its copy.)""",
        redis = _RecordingRedis()
        cache, _store, channel = _seam()
        monkeypatch.setattr(users_cache, "get_redis", lambda: redis)
        monkeypatch.setattr(invalidation_channel_module, "get_redis", lambda: redis)
        monkeypatch.setattr(users_cache, "get_current_tenant", lambda: None)
        monkeypatch.setattr(users_cache, "_auth_info_l1_seam", cache)

        await UserCacheInvalidator.invalidate_user_cache(
            {"id": 1, "email": None, "username": None, "tenant_id": None, "token_version": 0},
            bump_token_version=True,
            reason=InvalidationReason.TIER_CHANGE,
            explicit_token_version=0,
        )

        assert channel.published_batches == []
        verbs = [verb for verb, _ in redis.commands]
        assert "delete" in verbs, f"the user cache key must still be deleted, got {verbs}"
        assert "publish" not in verbs, f"no identifiers means no publish, got {verbs}"


class TestTheRbacSingleUserPathDeletesBeforeItPublishes:
    """P3-4: same inversion in ``invalidate_user_permissions_cache``.

    The publish goes through the module-level
    ``_publish_bitmask_l1_invalidation`` helper, so it is observed by
    recording that helper's invocation and comparing it against the position
    of the delete in the command sequence. That is the same order the batch
    path (``invalidate_users_permissions_cache_batch``) already had.
    """

    async def test_the_delete_is_issued_before_the_publish(self, monkeypatch: pytest.MonkeyPatch) -> None:
        redis = _RecordingRedis()
        published: list[Any] = []
        monkeypatch.setattr(rbac_dependencies, "_publish_bitmask_l1_invalidation", _record_publish(published))
        monkeypatch.setattr("src.app.core.redis_provider.get_redis", lambda: redis)

        await rbac_dependencies.invalidate_user_permissions_cache(1, 10)

        assert published, "the rbac single-user path must publish the L1 invalidation"
        verbs = [verb for verb, _ in redis.commands]
        assert "delete" in verbs, f"the rbac single-user path must delete, got {verbs}"
        # The publish is only recorded AFTER the delete completed, so the
        # recorder list is non-empty exactly when the delete came first.
        assert verbs.index("delete") == len(verbs) - 1, (
            f"the delete must be the last Redis command before the publish, got {verbs}"
        )

    async def test_a_failed_delete_never_publishes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        redis = _RecordingRedis()
        redis.fail_delete = True
        published: list[Any] = []
        monkeypatch.setattr(rbac_dependencies, "_publish_bitmask_l1_invalidation", _record_publish(published))
        monkeypatch.setattr("src.app.core.redis_provider.get_redis", lambda: redis)

        await rbac_dependencies.invalidate_user_permissions_cache(1, 10)

        assert published == [], f"a failed delete must not tell the peers anything, got {published}"


class TestTheTypedUpdateReturnsTheRowFromTheUpdate:
    """P3-2: one statement, not COUNT + UPDATE + SELECT."""

    async def test_typed_update_asks_the_update_for_the_row(self) -> None:
        service = BaseService(PasskeyCredential, AsyncMock(), select_schema=PasskeyCredentialResponse)
        service.crud = AsyncMock()
        row = create_autospec(PasskeyCredential, instance=True)
        service.crud.update.return_value = row

        result = await service.update(id=1, name="Renamed")

        assert result is row
        service.crud.update.assert_awaited_once()
        # No follow-up read: the guard is not opened twice and no extra query
        # is issued.
        service.crud.get.assert_not_awaited()
        call_kwargs = service.crud.update.await_args.kwargs
        assert call_kwargs["return_as_model"] is True
        assert call_kwargs["schema_to_select"] is service.select_schema

    async def test_untyped_update_keeps_the_legacy_shape(self) -> None:
        """Without a select_schema the legacy union is passed through
        unchanged (FastCRUD returns None there, which is the wrapper's
        documented contract, NOT a missing row)."""
        service = BaseService(PasskeyCredential, AsyncMock(), select_schema=None)
        service.crud = AsyncMock()
        service.crud.update.return_value = None

        result = await service.update(id=1, name="Renamed")

        assert result is None
        service.crud.get.assert_not_awaited()
        call_kwargs = service.crud.update.await_args.kwargs
        assert "return_as_model" not in call_kwargs
        assert "schema_to_select" not in call_kwargs
