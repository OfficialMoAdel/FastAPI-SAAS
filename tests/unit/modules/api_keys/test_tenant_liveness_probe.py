"""Unit tests: the tenant-liveness probe reads the key the writers write.

PERF, not correctness: a miss is always safe (it falls through to the
authoritative DB join), only slow. So these tests pin two things:

1. the probe actually reads a key the request path populates (hit rate
   goes from 0/N to N/N), and
2. when the cache genuinely cannot answer, the probe returns None and the
   caller still validates correctly via the DB — fail-safe.

Keyspace under test:
    probe  — ``tenant:slug:{slug}`` (the writer's key) then ``tenant:{id}``
    writers — ``tenancy.resolver.slug_cache_key`` (warmer, middleware L3
    fill) and ``tenancy.cache.get_tenant_cache_key`` (TenantService)
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.core.json_utils import json_dumps
from src.app.core.tenancy.cache import get_tenant_cache_key
from src.app.core.tenancy.context import NO_TENANT, TenantContext
from src.app.core.tenancy.lifecycle import bound_tenant
from src.app.core.tenancy.resolver import slug_cache_key, tenant_cache_data_from_model
from src.app.modules.api_keys.service import APIKeyService

TID = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_TID = uuid.UUID("22222222-2222-2222-2222-222222222222")
SLUG = "host"


class _TenantRow:
    """Minimal stand-in for the Tenant row the writers serialize."""

    def __init__(self, tenant_id: uuid.UUID = TID, slug: str = SLUG, is_active: bool = True) -> None:
        self.id = tenant_id
        self.slug = slug
        self.name = "System"
        self.is_active = is_active


class FakeRedis:
    """In-memory stand-in recording every key the probe reads."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.reads: list[str] = []

    async def get(self, key: str) -> str | None:
        self.reads.append(key)
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = value

    async def delete(self, *keys: str) -> int:
        return sum(self.store.pop(k, None) is not None for k in keys)


def _service() -> APIKeyService:
    return APIKeyService(MagicMock())


def _context(tenant_id: uuid.UUID = TID, slug: str = SLUG) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, tenant_slug=slug, tenant_name="System", is_active=True)


class TestTenantLivenessProbeKeyspace:
    """The probe key must match the key the request path writes."""

    def test_writer_key_is_the_slug_key(self) -> None:
        """The warmer/resolver write the slug key — document the writer side."""
        assert slug_cache_key(SLUG) == "tenant:slug:host"
        assert get_tenant_cache_key(TID) == f"tenant:{TID}"
        assert slug_cache_key(SLUG) != get_tenant_cache_key(TID)

    async def test_probe_hits_the_key_the_warmer_writes(self) -> None:
        """Only the warmer's slug key is populated -> the probe must hit it."""
        redis = FakeRedis()
        redis.store[slug_cache_key(SLUG)] = json_dumps(
            tenant_cache_data_from_model(_TenantRow()), as_str=True
        )

        with (
            patch("src.app.core.redis_provider.get_redis", lambda: redis),
            bound_tenant(_context()),
        ):
            result = await _service()._get_cached_tenant_active(TID)

        assert result is True
        assert redis.reads == [slug_cache_key(SLUG)]
        assert get_tenant_cache_key(TID) not in redis.reads

    async def test_probe_hits_the_key_tenants_service_writes(self) -> None:
        """TenantService._cache_tenant also writes the id key — still hits."""
        redis = FakeRedis()
        payload = json_dumps(tenant_cache_data_from_model(_TenantRow()), as_str=True)
        redis.store[get_tenant_cache_key(TID)] = payload
        redis.store[slug_cache_key(SLUG)] = payload

        with (
            patch("src.app.core.redis_provider.get_redis", lambda: redis),
            bound_tenant(_context()),
        ):
            result = await _service()._get_cached_tenant_active(TID)

        assert result is True
        # The writer's key is preferred; the id key is never needed.
        assert redis.reads == [slug_cache_key(SLUG)]

    async def test_suspended_tenant_returns_false_from_cache(self) -> None:
        """A cached inactive tenant short-circuits the cache hit (T8 purpose)."""
        redis = FakeRedis()
        redis.store[slug_cache_key(SLUG)] = json_dumps(
            tenant_cache_data_from_model(_TenantRow(is_active=False)), as_str=True
        )

        with (
            patch("src.app.core.redis_provider.get_redis", lambda: redis),
            bound_tenant(_context()),
        ):
            result = await _service()._get_cached_tenant_active(TID)

        assert result is False

    async def test_entry_for_a_different_tenant_is_not_trusted(self) -> None:
        """A slug rename can leave an entry for another id under this slug.

        The probe must not report that entry as this key's tenant liveness —
        it returns None, i.e. the fail-safe DB path.
        """
        redis = FakeRedis()
        redis.store[slug_cache_key(SLUG)] = json_dumps(
            tenant_cache_data_from_model(_TenantRow(tenant_id=OTHER_TID)), as_str=True
        )

        with (
            patch("src.app.core.redis_provider.get_redis", lambda: redis),
            bound_tenant(_context()),
        ):
            result = await _service()._get_cached_tenant_active(TID)

        assert result is None
        assert get_tenant_cache_key(TID) in redis.reads

    async def test_miss_path_still_validates_via_db(self) -> None:
        """Fail-safe: an empty cache falls through to the DB join, not a rejection."""
        redis = FakeRedis()
        service = _service()

        with (
            patch("src.app.core.redis_provider.get_redis", lambda: redis),
            bound_tenant(_context()),
        ):
            assert await service._get_cached_tenant_active(TID) is None

            row = MagicMock()
            row.user_id = 1
            row.tenant_id = TID
            row.scopes = '["read"]'
            row.id = 5
            row.expires_at = None
            row.valid_from = None
            result = MagicMock()
            result.scalar_one_or_none.return_value = row
            service.db.execute = AsyncMock(return_value=result)

            with patch.object(service.crud, "update", new_callable=AsyncMock):
                db_result = await service._validate_from_db("hashed")

        assert db_result is not None
        assert db_result["key_id"] == 5

    async def test_probe_without_tenant_context_uses_the_id_key(self) -> None:
        """No request context (e.g. background validation) still probes id key."""
        redis = FakeRedis()
        redis.store[get_tenant_cache_key(TID)] = json_dumps(
            tenant_cache_data_from_model(_TenantRow()), as_str=True
        )

        with patch("src.app.core.redis_provider.get_redis", lambda: redis):
            result = await _service()._get_cached_tenant_active(TID)

        assert result is True
        assert redis.reads == [get_tenant_cache_key(TID)]

    async def test_no_tenant_sentinel_is_not_probed_as_a_slug(self) -> None:
        """The NO_TENANT sentinel's 'no-tenant' slug must not be probed."""
        redis = FakeRedis()

        with (
            patch("src.app.core.redis_provider.get_redis", lambda: redis),
            bound_tenant(NO_TENANT),
        ):
            result = await _service()._get_cached_tenant_active(TID)

        assert result is None
        assert slug_cache_key(NO_TENANT.tenant_slug) not in redis.reads
        assert redis.reads == [get_tenant_cache_key(TID)]

    async def test_empty_tenant_id_short_circuits_without_redis(self) -> None:
        """No tenant id -> None, and no cache read at all."""
        redis = FakeRedis()
        with patch("src.app.core.redis_provider.get_redis", lambda: redis):
            assert await _service()._get_cached_tenant_active(None) is None
        assert redis.reads == []

    async def test_redis_failure_is_swallowed_and_returns_none(self) -> None:
        """The probe must never break validation — an error is a miss."""

        class Broken:
            async def get(self, key):
                raise ConnectionError("redis down")

        with (
            patch("src.app.core.redis_provider.get_redis", lambda: Broken()),
            bound_tenant(_context()),
        ):
            assert await _service()._get_cached_tenant_active(TID) is None

    async def test_probe_hit_rate_is_no_longer_zero(self) -> None:
        """The regression this whole file exists for: 0/N -> N/N."""
        redis = FakeRedis()
        redis.store[slug_cache_key(SLUG)] = json_dumps(
            tenant_cache_data_from_model(_TenantRow()), as_str=True
        )
        service = _service()
        n = 500
        hits = 0
        with (
            patch("src.app.core.redis_provider.get_redis", lambda: redis),
            bound_tenant(_context()),
        ):
            for _ in range(n):
                hits += (await service._get_cached_tenant_active(TID)) is True
        assert hits == n

    async def test_hit_and_miss_are_logged_so_effectiveness_is_observable(self) -> None:
        """The probe's hit/miss decision is logged in production.

        Without this, a keyspace regression (the one this file pins) would
        be invisible in production: a silent always-miss probe looks exactly
        like a healthy fail-safe.
        """
        redis = FakeRedis()
        redis.store[slug_cache_key(SLUG)] = json_dumps(
            tenant_cache_data_from_model(_TenantRow()), as_str=True
        )
        service = _service()

        with (
            patch("src.app.core.redis_provider.get_redis", lambda: redis),
            bound_tenant(_context()),
            patch("src.app.modules.api_keys.service.logger") as log,
        ):
            await service._get_cached_tenant_active(TID)
            hit_calls = [c for c in log.info.call_args_list if "HIT" in str(c)]

            redis.store.clear()
            await service._get_cached_tenant_active(TID)
            miss_calls = [c for c in log.info.call_args_list if "MISS" in str(c)]

        assert hit_calls, "a cache hit must be logged"
        # The log carries the key that answered, so the keyspace in use is
        # visible from the logs alone.
        assert slug_cache_key(SLUG) in str(hit_calls[0])
        assert miss_calls, "a cache miss must be logged"
