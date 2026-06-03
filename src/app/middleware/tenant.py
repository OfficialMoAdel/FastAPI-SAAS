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

import asyncio
from typing import cast

from cachetools import TTLCache
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from src.app.core.config import settings
from src.app.core.db.database import local_session
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.tenancy.context import TenantContext, clear_tenant_context, set_current_tenant
from src.app.core.utils.cache import client as redis_client
from src.app.modules.tenants.models import Tenant

logger = get_logger(__name__)

# L1 In-Memory Cache for tenant resolution
_L1_TENANT_CACHE: TTLCache[str, dict | None] = TTLCache(maxsize=1000, ttl=60.0)

# Cache stampede protection
_TENANT_LOCKS: dict[str, asyncio.Lock] = {}

def _get_tenant_lock(slug: str) -> asyncio.Lock:
    """Get or create an asyncio.Lock for the given slug."""
    lock = _TENANT_LOCKS.get(slug)
    if lock is None:
        lock = asyncio.Lock()
        _TENANT_LOCKS[slug] = lock
    return lock


TENANT_EXEMPT_PATHS: frozenset[str] = frozenset(
    [
        "/docs",
        "/redoc",
        "/openapi.json",
        "/api/v1/health",
        "/api/v1/ready",
        "/api/v1/app_configuration",
        "/api/v1/billing/webhook",
    ]
)

TENANT_EXEMPT_PREFIXES: tuple[str, ...] = ("/api/v1/tenants",)

TENANT_HEADER = "X-Tenant-ID"


def is_path_exempt(path: str) -> bool:
    """Check if the request path is exempt from tenant validation."""
    if path in TENANT_EXEMPT_PATHS:
        return True
    for prefix in TENANT_EXEMPT_PREFIXES:
        if path == prefix or path.startswith(f"{prefix}/"):
            return True
    return False


class TenantMiddleware(BaseHTTPMiddleware):
    """Middleware to validate X-Tenant-ID header and set tenant context."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process the request and validate tenant header."""
        path = request.url.path

        if is_path_exempt(path) or request.method == "OPTIONS":
            return await call_next(request)

        # PERFORMANCE: Check for missing header BEFORE acquiring a DB connection.
        tenant_slug = request.headers.get(TENANT_HEADER)
        if not tenant_slug or not tenant_slug.strip():
            logger.warning("Tenant resolution failed: missing_header | path=%s", path)
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": f"Missing required header: {TENANT_HEADER}"},
            )

        # Normalize slug to ensure case-insensitive lookup
        tenant_slug = tenant_slug.strip().lower()

        # R1-UNIFIED: Unified Optimized Path
        # We do NOT hold a connection for the entire request.
        # Connections acquired during request processing will have RLS re-applied
        # via the 'after_begin' event listener in src/app/core/db/database.py
        tenant = await self._get_tenant_by_slug(tenant_slug)

        if tenant is None:
            logger.warning("Tenant resolution failed: invalid_slug | slug=%s", tenant_slug)
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Access denied"},
            )

        if not tenant.is_active:
            logger.warning("Tenant resolution failed: inactive_tenant | slug=%s", tenant_slug)
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Tenant inactive. Contact support."},
            )

        # Set application-level tenant context (stored in ContextVar)
        # Downstream DB sessions will automatically use this for RLS variables.
        set_current_tenant(tenant)

        try:
            response = await call_next(request)
            return response
        finally:
            # Always clear context to prevent leakage between requests
            try:
                clear_tenant_context()
            except Exception as ctx_err:
                logger.warning("Failed to clear tenant context: %s", ctx_err)

    async def _get_tenant_by_slug(self, slug: str) -> Tenant | TenantContext | None:
        """Look up tenant by slug with L1+L2 caching and DB fallback."""
        if len(slug) > 64:
            return None

        # L1: In-memory cache
        try:
            if slug in _L1_TENANT_CACHE:
                cached_data = _L1_TENANT_CACHE[slug]
                if cached_data is not None:
                    return await self._get_tenant_from_cache_data(cached_data)
                return None  # Negative cache hit
        except Exception as e:
            logger.warning("Tenant L1 cache read error: %s", e)

        # L2/L3: Lock and lookup
        async with _get_tenant_lock(slug):
            # Double-check L1
            if slug in _L1_TENANT_CACHE:
                data = _L1_TENANT_CACHE[slug]
                return await self._get_tenant_from_cache_data(data) if data else None

            cache_key = f"tenant:slug:{slug}"

            # L2: Redis cache
            if redis_client:
                try:
                    # Check negative cache first (dedicated key avoids sentinel collision)
                    neg_key = f"tenant:negative:{slug}"
                    if await redis_client.exists(neg_key):
                        _L1_TENANT_CACHE[slug] = None
                        return None

                    cached = await redis_client.get(cache_key)
                    if cached:
                        data = json_loads(cached)
                        _L1_TENANT_CACHE[slug] = data
                        return await self._get_tenant_from_cache_data(data)
                except Exception as e:
                    logger.warning("Tenant L2 cache read error: %s", e)

            # L3: Database
            async with local_session() as session:
                from src.app.core.db.rls import bypass_rls, clear_rls_context

                stmt = select(Tenant).where(Tenant.slug == slug)
                async with bypass_rls(session, reason="Tenant resolution via middleware"):
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
                except Exception as e:
                    logger.warning("Tenant L1 cache write error: %s", e)

                if redis_client:
                    try:
                        if cache_data:
                            await redis_client.setex(cache_key, 3600, json_dumps(cache_data))
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

                return cast(Tenant | None, tenant)

    async def _get_tenant_from_cache_data(self, data: dict) -> TenantContext:
        """Reconstruct TenantContext from cached dict."""
        import uuid

        tenant_id = data["id"]
        if isinstance(tenant_id, str):
            tenant_id = uuid.UUID(tenant_id)

        return TenantContext(
            tenant_id=tenant_id,
            tenant_slug=data["slug"],
            tenant_name=data.get("name", ""),
            is_active=data["is_active"],
        )
