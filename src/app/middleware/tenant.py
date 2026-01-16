"""Tenant validation middleware for multi-tenant request handling.

This middleware:
- Validates X-Tenant-ID header on all requests except whitelisted paths
- Looks up tenant in database and validates it's active
- Sets tenant context for downstream request processing
- Clears context in finally block to prevent leakage
"""

from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from src.app.core.config import settings
from src.app.core.db.database import local_session
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.tenancy.context import clear_tenant_context, set_tenant_context
from src.app.modules.tenants.models import Tenant

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

TENANT_EXEMPT_PATHS: frozenset[str] = frozenset(
    [
        "/docs",
        "/redoc",
        "/openapi.json",
        "/api/v1/health",
        "/api/v1/ready",
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

        Args:
            request: The incoming request.
            call_next: The next middleware or route handler.

        Returns
        -------
            The response object.
        """
        path = request.url.path

        if is_path_exempt(path):
            return await call_next(request)

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

            set_tenant_context(tenant)
            logger.debug(
                "Tenant resolved successfully | slug=%s | tenant_id=%s | path=%s",
                tenant_slug,
                tenant.id,
                path,
            )

            response = await call_next(request)
            return response

        finally:
            clear_tenant_context()

    async def _get_tenant_by_slug(self, slug: str) -> Tenant | None:
        """Look up tenant by slug with Redis caching (1-hour TTL).

        Uses Redis cache to avoid DB query on every request.
        Cache key: tenant:slug:{slug}

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

        from src.app.core.utils.cache import client as redis_client

        cache_key = f"tenant:slug:{slug}"

        # Try Redis cache first
        if redis_client:
            try:
                cached = await redis_client.get(cache_key)
                if cached:
                    # Deserialize cached tenant data
                    data = json_loads(cached)

                    # Handle negative cache
                    if data is None:
                        logger.debug("Tenant negative cache hit | slug=%s", slug)
                        return None

                    logger.debug("Tenant cache hit | slug=%s", slug)
                    # Reconstruct minimal Tenant object for validation
                    return await self._get_tenant_from_cache_data(data)
            except Exception as e:
                logger.warning("Tenant cache read error: %s", e)

        # Cache miss - query DB
        async with local_session() as session:
            session: AsyncSession
            stmt = select(Tenant).where(Tenant.slug == slug)
            result = await session.execute(stmt)
            tenant = result.scalar_one_or_none()

            if redis_client:
                try:
                    if tenant:
                        # Cache Success (1 hour)
                        cache_data = json_dumps(
                            {
                                "id": tenant.id,
                                "slug": tenant.slug,
                                "is_active": tenant.is_active,
                            },
                            as_str=True,
                        )
                        await redis_client.setex(cache_key, 3600, cache_data)
                        logger.debug("Tenant cached | slug=%s | tenant_id=%s", slug, tenant.id)
                    else:
                        # Cache Failure (Negative Cache)
                        # Prevent Cache Penetration DoS
                        ttl = settings.TENANT_NEGATIVE_CACHE_TTL
                        await redis_client.setex(cache_key, ttl, "null")
                        logger.debug("Tenant negative cache set | slug=%s | ttl=%s", slug, ttl)

                except Exception as e:
                    logger.warning("Tenant cache write error: %s", e)

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
        # Create a detached object (not bound to session)
        tenant = Tenant.__new__(Tenant)
        object.__setattr__(tenant, "id", data["id"])
        object.__setattr__(tenant, "slug", data["slug"])
        object.__setattr__(tenant, "is_active", data["is_active"])
        return tenant
