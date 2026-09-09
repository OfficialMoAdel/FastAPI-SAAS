"""Tenant L1 cache coordinator (P2-6).

Lives outside the HTTP middleware layer so domain services can depend on it
without an upward import (DIP). The L1 TTLCache itself stays in
``src.app.middleware.tenant`` because it is read on every request by the
tenant resolution hot path; moving the cache out would add an import to
every request just to satisfy a layering rule. This module owns the
*coordination* (publish + invalidate + listener) instead, which is what
service-layer callers need.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from cachetools import TTLCache  # noqa: F401 — re-exported for typing

from src.app.core.redis_provider import get_redis

logger = logging.getLogger(__name__)

# B4: canonical L2 TTL for tenant cache entries. Every writer of the
# ``tenant:slug:{slug}`` / ``tenant:{id}`` keys (tenants service, middleware
# L3 fill, cache warmer) must use this value. Previously the middleware L3
# fill and the warmer wrote 3600s entries while the service wrote 300s — a
# concurrent deactivation's re-cached ``is_active=False`` could be overwritten
# by a stale in-flight fill and survive for a full hour. 300s bounds the
# stale-is_active window to five minutes across all writers.
TENANT_CACHE_TTL = 300

# Channel name shared with the listener in src.app.middleware.tenant.
# Keep the literal in one place so middleware and service cannot drift.
_L1_INVALIDATION_CHANNEL = "tenant:l1_invalidate"

# The cache itself stays in middleware for hot-path locality; this module
# exposes a non-resetting view so service callers can invalidate without
# re-importing the middleware module.
_l1_invalidation_task: asyncio.Task | None = None
def get_tenant_cache_key(tenant_id: UUID | str) -> str:
    """Generate Redis cache key for tenant."""
    return f"tenant:{tenant_id}"




def invalidate_tenant_l1(slug: str | None = None) -> None:
    """Drop one (or all) entries from this process's L1 tenant cache.

    Delegates to the middleware's cache; kept here so domain services can
    depend on a stable coordinator import without reaching into HTTP-layer
    internals.
    """
    # Late import to avoid a circular dependency at module load
    # (middleware.tenant imports this module for the listener wiring; this
    # module imports middleware.tenant for the cache reference).
    from src.app.middleware.tenant import invalidate_tenant_l1 as _invalidate

    _invalidate(slug)


async def publish_tenant_l1_invalidation(slug: str | None) -> None:
    """Publish an L1 invalidation so other workers drop their cached entry."""
    redis_client = get_redis()
    if redis_client is None:
        return
    try:
        await redis_client.publish(_L1_INVALIDATION_CHANNEL, slug or "*")
    except Exception as e:  # noqa: BLE001 - invalidation must never break requests
        logger.warning("Failed to publish tenant L1 invalidation: %s", e)


def start_l1_invalidation_listener() -> None:
    """Start (once per event loop) a background subscriber applying invalidations.

    Wraps the middleware's listener so service code that wants the listener
    started alongside (e.g. tests) can call this without importing HTTP
    internals.
    """
    from src.app.middleware.tenant import _start_l1_invalidation_listener

    _start_l1_invalidation_listener()


async def stop_l1_invalidation_listener() -> None:
    """Cancel the L1 invalidation listener on application shutdown."""
    from src.app.middleware.tenant import stop_l1_invalidation_listener as _stop

    await _stop()
