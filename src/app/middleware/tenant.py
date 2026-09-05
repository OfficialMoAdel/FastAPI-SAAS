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
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from cachetools import TTLCache
from fastapi import Request, status
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware

from src.app.core.cache.invalidation_channel import InvalidationChannel
from src.app.core.config import settings
from src.app.core.db.database import local_session
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.redis_provider import get_redis
from src.app.core.tenancy.context import NO_TENANT, TenantContext, clear_tenant_context, set_current_tenant
from src.app.modules.tenants.models import Tenant

logger = get_logger(__name__)

# L1 In-Memory Cache for tenant resolution
_L1_TENANT_CACHE: TTLCache[str, dict | None] = TTLCache(maxsize=1000, ttl=60.0)

# Cache stampede protection - track lock waiters to prevent memory leaks
_TENANT_LOCKS: dict[str, dict] = {}

# T9 FIX: cross-worker L1 invalidation channel. Suspending/deleting a tenant
# publishes here so every web process drops its in-process TTLCache entry
# immediately instead of serving stale is_active=True for up to ttl seconds.
_L1_INVALIDATION_CHANNEL = "tenant:l1_invalidate"


def invalidate_tenant_l1(slug: str | None = None) -> None:
    """Drop one (or all) entries from this process's L1 tenant cache."""
    if slug is None or slug == "*":
        _L1_TENANT_CACHE.clear()
    else:
        _L1_TENANT_CACHE.pop(slug, None)



_tenant_invalidation_channel = InvalidationChannel(
    channel=_L1_INVALIDATION_CHANNEL,
    on_message=lambda msg: invalidate_tenant_l1(None if msg == "*" else msg),
    logger_name="tenant_l1",
)

_publish_tenant_l1_invalidation = _tenant_invalidation_channel.publish
_start_l1_invalidation_listener = _tenant_invalidation_channel.start
stop_l1_invalidation_listener = _tenant_invalidation_channel.stop

# P4: slug validation regex compiled once at import
_SLUG_RE = re.compile(r"^[a-z0-9\-]+$")

@asynccontextmanager
async def _get_tenant_lock(slug: str) -> AsyncGenerator[asyncio.Lock, None]:
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


# P2-7: use the shared common set; tenant adds /api/v1/app_configuration
# which is intentionally tenant-less.
from src.app.core.middleware_exempt import COMMON_EXEMPT_PATHS
from src.app.core.middleware_exempt import is_path_exempt as _shared_is_path_exempt

_TENANT_EXTRA_EXEMPT: frozenset[str] = frozenset({"/api/v1/app_configuration"})
TENANT_EXEMPT_PATHS: frozenset[str] = COMMON_EXEMPT_PATHS | _TENANT_EXTRA_EXEMPT

TENANT_EXEMPT_PREFIXES: tuple[str, ...] = ("/api/v1/tenants", "/api/v1/account/sso")

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
                    return await self._get_tenant_from_cache_data(cached_data)
                return None  # Negative cache hit
        except Exception as e:
            logger.warning("Tenant L1 cache read error: %s", e)

        # L2/L3: Lock and lookup
        async with _get_tenant_lock(slug) as lock:
            async with lock:
                # Double-check L1
                if slug in _L1_TENANT_CACHE:
                    data = _L1_TENANT_CACHE[slug]
                    return await self._get_tenant_from_cache_data(data) if data else None

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
                            return None
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
                    try:
                        async with bypass_rls(session, reason="Tenant resolution via middleware"):
                            result = await session.execute(stmt)
                    except RuntimeError as e:
                        if "audit log failure" in str(e):
                            logger.error(
                                "RLS bypass audit failed during tenant resolution, falling back to standard query",
                                error=str(e),
                            )
                            result = await session.execute(stmt)
                        else:
                            raise
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

                    if tenant is None:
                        return None
                    return TenantContext.from_model(tenant)

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
