"""D.6 (OQ-5): the tenant negative cache is SEPARATE from the positive one.

WHY THIS EXISTS
    Before D.6 the negative marker was a ``None`` VALUE inside
    ``_L1_TENANT_CACHE`` — the same ``TTLCache(maxsize=1000, ttl=60)`` that
    holds real tenants. A slug space is unbounded and attacker-controlled, so
    ~1000 probes of nonexistent slugs evicted every live tenant from the hot
    path; each subsequent request then paid an L2 RTT plus an audited L3
    ``bypass_rls`` DB query. That is attacker-controlled eviction of production
    cache — a capacity defect, not a micro-optimisation.

WHAT THIS PINS
    1. EVICTION ISOLATION: filling the negative cache to its capacity leaves
       every positive entry in place (and vice versa).
    2. THE INVARIANT: after creating a tenant, the next resolution for its
       slug does NOT hit a negative entry.
    3. The invalidation seam still drops ALL THREE layers together, so a
       negative marker can never outlive a creation/invalidation.
    4. Cross-worker invalidation reaches the negative layer (the marker is a
       layer of the same ``L1Cache`` key-space, so one payload clears it).

WHY THE CREATE PATH IS ASSERTED SEPARATELY
    The invariant is only true because ``create_tenant`` drops BOTH the L2
    negative key and the L1 layers (bead B7). A test that only exercised the
    resolver would pass even if that drop were removed — the resolver would
    simply re-fill from the DB. The create-path test pins the drop itself.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.tenancy.cache import (
    _L1_TENANT_CACHE,
    _L1_TENANT_NEGATIVE,
    _L1_TENANT_PARSED,
    invalidate_tenant_l1,
)
from src.app.core.tenancy.resolver import resolve_tenant_by_slug

_SLUG = "acme-corp"


@pytest.fixture(autouse=True)
def _clean_l1() -> Any:
    _L1_TENANT_CACHE.clear()
    _L1_TENANT_NEGATIVE.clear()
    _L1_TENANT_PARSED.clear()
    yield
    _L1_TENANT_CACHE.clear()
    _L1_TENANT_NEGATIVE.clear()
    _L1_TENANT_PARSED.clear()


class TestEvictionIsolation:
    def test_a_full_negative_cache_does_not_evict_tenants(self) -> None:
        """The defect this change exists to close."""
        tenant = {"id": "11111111-1111-1111-1111-111111111111", "slug": _SLUG, "name": "Acme", "is_active": True}
        _L1_TENANT_CACHE[_SLUG] = tenant

        # A scanner probes far more nonexistent slugs than the positive
        # cache's capacity.
        for index in range(_L1_TENANT_NEGATIVE.maxsize + 50):
            _L1_TENANT_NEGATIVE[f"probe-{index}"] = True

        assert _SLUG in _L1_TENANT_CACHE, "a slug scanner must not evict a real tenant"
        assert _L1_TENANT_CACHE[_SLUG] == tenant

    def test_a_full_positive_cache_does_not_evict_negatives(self) -> None:
        """Isolation holds in the other direction too."""
        _L1_TENANT_NEGATIVE["known-absent"] = True
        for index in range(_L1_TENANT_CACHE.maxsize + 50):
            _L1_TENANT_CACHE[f"tenant-{index}"] = {
                "id": f"00000000-0000-0000-0000-{index:012d}",
                "slug": f"tenant-{index}",
                "name": "T",
                "is_active": True,
            }
        assert "known-absent" in _L1_TENANT_NEGATIVE

    def test_the_negative_cache_is_bounded(self) -> None:
        """Bounded capacity: the memory a scanner can pin is bounded too."""
        assert _L1_TENANT_NEGATIVE.maxsize == 2000
        assert _L1_TENANT_NEGATIVE.ttl == 60.0
        # Deliberately larger than the positive cache: a negative entry is one
        # boolean, a positive entry is a payload.
        assert _L1_TENANT_NEGATIVE.maxsize > _L1_TENANT_CACHE.maxsize


class TestInvalidationDropsEveryLayer:
    def test_dropping_a_slug_clears_all_three(self) -> None:
        _L1_TENANT_CACHE[_SLUG] = {"id": "1", "slug": _SLUG, "name": "A", "is_active": True}
        _L1_TENANT_NEGATIVE[_SLUG] = True
        _L1_TENANT_PARSED[_SLUG] = MagicMock()

        invalidate_tenant_l1(_SLUG)

        assert _SLUG not in _L1_TENANT_CACHE
        assert _SLUG not in _L1_TENANT_NEGATIVE
        assert _SLUG not in _L1_TENANT_PARSED

    def test_a_broadcast_clears_all_three(self) -> None:
        _L1_TENANT_CACHE["a"] = {"id": "1", "slug": "a", "name": "A", "is_active": True}
        _L1_TENANT_NEGATIVE["b"] = True

        invalidate_tenant_l1(None)

        assert len(_L1_TENANT_CACHE) == 0
        assert len(_L1_TENANT_NEGATIVE) == 0

    def test_a_remote_payload_reaches_the_negative_layer(self) -> None:
        """Cross-worker: the marker is a layer of the same key-space."""
        from src.app.core.tenancy.cache import _tenant_l1_seam

        _L1_TENANT_NEGATIVE[_SLUG] = True
        _tenant_l1_seam.handle(_SLUG)  # what the pub/sub listener applies
        assert _SLUG not in _L1_TENANT_NEGATIVE


class TestAfterCreationTheSlugResolves:
    @pytest.mark.asyncio
    async def test_creation_clears_the_negative_entry_so_resolution_succeeds(self) -> None:
        """The invariant: create -> the next request resolves, no negative hit."""
        from src.app.modules.tenants.service import TenantService

        # A probe ran BEFORE the tenant existed and left a negative marker in
        # this worker (the exact B7 scenario).
        _L1_TENANT_NEGATIVE[_SLUG] = True

        service = TenantService(MagicMock())
        created = MagicMock()
        created.slug = _SLUG
        created.id = "11111111-1111-1111-1111-111111111111"
        created.name = "Acme"
        created.is_active = True

        with (
            patch.object(service, "_cache_tenant", AsyncMock()),
            patch("src.app.core.utils.cache.client", None),
            patch.object(service.crud, "exists", AsyncMock(return_value=False)),
            patch.object(service.crud, "create", AsyncMock(return_value=created)),
        ):
            await service.create_tenant(MagicMock(slug=_SLUG))

        assert _SLUG not in _L1_TENANT_NEGATIVE, "creation must drop the stale negative marker"

        # ...and the resolver now reaches the DB instead of short-circuiting.
        db_tenant = MagicMock()
        db_tenant.id = "11111111-1111-1111-1111-111111111111"
        db_tenant.slug = _SLUG
        db_tenant.name = "Acme"
        db_tenant.is_active = True

        session = MagicMock()
        session.execute = AsyncMock(return_value=_scalar_result(db_tenant))
        session.connection = AsyncMock(return_value=MagicMock())

        with patch("src.app.core.db.rls.bypass_rls", _bypass):
            resolved = await resolve_tenant_by_slug(_SLUG, session_factory=_factory(session), redis_client=None)
        assert resolved is not None and resolved.tenant_slug == _SLUG
        session.execute.assert_awaited(), "a negative hit would have skipped the DB"

    @pytest.mark.asyncio
    async def test_a_negative_hit_still_short_circuits_before_the_db(self) -> None:
        """The negative cache must still DO its job."""
        _L1_TENANT_NEGATIVE[_SLUG] = True
        session = MagicMock()
        session.execute = AsyncMock()

        resolved = await resolve_tenant_by_slug(_SLUG, session_factory=_factory(session), redis_client=None)
        assert resolved is None
        session.execute.assert_not_awaited()


@asynccontextmanager
async def _bypass(session: Any, reason: str, user_id: Any = None) -> Any:
    """A bypass that succeeds without writing an audit row (sibling-test shape)."""
    yield session


def _scalar_result(value: Any) -> Any:
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    return result


def _factory(session: Any) -> Any:
    @asynccontextmanager
    async def _session_factory() -> Any:
        yield session

    return _session_factory
