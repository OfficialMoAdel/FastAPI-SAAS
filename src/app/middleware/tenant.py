"""Tenant validation middleware for multi-tenant request handling.

This middleware:
- Validates X-Tenant-ID header on all requests except whitelisted paths
- Looks up tenant in database and validates it's active
- Sets tenant context for downstream request processing
- Clears context in finally block to prevent leakage

Performance Optimization (R2-HYBRID MED-1):
- L1 in-memory cache (TTLCache) reduces Redis calls for hot tenants
- L2 Redis cache (canonical TENANT_CACHE_TTL, 300s) reduces DB queries
- Negative caching prevents cache penetration DoS
- NO-CONNECTION-HOLD: Releases DB connection immediately after resolution (P0-1).
  Enforcement relies on SQLAlchemy event listeners (restore_rls_context)
  which re-apply context to every connection acquired during the request.
"""


from fastapi import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from src.app.core.config import settings
from src.app.core.db.database import local_session
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
from src.app.core.tenancy.context import NO_TENANT, TenantContext
from src.app.core.tenancy.lifecycle import bound_tenant
from src.app.core.tenancy.resolver import (
    resolve_tenant_by_slug,
    tenant_context_from_cache_data,
)

logger = get_logger(__name__)

# L1 ownership lives in src.app.core.tenancy.cache; the names above are
# re-exported here so existing test/script importers
# (``from src.app.middleware.tenant import _L1_TENANT_CACHE`` etc.) keep
# working during migration. ``get_redis`` is likewise re-exported: the
# adapter resolves the L2 client through this module-global so
# ``mock.patch("src.app.middleware.tenant.get_redis")`` sites
# (unit tests, scripts/live_baseline.py) keep driving resolution.
# New code must import from core directly.

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
    "_TENANT_EXEMPT_MATCHES",
    "_TENANT_LOCKS",
    "_get_tenant_lock",
    "_publish_tenant_l1_invalidation",
    "_start_l1_invalidation_listener",
    "_tenant_invalidation_channel",
    "get_redis",
    "invalidate_tenant_l1",
    "is_path_exempt",
    "resolve_tenant_by_slug",
    "stop_l1_invalidation_listener",
]

# P4: slug validation lives in the canonical resolver
# (src.app.core.tenancy.resolver.is_valid_slug); this module keeps no copy.


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

    BLE triage (muse 22): this is an ACCIDENTAL broad except, so it is
    narrowed rather than annotated. The one thing it was ever written to
    survive is a bad/absent *settings read* — a renamed or removed
    ``admin``/``CRUD_ADMIN_MOUNT_PATH`` field raises AttributeError, a
    non-string value raises TypeError/ValueError from ``.rstrip``. Those
    three still fall back to ``/admin`` exactly as before.

    The defined behaviour on anything else is now to PROPAGATE. That is
    the point: the fallback feeds ``ADMIN_EXEMPT_PREFIX``, which decides
    which paths skip tenant resolution. A blanket ``except Exception``
    here could hide a genuine fault and silently exempt a path that
    should have required ``X-Tenant-ID`` — a fail-OPEN on the tenant
    boundary, reached by a bug nobody could see. Failing at import is
    loud, safe, and the same outcome the docstring already promised.
    """
    try:
        prefix = (settings.admin.CRUD_ADMIN_MOUNT_PATH or "/admin").rstrip("/") or "/"
    except (AttributeError, TypeError, ValueError):
        prefix = "/admin"
    return prefix


ADMIN_EXEMPT_PREFIX: str = _admin_mount_prefix()
TENANT_EXEMPT_PREFIXES = (*TENANT_EXEMPT_PREFIXES, ADMIN_EXEMPT_PREFIX)

TENANT_HEADER = "X-Tenant-ID"

# R5-MICRO: precompute the (prefix, prefix + "/") match pairs once at import
# so per-request exemption checks are a single C-speed startswith(tuple)
# instead of a Python loop building f-strings per prefix.
_TENANT_EXEMPT_MATCHES: tuple[str, ...] = tuple(
    prefix if prefix == "/" else f"{prefix}/" for prefix in TENANT_EXEMPT_PREFIXES
)


def is_path_exempt(path: str) -> bool:
    """Check if the request path is exempt from tenant validation."""
    if _shared_is_path_exempt(path, additional=_TENANT_EXTRA_EXEMPT):
        return True
    return path in TENANT_EXEMPT_PREFIXES or path.startswith(_TENANT_EXEMPT_MATCHES)


class TenantMiddleware:
    """Middleware to validate X-Tenant-ID header and set tenant context.

    P8: implements the NATIVE ASGI protocol (__call__ with scope/receive/send).
    I15: no longer subclasses BaseHTTPMiddleware — the base class was kept
    only for isinstance/mock compat while __call__ was fully overridden,
    which misled readers into expecting dispatch-bridge semantics (and
    mixed-transport double body buffering). Plain ASGI, matching
    rate_limit.py / body_limit.py; patch sites construct it the same way
    (Starlette's add_middleware passes app as the first positional arg).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
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
                with bound_tenant(NO_TENANT):
                    await self.app(scope, receive, send)
                return

            # Non-exempt path without header -> 400 Missing required header
            logger.warning("tenant_resolution_failed_missing_header", path=path)
            from src.app.core.errors import middleware_reject

            response = middleware_reject("tenant_header_missing", header=TENANT_HEADER)
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
                with bound_tenant(NO_TENANT):
                    await self.app(scope, receive, send)
                return

            logger.warning("tenant_resolution_failed_invalid_slug", tenant_slug=tenant_slug)
            from src.app.core.errors import middleware_reject

            response = middleware_reject("tenant_access_denied")
            await response(scope, receive, send)
            return

        if not getattr(tenant, "is_active", True):
            logger.warning("tenant_resolution_failed_inactive", tenant_slug=tenant_slug)
            from src.app.core.errors import middleware_reject

            response = middleware_reject("tenant_inactive")
            await response(scope, receive, send)
            return

        # Set application-level tenant context (ContextVar); downstream DB
        # sessions automatically pick this up for RLS variables. The seam
        # clears it on exit so contexts never leak between requests.
        with bound_tenant(tenant):
            await self.app(scope, receive, send)

    async def _get_tenant_by_slug(self, slug: str) -> TenantContext | None:
        """Look up tenant by slug (thin adapter over the canonical resolver).

        The L1+L2+DB read-through lives in
        :func:`src.app.core.tenancy.resolver.resolve_tenant_by_slug`. This
        method keeps its name/signature so existing
        ``mock.patch("src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug")``
        sites keep working; it forwards ``local_session`` (this module's
        global) as the L3 session factory and ``get_redis()`` as the L2
        client, so patches of ``src.app.middleware.tenant.local_session``
        and ``src.app.middleware.tenant.get_redis`` apply.
        """
        return await resolve_tenant_by_slug(slug, session_factory=local_session, redis_client=get_redis())

    def _get_tenant_from_cache_data(self, slug: str, data: dict) -> TenantContext:
        """Reconstruct TenantContext from cached dict (delegates to resolver)."""
        return tenant_context_from_cache_data(slug, data)
