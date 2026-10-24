"""Tenant L1/L2 cache ownership module (canonical owner).

Owns the L1 in-process tenant cache and its cross-worker invalidation
coordination:

- L1 positive: ``_L1_TENANT_CACHE`` (``TTLCache``, 60s, 1000) — hot-path tenant resolution.
- L1 negative: ``_L1_TENANT_NEGATIVE`` (``TTLCache``, 60s, 2000) — "slug absent", in its OWN
  cache (D.6/ADR-0028) so an unbounded, attacker-controlled slug space cannot evict tenants.
- L2: ``TENANT_CACHE_TTL`` (300s) — Redis ``tenant:slug:{slug}`` entries.
- Cross-worker invalidation via ``InvalidationChannel`` (ADR-0004).

NOTE — L1 vs L2 TTLs are intentionally distinct: L1 is a 60.0s in-process
TTLCache for hot tenants; L2 is a 300s Redis TTL bounding the stale
``is_active`` window across writers (B4). Do NOT conflate them.

``src.app.middleware.tenant`` is a consumer/importer of this module (it
re-exports these names for backward compatibility with existing
test/script importers). Single canonical literal for the invalidation
channel lives here: ``_L1_INVALIDATION_CHANNEL``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from cachetools import TTLCache

from src.app.core.cache.l1 import L1Cache, RawKeyCodec
from src.app.core.logger import get_logger
from src.app.core.redis_provider import get_redis
from src.app.core.tenancy.context import TenantContext

logger = get_logger(__name__)

# B4: canonical L2 TTL for tenant cache entries. Every writer of the
# ``tenant:slug:{slug}`` / ``tenant:{id}`` keys (tenants service, middleware
# L3 fill, cache warmer) must use this value. Previously the middleware L3
# fill and the warmer wrote 3600s entries while the service wrote 300s — a
# concurrent deactivation's re-cached ``is_active=False`` could be overwritten
# by a stale in-flight fill and survive for a full hour. 300s bounds the
# stale-is_active window to five minutes across all writers.
TENANT_CACHE_TTL = 300

# L1 in-memory cache for tenant resolution (hot path). Distinct 60.0s TTL —
# see module docstring; do not unify with TENANT_CACHE_TTL (L2, 300s).
#
# D.6 (report OQ-5): POSITIVE entries only. The negative marker used to be a
# ``None`` VALUE in THIS dict, which made attacker-chosen slugs compete for the
# same ``maxsize`` as real tenants: ~1000 probes of nonexistent slugs evicted
# every live tenant from the hot path, and each miss then cost an L2 RTT plus
# an audited L3 ``bypass_rls`` DB query. A slug space is unbounded and
# attacker-controlled, so the two populations are now separate caches with
# separate capacities — eviction in one can never displace the other.
_L1_TENANT_CACHE: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=1000, ttl=60.0)

# Negative L1: "this slug is known to be absent", in its OWN cache.
#
# Sized separately and deliberately larger than the positive cache: a negative
# entry costs one boolean, while a positive entry costs a tenant payload, so
# trading capacity toward the cheap side is safe. It stays bounded, so the
# memory a scanner can pin is bounded too.
_L1_TENANT_NEGATIVE: TTLCache[str, bool] = TTLCache(maxsize=2000, ttl=60.0)

# P2 micro: frozen parsed-object memo. TenantContext is frozen+slots
# (tenancy/context.py), so sharing one instance across requests is safe and
# skips the uuid.UUID re-parse (~1.33 us) on repeat L1 hits. Same
# maxsize/TTL as the dict cache. Correctness contract: every write to
# _L1_TENANT_CACHE in middleware/tenant.py pairs with a pop here, and
# invalidate_tenant_l1 drops every layer — so a parsed entry can never survive
# the dict entry it was built from (TTL expiry drops the earlier-written dict
# first; the subsequent L2/L3 fill rewrites both). The negative cache is a
# layer of the same key-space, so one invalidation clears all three.
_L1_TENANT_PARSED: TTLCache[str, TenantContext] = TTLCache(maxsize=1000, ttl=60.0)

# Cache stampede protection - track lock waiters to prevent memory leaks
_TENANT_LOCKS: dict[str, dict] = {}

# T9 FIX: cross-worker L1 invalidation channel. Suspending/deleting a tenant
# publishes here so every web process drops its in-process TTLCache entry
# immediately instead of serving stale is_active=True for up to ttl seconds.
_L1_INVALIDATION_CHANNEL = "tenant:l1_invalidate"


# The seam owns the grammar (a "*" payload means "drop everything") and the
# drop-then-publish ordering. Both layers are passed together so the parsed memo
# can never outlive the dict entry it was built from — that invariant used to
# live in a comment on _L1_TENANT_PARSED and in the hands of each reviewer.
_tenant_l1_seam = L1Cache(
    name="tenant_l1",
    channel=_L1_INVALIDATION_CHANNEL,
    codec=RawKeyCodec(),
    caches=[_L1_TENANT_CACHE, _L1_TENANT_NEGATIVE, _L1_TENANT_PARSED],
    logger_name="tenant_l1",
)


def invalidate_tenant_l1(slug: str | None = None) -> None:
    """Drop one (or all) entries from this process's L1 tenant cache."""
    if slug is None or slug == "*":
        _tenant_l1_seam.clear()
    else:
        _tenant_l1_seam.drop(slug)


# middleware/tenant.py re-exports these three names for existing test/script
# importers, so they stay alive during migration (swept later if nothing uses
# them). New code should reach for the seam.
_tenant_invalidation_channel = _tenant_l1_seam.channel
_publish_tenant_l1_invalidation = _tenant_l1_seam.publish
_start_l1_invalidation_listener = _tenant_l1_seam.start
stop_l1_invalidation_listener = _tenant_l1_seam.stop


@asynccontextmanager
async def _get_tenant_lock(slug: str) -> AsyncGenerator[asyncio.Lock]:
    """Get an asyncio.Lock for the given slug, cleaning up when unused."""
    lock_info = _TENANT_LOCKS.get(slug)
    if lock_info is None:
        lock_info = {"lock": asyncio.Lock(), "waiters": 0}
        _TENANT_LOCKS[slug] = lock_info

    lock_info["waiters"] += 1
    try:
        yield lock_info["lock"]
    finally:
        lock_info["waiters"] -= 1
        if lock_info["waiters"] == 0:
            _TENANT_LOCKS.pop(slug, None)


def get_tenant_cache_key(tenant_id: UUID | str) -> str:
    """Generate Redis cache key for tenant."""
    return f"tenant:{tenant_id}"


async def publish_tenant_l1_invalidation(slug: str | None) -> None:
    """Publish an L1 invalidation so other workers drop their cached entry."""
    redis_client = get_redis()
    if redis_client is None:
        return
    try:
        await redis_client.publish(_L1_INVALIDATION_CHANNEL, slug or "*")
    except Exception as e:  # noqa: BLE001 - invalidation must never break requests
        logger.warning("tenant_l1_invalidation_publish_failed", error=str(e))
        from src.app.core.monitoring.r1_unified_metrics import record_tenant_l1_publish_failed

        record_tenant_l1_publish_failed()


def start_l1_invalidation_listener() -> None:
    """Start (once per event loop) a background subscriber applying invalidations."""
    _start_l1_invalidation_listener()


# NOTE: underscore-prefixed names stay importable (middleware/tenant.py and
# existing test importers use explicit imports) but are intentionally NOT
# exported — the public surface is the six names below.
__all__ = [
    "TENANT_CACHE_TTL",
    "get_tenant_cache_key",
    "_L1_TENANT_NEGATIVE",
    "invalidate_tenant_l1",
    "publish_tenant_l1_invalidation",
    "start_l1_invalidation_listener",
    "stop_l1_invalidation_listener",
]
