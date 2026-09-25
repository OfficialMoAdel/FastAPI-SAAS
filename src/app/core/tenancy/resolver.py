"""Tenant resolution seam — the ONE read-through path for slug resolution.

Canonical owner of tenant resolution (architecture-review A2): L1
(in-process ``TTLCache``) → L2 (Redis) → L3 (database), slug validation,
cache key/TTL rules, and the cache-dict ↔ :class:`TenantContext`
conversion. Both the request path (``src.app.middleware.tenant``) and the
admin/write path (``TenantService``, startup warmer) call into this module
instead of running their own read-through.

Ownership split with :mod:`src.app.core.tenancy.cache`:

- ``cache.py`` owns the L1 *store* (caches, locks, invalidation channel).
- This module owns the *resolution policy* over that store (key building,
  TTLs, negative caching, DB fallback, parsed-object memo).

Backward compatibility: ``src.app.middleware.tenant`` re-exports the L1
store names and keeps ``TenantMiddleware._get_tenant_by_slug`` as a thin
delegating method, so existing test/script importers and
``mock.patch("src.app.middleware.tenant....")`` sites keep working.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, cast

from sqlalchemy import select

from src.app.core.db.database import local_session
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.redis_provider import get_redis
from src.app.core.tenancy.cache import (
    _L1_TENANT_CACHE,
    _L1_TENANT_PARSED,
    TENANT_CACHE_TTL,
    _get_tenant_lock,
    invalidate_tenant_l1,
    publish_tenant_l1_invalidation,
)
from src.app.core.tenancy.context import TenantContext, TenantLike
from src.app.modules.tenants.models import Tenant

logger = get_logger(__name__)

# Sentinel distinguishing "no override passed" from an explicit ``None``
# (caller forces L2 off, e.g. tests patching the middleware's get_redis).
_UNSET: Any = object()

# P4: slug validation — same rule the middleware enforced inline.
_SLUG_RE = re.compile(r"^[a-z0-9\-]+$")
MAX_SLUG_LENGTH = 64


def is_valid_slug(slug: str) -> bool:
    """Return True when ``slug`` is a well-formed tenant slug."""
    if not slug or len(slug) > MAX_SLUG_LENGTH:
        return False
    return _SLUG_RE.match(slug) is not None


def normalize_slug(slug: str) -> str:
    """Normalize a raw header value for case-insensitive lookup."""
    return slug.strip().lower()


def slug_cache_key(slug: str) -> str:
    """Return the canonical L2 key for a tenant slug."""
    return f"tenant:slug:{normalize_slug(slug)}"


def negative_cache_key(slug: str) -> str:
    """Return the canonical L2 negative-cache key for a tenant slug."""
    return f"tenant:negative:{normalize_slug(slug)}"


def tenant_cache_data_from_model(tenant: Tenant) -> dict[str, Any]:
    """Build the minimal cache payload for a tenant row.

    Field set matches the startup warmer exactly (id/slug/name/is_active)
    — no counters or flags, which have their own authoritative stores.
    """
    return {
        "id": str(tenant.id),
        "slug": tenant.slug,
        "name": tenant.name,
        "is_active": tenant.is_active,
    }


def tenant_context_from_cache_data(slug: str, data: dict[str, Any]) -> TenantContext:
    """Reconstruct a :class:`TenantContext` from a cached dict.

    Sync (zero awaits) + frozen parsed-object memo: ``TenantContext`` is
    frozen+slots so sharing one instance across requests is safe and skips
    the ``uuid.UUID`` re-parse on repeat L1 hits. Every
    ``_L1_TENANT_CACHE`` write pairs with a parsed pop, and
    :func:`invalidate_tenant_l1` drops both, so the memo tracks the dict.
    """
    parsed = _L1_TENANT_PARSED.get(slug)
    if parsed is None:
        tenant_id = data["id"]
        if isinstance(tenant_id, str):
            tenant_id = uuid.UUID(tenant_id)
        parsed = TenantContext(
            tenant_id=tenant_id,
            tenant_slug=data["slug"],
            tenant_name=data.get("name", ""),
            is_active=data["is_active"],
        )
        _L1_TENANT_PARSED[slug] = parsed
    return parsed


async def resolve_tenant_by_slug(
    slug: str,
    *,
    session_factory: Any | None = None,
    redis_client: Any = _UNSET,
) -> TenantContext | None:
    """Resolve a tenant slug through L1 → L2 → DB (returns ``TenantContext``).

    Args:
        slug: Normalized tenant slug (already lower-cased/stripped by the
            caller; re-normalized defensively via key helpers).
        session_factory: DB session factory for the L3 fallback. Defaults
            to this module's :data:`local_session` at call time (so patches
            of ``src.app.core.tenancy.resolver.local_session`` apply); the
            middleware passes its own module-global ``local_session`` so
            patches of ``src.app.middleware.tenant.local_session`` keep
            working.
        redis_client: L2 client override. ``_UNSET`` (default) acquires via
            :func:`get_redis` at call time; an explicit ``None`` forces the
            L2 off. The middleware passes its own module-global
            ``get_redis()`` result so patches of
            ``src.app.middleware.tenant.get_redis`` (and, through it,
            ``utils.cache.client``) keep working.

    Returns:
        The resolved :class:`TenantContext`, or ``None`` for an invalid,
        unknown, or negatively-cached slug.
    """
    if not is_valid_slug(slug):
        return None

    # L1: in-memory cache (None value = negative entry).
    try:
        if slug in _L1_TENANT_CACHE:
            cached_data = _L1_TENANT_CACHE[slug]
            if cached_data is not None:
                return tenant_context_from_cache_data(slug, cached_data)
            return None
    except Exception as e:  # noqa: BLE001 - L1 must never break resolution
        logger.warning("Tenant L1 cache read error: %s", e)

    # L2/L3 under the per-slug stampede lock.
    async with _get_tenant_lock(slug) as lock:
        async with lock:
            if slug in _L1_TENANT_CACHE:
                data = _L1_TENANT_CACHE[slug]
                return tenant_context_from_cache_data(slug, data) if data else None

            cache_key = slug_cache_key(slug)

            client = get_redis() if redis_client is _UNSET else redis_client
            if client:
                try:
                    neg_key = negative_cache_key(slug)
                    # P7: negative marker + cached tenant in ONE pipeline RTT.
                    pipe = client.pipeline(transaction=False)
                    pipe.exists(neg_key)
                    pipe.get(cache_key)
                    neg_exists, cached = await pipe.execute()
                    if neg_exists:
                        _L1_TENANT_CACHE[slug] = None
                        _L1_TENANT_PARSED.pop(slug, None)
                        return None
                    if cached:
                        data = json_loads(cached)
                        _L1_TENANT_CACHE[slug] = data
                        _L1_TENANT_PARSED.pop(slug, None)
                        return tenant_context_from_cache_data(slug, data)
                except Exception as e:  # noqa: BLE001 - L2 miss falls to DB
                    logger.warning("Tenant L2 cache read error: %s", e)

            # L3: database fallback (RLS bypass: resolution precedes context).
            factory = session_factory if session_factory is not None else local_session
            async with factory() as session:
                from src.app.core.db.rls import (
                    RLSAuditFailureError,
                    bypass_rls,
                    clear_rls_context,
                )

                stmt = select(Tenant).where(Tenant.slug == slug)
                try:
                    async with bypass_rls(session, reason="Tenant resolution via resolver"):
                        result = await session.execute(stmt)
                except RLSAuditFailureError as e:
                    # Q-M9: typed catch — the audited bypass is unavailable;
                    # fall back to the standard query (this lookup needs no
                    # bypass when RLS context is absent).
                    logger.error(
                        "RLS bypass audit failed during tenant resolution, falling back to standard query",
                        error=str(e),
                    )
                    result = await session.execute(stmt)
                tenant = result.scalar_one_or_none()

                # SECURITY: clear RLS context before returning conn to pool.
                try:
                    conn = await session.connection()
                    await clear_rls_context(conn)
                except Exception as e:  # noqa: BLE001 - cleanup best-effort
                    logger.warning("Failed to clear RLS context in tenant resolver: %s", e)

                cache_data: dict[str, Any] | None = None
                if tenant:
                    cache_data = tenant_cache_data_from_model(tenant)

                try:
                    _L1_TENANT_CACHE[slug] = cache_data
                    _L1_TENANT_PARSED.pop(slug, None)
                except Exception as e:  # noqa: BLE001 - cache write best-effort
                    logger.warning("Tenant L1 cache write error: %s", e)

                if client:
                    try:
                        if cache_data:
                            # B4: canonical 300s TTL — a 3600s entry could
                            # overwrite a concurrent deactivation's
                            # re-cached is_active=False for a full hour.
                            await client.setex(cache_key, TENANT_CACHE_TTL, json_dumps(cache_data))
                            await client.delete(negative_cache_key(slug))
                        else:
                            from src.app.core.config import settings

                            await client.setex(
                                negative_cache_key(slug),
                                settings.redis_cache.TENANT_NEGATIVE_CACHE_TTL,
                                "1",
                            )
                    except Exception as e:  # noqa: BLE001 - cache write best-effort
                        logger.warning("Tenant L2 cache write error: %s", e)

                if tenant is None:
                    return None
                # A Tenant row satisfies TenantLike — it has all four fields —
                # but mypy cannot match a declarative model structurally,
                # because its columns are Mapped[...] descriptors rather than
                # plain attributes. This edge is next in the H-5 queue; when
                # the Tenant import here goes, the cast goes with it.
                return TenantContext.from_model(cast("TenantLike", tenant))


__all__ = [
    "MAX_SLUG_LENGTH",
    "TENANT_CACHE_TTL",
    "invalidate_tenant_l1",
    "is_valid_slug",
    "negative_cache_key",
    "normalize_slug",
    "publish_tenant_l1_invalidation",
    "resolve_tenant_by_slug",
    "slug_cache_key",
    "tenant_cache_data_from_model",
    "tenant_context_from_cache_data",
]
