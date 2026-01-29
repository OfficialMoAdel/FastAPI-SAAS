"""Tenant validation middleware for multi-tenant request handling.

This middleware:
- Validates X-Tenant-ID header on all requests except whitelisted paths
- Looks up tenant in database and validates it's active
- Sets tenant context for downstream request processing
- Sets PostgreSQL RLS session variables for database-level isolation
- Clears context in finally block to prevent leakage

Performance Optimization (R2-HYBRID MED-1):
- L1 in-memory cache (TTLCache) reduces Redis calls for hot tenants
- L2 Redis cache (1hr TTL) reduces DB queries
- Negative caching prevents cache penetration DoS
"""

from typing import TYPE_CHECKING

from cachetools import TTLCache
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from src.app.core.config import settings
from src.app.core.db.database import async_engine, local_session
from src.app.core.db.rls import RLS_TENANT_VAR, RLS_USER_VAR
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.tenancy.context import clear_tenant_context, set_tenant_context
from src.app.modules.tenants.models import Tenant

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# L1 In-Memory Cache for tenant resolution (R2-HYBRID MED-1)
# - maxsize: 1000 tenants (covers most deployments)
# - ttl: 5 seconds (balance between freshness and performance)
# - Thread-safe: cachetools TTLCache is safe for async context
# - Stores: dict with {id, slug, is_active} or None for negative cache
_L1_TENANT_CACHE: TTLCache[str, dict | None] = TTLCache(maxsize=1000, ttl=5.0)

TENANT_EXEMPT_PATHS: frozenset[str] = frozenset(
    [
        "/docs",
        "/redoc",
        "/openapi.json",
        "/api/v1/health",
        "/api/v1/ready",
        "/api/v1/app_configuration",  # Public frontend configuration
    ]
)

TENANT_EXEMPT_PREFIXES: tuple[str, ...] = ("/api/v1/tenants",)

TENANT_HEADER = "X-Tenant-ID"


def is_path_exempt(path: str) -> bool:
    """Check if the request path is exempt from tenant validation.

    Args:
        path: The request path to check.

    Returns
    -------
        True if the path is exempt, False otherwise.
    """
    if path in TENANT_EXEMPT_PATHS:
        return True
    for prefix in TENANT_EXEMPT_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


class TenantMiddleware(BaseHTTPMiddleware):
    """Middleware to validate X-Tenant-ID header and set tenant context.

    Validates tenant header on all non-exempt requests:
    - Missing header: 400 Bad Request
    - Invalid/inactive tenant: 403 Forbidden
    - Valid tenant: Sets context and proceeds
    """

    def __init__(self, app: FastAPI) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process the request and validate tenant header.

        Sets both application-level and database-level (RLS) tenant context.

        Args:
            request: The incoming request.
            call_next: The next middleware or route handler.

        Returns
        -------
            The response object.
        """
        path = request.url.path

        if is_path_exempt(path) or request.method == "OPTIONS":
            return await call_next(request)

        # Acquire a connection from the pool for this request
        async with async_engine.connect() as connection:
            try:
                tenant_slug = request.headers.get(TENANT_HEADER)

                if not tenant_slug or not tenant_slug.strip():
                    logger.warning(
                        "Tenant resolution failed: missing_header | path=%s | method=%s | client=%s",
                        path,
                        request.method,
                        request.client.host if request.client else "unknown",
                    )
                    return JSONResponse(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        content={"detail": f"Missing required header: {TENANT_HEADER}"},
                    )

                # Normalize slug to ensure case-insensitive lookup
                tenant_slug = tenant_slug.strip().lower()

                tenant = await self._get_tenant_by_slug(tenant_slug)

                if tenant is None:
                    logger.warning(
                        "Tenant resolution failed: invalid_slug | slug=%s | path=%s | method=%s | client=%s",
                        tenant_slug,
                        path,
                        request.method,
                        request.client.host if request.client else "unknown",
                    )
                    return JSONResponse(
                        status_code=status.HTTP_403_FORBIDDEN,
                        content={"detail": "Access denied"},
                    )

                if not tenant.is_active:
                    logger.warning(
                        "Tenant resolution failed: inactive_tenant | slug=%s | "
                        "tenant_id=%s | path=%s | method=%s | client=%s",
                        tenant_slug,
                        tenant.id,
                        path,
                        request.method,
                        request.client.host if request.client else "unknown",
                    )
                    return JSONResponse(
                        status_code=status.HTTP_403_FORBIDDEN,
                        content={"detail": "Tenant inactive. Contact support."},
                    )

                # Set application-level tenant context
                set_tenant_context(tenant)

                # Set PostgreSQL RLS session variables on this connection
                await connection.execute(
                    text(f"SET LOCAL {RLS_TENANT_VAR} = :tenant_id"),
                    {"tenant_id": str(tenant.id)},
                )
                logger.debug(
                    "Tenant resolved successfully | slug=%s | tenant_id=%s | path=%s | rls_context=set",
                    tenant_slug,
                    tenant.id,
                    path,
                )

                # Store connection in request state for use by handlers
                request.state.rls_connection = connection

                response = await call_next(request)
                return response

            finally:
                # Clear RLS context before returning connection to pool
                try:
                    await connection.execute(text(f"RESET {RLS_TENANT_VAR}"))
                    await connection.execute(text(f"RESET {RLS_USER_VAR}"))
                    logger.debug("RLS context cleared")
                except Exception as e:
                    logger.warning("Failed to clear RLS context: %s", e)

                # Clear application-level tenant context
                clear_tenant_context()

    async def _get_tenant_by_slug(self, slug: str) -> Tenant | None:
        """Look up tenant by slug with L1+L2 caching.

        Cache hierarchy (R2-HYBRID MED-1):
        - L1: In-memory TTLCache (5s TTL) - ~0.5-1ms savings per hit
        - L2: Redis cache (1hr TTL) - ~2-5ms savings per hit
        - L3: PostgreSQL database

        Args:
            slug: The tenant slug to look up.

        Returns
        -------
            The tenant if found, None otherwise.
        """
        # Security: Prevent DoS/large payload attacks
        if len(slug) > 64:
            logger.debug("Tenant resolution failed: slug_too_long | length=%d", len(slug))
            return None

        # L1: Check in-memory cache first (fastest path)
        try:
            cached_data = _L1_TENANT_CACHE.get(slug)
            if cached_data is not None:
                # L1 hit - reconstruct tenant from cached data
                logger.debug("Tenant L1 cache hit | slug=%s", slug)
                return await self._get_tenant_from_cache_data(cached_data)
            elif slug in _L1_TENANT_CACHE:
                # L1 negative cache hit (value is None, but key exists)
                logger.debug("Tenant L1 negative cache hit | slug=%s", slug)
                return None
        except Exception as e:
            logger.warning("Tenant L1 cache read error: %s", e)

        from src.app.core.utils.cache import client as redis_client

        cache_key = f"tenant:slug:{slug}"

        # L2: Try Redis cache
        if redis_client:
            try:
                cached = await redis_client.get(cache_key)
                if cached:
                    # Deserialize cached tenant data
                    data = json_loads(cached)

                    # Handle negative cache
                    if data is None:
                        logger.debug("Tenant L2 negative cache hit | slug=%s", slug)
                        # Populate L1 negative cache
                        _L1_TENANT_CACHE[slug] = None
                        return None

                    logger.debug("Tenant L2 cache hit | slug=%s", slug)
                    # Populate L1 cache
                    _L1_TENANT_CACHE[slug] = data
                    # Reconstruct minimal Tenant object for validation
                    return await self._get_tenant_from_cache_data(data)
            except Exception as e:
                logger.warning("Tenant L2 cache read error: %s", e)

        # L3: Cache miss - query DB
        async with local_session() as session:
            stmt = select(Tenant).where(Tenant.slug == slug)
            result = await session.execute(stmt)
            tenant = result.scalar_one_or_none()

            # Prepare cache data
            cache_data: dict | None = None
            if tenant:
                cache_data = {
                    "id": tenant.id,
                    "slug": tenant.slug,
                    "is_active": tenant.is_active,
                }

            # Populate L1 cache (always, even on DB miss for negative caching)
            try:
                _L1_TENANT_CACHE[slug] = cache_data
            except Exception as e:
                logger.warning("Tenant L1 cache write error: %s", e)

            # Populate L2 Redis cache
            if redis_client:
                try:
                    if tenant:
                        # Cache Success (1 hour)
                        # OPT-7: Use bytes directly (skip as_str=True decode)
                        serialized = json_dumps(cache_data)
                        await redis_client.setex(cache_key, 3600, serialized)
                        logger.debug("Tenant L2 cached | slug=%s | tenant_id=%s", slug, tenant.id)
                    else:
                        # Cache Failure (Negative Cache)
                        # Prevent Cache Penetration DoS
                        ttl = settings.TENANT_NEGATIVE_CACHE_TTL
                        await redis_client.setex(cache_key, ttl, "null")
                        logger.debug("Tenant L2 negative cache set | slug=%s | ttl=%s", slug, ttl)

                except Exception as e:
                    logger.warning("Tenant L2 cache write error: %s", e)

            return tenant

    async def _get_tenant_from_cache_data(self, data: dict) -> Tenant | None:
        """Create a minimal Tenant object from cached data for validation.

        Args:
            data: Cached tenant data dict with id, slug, is_active.

        Returns
        -------
            Tenant object with cached fields populated.
        """
        # For middleware validation, we only need id, slug, and is_active
        # Create minimal Tenant without triggering SQLAlchemy state tracking
        tenant = Tenant(
            name=data.get("name", "cached"),  # Name required by model
            slug=data["slug"],
            is_active=data["is_active"],
        )
        # Override id after creation (since init=False on id field)
        object.__setattr__(tenant, "id", data["id"])
        return tenant
