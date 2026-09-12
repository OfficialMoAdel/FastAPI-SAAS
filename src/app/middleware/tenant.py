"""Tenant validation middleware for multi-tenant request handling.

This middleware:
- Validates X-Tenant-ID header on all requests except whitelisted paths
- Looks up tenant in database and validates it's active
- Sets tenant context for downstream request processing
- Clears context in finally block to prevent leakage

Performance Optimization (R2-HYBRID MED-1):
- L1 in-memory cache (TTLCache) reduces Redis calls for hot tenants
- L2 Redis cache (1hr TTL) reduces DB queries
- Negative caching prevents cache penetration DoS
- NO-CONNECTION-HOLD: Releases DB connection immediately after resolution (P0-1).
  Enforcement relies on SQLAlchemy event listeners (restore_rls_context)
  which re-apply context to every connection acquired during the request.
"""

import re
import uuid
from typing import Any

from fastapi import Request, status
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware

from src.app.core.config import settings
from src.app.core.db.database import local_session
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.redis_provider import get_redis
from src.app.core.tenancy.cache import (
    _L1_INVALIDATION_CHANNEL,
    _L1_TENANT_CACHE,
    _L1_TENANT_PARSED,
    _TENANT_LOCKS,
    TENANT_CACHE_TTL,
    _get_tenant_lock,
    _publish_tenant_l1_invalidation,
    _start_l1_invalidation_listener,
    _tenant_invalidation_channel,
    invalidate_tenant_l1,
    stop_l1_invalidation_listener,
)
from src.app.core.tenancy.context import NO_TENANT, TenantContext, clear_tenant_context, set_current_tenant
from src.app.modules.tenants.models import Tenant

logger = get_logger(__name__)

# L1 ownership lives in src.app.core.tenancy.cache; the names above are
# re-exported here so existing test/script importers
# (``from src.app.middleware.tenant import _L1_TENANT_CACHE`` etc.) keep
# working during migration. New code must import from core directly.

__all__ = [
    "ADMIN_EXEMPT_PREFIX",
    "TENANT_CACHE_TTL",
    "TENANT_EXEMPT_PATHS",
    "TENANT_EXEMPT_PREFIXES",
    "TENANT_HEADER",
    "TenantMiddleware",
    "_L1_INVALIDATION_CHANNEL",
    "_L1_TENANT_CACHE",
    "_L1_TENANT_PARSED",
    "_TENANT_LOCKS",
    "_get_tenant_lock",
    "_publish_tenant_l1_invalidation",
    "_start_l1_invalidation_listener",
    "_tenant_invalidation_channel",
    "invalidate_tenant_l1",
    "is_path_exempt",
    "stop_l1_invalidation_listener",
]

# P4: slug validation regex compiled once at import
_SLUG_RE = re.compile(r"^[a-z0-9\-]+$")


# P2-7: use the shared common set; tenant adds /api/v1/app_configuration
# which is intentionally tenant-less.
from src.app.core.middleware_exempt import COMMON_EXEMPT_PATHS
from src.app.core.middleware_exempt import is_path_exempt as _shared_is_path_exempt

_TENANT_EXTRA_EXEMPT: frozenset[str] = frozenset({"/api/v1/app_configuration", "/api/metrics"})
TENANT_EXEMPT_PATHS: frozenset[str] = COMMON_EXEMPT_PATHS | _TENANT_EXTRA_EXEMPT

# S-M2 NOTE: registration is NOT exempt — requests to /api/v1/account/register
# carry X-Tenant-ID and resolve a tenant context, but since the signup gate
# (auth router) only bootstraps a tenant's FIRST user, resolving an existing
# active tenant via its guessable slug no longer grants signup rights.
TENANT_EXEMPT_PREFIXES: tuple[str, ...] = ("/api/v1/tenants", "/api/v1/account/sso")


def _admin_mount_prefix() -> str:
    """Resolve the CRUDAdmin mount path for the tenant exemption.

    C-2: the admin panel is a cross-tenant operator surface — it must not
    400 on a missing `X-Tenant-ID` header. Falls back to `/admin` if the
    setting is unavailable (import-time safety).
    """
    try:
        prefix = (settings.admin.CRUD_ADMIN_MOUNT_PATH or "/admin").rstrip("/") or "/"
    except Exception:
        prefix = "/admin"
    return prefix


ADMIN_EXEMPT_PREFIX: str = _admin_mount_prefix()
TENANT_EXEMPT_PREFIXES = (*TENANT_EXEMPT_PREFIXES, ADMIN_EXEMPT_PREFIX)

TENANT_HEADER = "X-Tenant-ID"


def is_path_exempt(path: str) -> bool:
    """Check if the request path is exempt from tenant validation."""
    if _shared_is_path_exempt(path, additional=_TENANT_EXTRA_EXEMPT):
        return True
    for prefix in TENANT_EXEMPT_PREFIXES:
        if path == prefix or path.startswith(f"{prefix}/"):
            return True
    return False


class TenantMiddleware(BaseHTTPMiddleware):
    """Middleware to validate X-Tenant-ID header and set tenant context.

    P8: implements the NATIVE ASGI protocol (__call__ with scope/receive/send)
    instead of Starlette's BaseHTTPMiddleware dispatch bridge — eliminating
    the anyio task-group and memory-stream hop that copies every response
    byte through an extra queue. Subclassing is retained only so isinstance
    checks and test mocks keep working; BaseHTTPMiddleware.__call__ is fully
    overridden.
    """

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        path = request.url.path

        # Check for tenant header
        tenant_slug = request.headers.get(TENANT_HEADER)
        if not tenant_slug or not tenant_slug.strip():
            # If no header provided and path is exempt (or OPTIONS): allow with NO_TENANT
            if is_path_exempt(path) or request.method == "OPTIONS":
                set_current_tenant(NO_TENANT)
                try:
                    await self.app(scope, receive, send)
                finally:
                    clear_tenant_context()
                return

            # Non-exempt path without header -> 400 Missing required header
            logger.warning("Tenant resolution failed: missing_header | path=%s", path)
            from src.app.core.errors import ErrorCode, create_error_response

            response = create_error_response(
                code=ErrorCode.VALIDATION_ERROR,
                message=f"Missing required header: {TENANT_HEADER}",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
            await response(scope, receive, send)
            return

        # Normalize slug to ensure case-insensitive lookup
        tenant_slug = tenant_slug.strip().lower()

        # R1-UNIFIED: connections are NOT held for the whole request; RLS
        # variables are re-applied by the pool checkout listener.
        tenant = await self._get_tenant_by_slug(tenant_slug)

        if tenant is None:
            # On exempt paths (e.g. /health, /api/v1/tenants, /docs), if tenant slug is not found,
            # allow the exempt path to proceed with NO_TENANT rather than blocking probes or bootstrapping.
            if is_path_exempt(path) or request.method == "OPTIONS":
                set_current_tenant(NO_TENANT)
                try:
                    await self.app(scope, receive, send)
                finally:
                    clear_tenant_context()
                return

            logger.warning("Tenant resolution failed: invalid_slug | slug=%s", tenant_slug)
            from src.app.core.errors import ErrorCode, create_error_response

            response = create_error_response(
                code=ErrorCode.FORBIDDEN,
                message="Access denied",
                status_code=status.HTTP_403_FORBIDDEN,
            )
            await response(scope, receive, send)
            return

        if not getattr(tenant, "is_active", True):
            logger.warning("Tenant resolution failed: inactive_tenant | slug=%s", tenant_slug)
            from src.app.core.errors import ErrorCode, create_error_response

            response = create_error_response(
                code=ErrorCode.FORBIDDEN,
                message="Tenant inactive. Contact support.",
                status_code=status.HTTP_403_FORBIDDEN,
            )
            await response(scope, receive, send)
            return

        # Set application-level tenant context (ContextVar); downstream DB
        # sessions automatically pick this up for RLS variables.
        set_current_tenant(tenant)
        try:
            await self.app(scope, receive, send)
        finally:
            # Always clear context to prevent leakage between requests
            try:
                clear_tenant_context()
            except Exception as ctx_err:
                logger.warning("Failed to clear tenant context: %s", ctx_err)

    async def _get_tenant_by_slug(self, slug: str) -> TenantContext | None:
        """Look up tenant by slug with L1+L2 caching and DB fallback (returns TenantContext)."""
        if len(slug) > 64 or not _SLUG_RE.match(slug):
            return None

        # L1: In-memory cache
        try:
            if slug in _L1_TENANT_CACHE:
                cached_data = _L1_TENANT_CACHE[slug]
                if cached_data is not None:
                    return self._get_tenant_from_cache_data(slug, cached_data)
                return None  # Negative cache hit
        except Exception as e:
            logger.warning("Tenant L1 cache read error: %s", e)

        # L2/L3: Lock and lookup
        async with _get_tenant_lock(slug) as lock:
            async with lock:
                # Double-check L1
                if slug in _L1_TENANT_CACHE:
                    data = _L1_TENANT_CACHE[slug]
                    return self._get_tenant_from_cache_data(slug, data) if data else None

                cache_key = f"tenant:slug:{slug}"

                # L2: Redis cache
                redis_client = get_redis()
                if redis_client:
                    try:
                        # Check negative cache first (dedicated key avoids sentinel collision)
                        neg_key = f"tenant:negative:{slug}"
                        # P7: negative marker + cached tenant in ONE pipeline RTT
                        # (previously two sequential awaits: exists then get).
                        pipe = redis_client.pipeline(transaction=False)
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
                            return self._get_tenant_from_cache_data(slug, data)
                    except Exception as e:
                        logger.warning("Tenant L2 cache read error: %s", e)

                # L3: Database
                async with local_session() as session:
                    from src.app.core.db.rls import RLSAuditFailureError, bypass_rls, clear_rls_context

                    stmt = select(Tenant).where(Tenant.slug == slug)
                    try:
                        async with bypass_rls(session, reason="Tenant resolution via middleware"):
                            result = await session.execute(stmt)
                    except RLSAuditFailureError as e:
                        # Q-M9: typed catch — the bypass audit INSERT failed, so
                        # the audited bypass is unavailable. Fall back to the
                        # standard (RLS-protected) query: tenant resolution needs
                        # no bypass when RLS context is absent for this lookup.
                        logger.error(
                            "RLS bypass audit failed during tenant resolution, falling back to standard query",
                            error=str(e),
                        )
                        result = await session.execute(stmt)
                    tenant = result.scalar_one_or_none()

                    # SECURITY FIX: Explicitly clear RLS context before returning connection to pool
                    try:
                        conn = await session.connection()
                        await clear_rls_context(conn)
                    except Exception as e:
                        logger.warning("Failed to clear RLS context in tenant middleware: %s", e)

                    cache_data: dict | None = None
                    if tenant:
                        cache_data = {
                            "id": str(tenant.id),
                            "slug": tenant.slug,
                            "name": tenant.name,
                            "is_active": tenant.is_active,
                        }

                    # Populate caches
                    try:
                        _L1_TENANT_CACHE[slug] = cache_data
                        # P2 micro: dict overwritten → drop any parsed object
                        # built from the previous entry (same as L2-fill pop).
                        _L1_TENANT_PARSED.pop(slug, None)
                    except Exception as e:
                        logger.warning("Tenant L1 cache write error: %s", e)

                    if redis_client:
                        try:
                            if cache_data:
                                # B4: use the canonical TTL (300s) — a 3600s
                                # entry could overwrite a concurrent deactivation's
                                # re-cached is_active=False for a full hour.
                                await redis_client.setex(
                                    cache_key, TENANT_CACHE_TTL, json_dumps(cache_data)
                                )
                                # Clear any stale negative cache for this slug
                                await redis_client.delete(f"tenant:negative:{slug}")
                            else:
                                # Use dedicated negative-cache key instead of sentinel value
                                await redis_client.setex(
                                    f"tenant:negative:{slug}",
                                    settings.redis_cache.TENANT_NEGATIVE_CACHE_TTL,
                                    "1",
                                )
                        except Exception as e:
                            logger.warning("Tenant L2 cache write error: %s", e)

                    if tenant is None:
                        return None
                    return TenantContext.from_model(tenant)

    def _get_tenant_from_cache_data(self, slug: str, data: dict) -> TenantContext:
        """Reconstruct TenantContext from cached dict.

        P2 micro: sync (zero awaits — coroutine overhead per L1 hit removed)
        + frozen parsed-object memo (uuid.UUID re-parse skipped on repeat
        hits). Every _L1_TENANT_CACHE write pairs with a parsed pop, and
        invalidate_tenant_l1 drops both, so the memo tracks the dict cache.
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
