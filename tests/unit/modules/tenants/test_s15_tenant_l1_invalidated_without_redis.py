"""S-15 — the tenant L1 cache must be dropped even when Redis is down.

`_invalidate_tenant_cache` guards `if cache_client.client is None: return`
BEFORE the L1 invalidation block. Its own docstring describes why that block
exists:

    "the Redis deletes above only clear the shared L2 cache; each process keeps
     an in-memory TTLCache that previously served stale is_active=True for up
     to 60s"

So a Redis outage — the precise scenario described — skipped the L1 drop
entirely. A tenant deactivated during an outage stayed `is_active=True` in
every worker for the full 60 s L1 TTL, silently: no log, no other invalidation
path.

The local drop needs no Redis, so it now runs unconditionally and first. Only
the two Redis-dependent legs (L2 delete, cross-worker publish) are skipped, and
the degradation is logged rather than silent.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.tenants.service import TenantService


async def _exhaustion_passthrough(fn, **_kw):
    """Run the invalidation body so the L2 leg is observable."""
    return await fn()


@pytest.fixture
def service() -> TenantService:
    return TenantService(MagicMock())


async def _run(slug: str | None, *, redis_up: bool) -> list[str]:
    """Run _invalidate_tenant_cache and record which legs actually ran."""
    ran: list[str] = []
    svc = TenantService(MagicMock())

    client = MagicMock() if redis_up else None
    fake_cache = MagicMock()
    fake_cache.client = client
    fake_cache.delete = AsyncMock(side_effect=lambda *a, **k: ran.append("l2_delete"))
    fake_cache.set = AsyncMock()

    def _local_drop(s: str | None = None) -> None:
        ran.append("l1_local_drop")

    async def _publish(s: str | None = None) -> None:
        ran.append("l1_publish")

    with (
        patch("src.app.modules.tenants.service.cache_client", fake_cache),
        patch("src.app.modules.tenants.service.get_tenant_cache_key", lambda _t: "k"),
        patch("src.app.modules.tenants.service._get_slug_cache_key", lambda _s: "s"),
        patch(
            "src.app.modules.tenants.service.invalidate_with_exhaustion",
            _exhaustion_passthrough,
        ),
        patch("src.app.core.tenancy.cache.invalidate_tenant_l1", _local_drop),
        patch("src.app.core.tenancy.cache.publish_tenant_l1_invalidation", _publish),
    ):
        await svc._invalidate_tenant_cache(uuid4(), slug)

    return ran


@pytest.mark.asyncio
async def test_l1_is_dropped_when_redis_is_down():
    """The regression: a Redis outage must not skip the local L1 drop."""
    ran = await _run("acme", redis_up=False)
    assert "l1_local_drop" in ran, f"L1 drop was skipped during a Redis outage: {ran}"


@pytest.mark.asyncio
async def test_all_legs_run_when_redis_is_up():
    """No regression: with Redis up, everything still runs."""
    ran = await _run("acme", redis_up=True)
    assert "l1_local_drop" in ran
    assert "l1_publish" in ran
    assert "l2_delete" in ran


@pytest.mark.asyncio
async def test_l1_drop_runs_before_any_redis_leg():
    """Ordering matters: the local drop must not depend on Redis succeeding."""
    ran = await _run("acme", redis_up=True)
    assert ran.index("l1_local_drop") < ran.index("l2_delete")


@pytest.mark.asyncio
async def test_degradation_is_logged_not_silent():
    """The outage path must be observable — silence is what made this hard."""
    svc = TenantService(MagicMock())
    fake_cache = MagicMock()
    fake_cache.client = None

    with (
        patch("src.app.modules.tenants.service.cache_client", fake_cache),
        patch("src.app.core.tenancy.cache.invalidate_tenant_l1", lambda s=None: None),
        patch("src.app.core.tenancy.cache.publish_tenant_l1_invalidation", AsyncMock()),
        patch("src.app.modules.tenants.service.logger") as log,
    ):
        await svc._invalidate_tenant_cache(uuid4(), "acme")

    assert log.warning.called, "a degraded invalidation must warn, not pass silently"
