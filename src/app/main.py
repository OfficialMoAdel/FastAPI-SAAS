"""FastAPI SaaS application entry point."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.responses import ORJSONResponse

from .admin.initialize import create_admin_interface
from .api import router
from .core.cache.warmer import warmer
from .core.config import settings
from .core.setup import create_application, lifespan_factory
from .middleware.attack_detection import AttackDetectionMiddleware
from .middleware.body_limit import RequestBodyLimitMiddleware
from .middleware.ip_filter import IPFilterMiddleware
from .middleware.rate_limit import RateLimitMiddleware
from .middleware.request_id import RequestIDMiddleware
from .middleware.security_headers import SecurityHeadersMiddleware
from .middleware.tenant import TenantMiddleware

admin = create_admin_interface()
logger = logging.getLogger(__name__)

CACHE_WARMING_ENABLED = True
CACHE_WARMING_DELAY = timedelta(seconds=2)


async def _warm_token_blacklist_cache() -> None:
    """Warm the token blacklist Redis cache from database on startup."""
    from .core.db.crud_token_blacklist import crud_token_blacklist
    from .core.db.database import async_session_maker
    from .core.security.token_blacklist_cache import warm_cache_from_db

    try:
        async with async_session_maker() as db:
            tokens = await crud_token_blacklist.get_active_tokens(db)
            if tokens:
                count = await warm_cache_from_db(tokens)
                logger.info(f"Token blacklist cache warmed with {count} tokens")
    except Exception as e:
        logger.warning(f"Failed to warm token blacklist cache: {e}")


async def _warm_application_caches() -> None:
    """Warm application caches on startup.

    Uses CacheWarmer registry to warm all registered caches including:
    - Feature flags
    - Tiers
    - SSO settings
    """
    if not CACHE_WARMING_ENABLED:
        logger.info("Cache warming is disabled")
        return

    from .core.db.database import async_session_maker

    try:
        async with async_session_maker() as session:
            logger.info(f"Starting cache warming with {len(warmer.list_warmers())} warmers")
            result = await warmer.warm_all(session)

            if result.errors:
                logger.warning(f"Cache warming completed with errors: {result.errors}")
            else:
                logger.info(f"Cache warming successful: {result.tenants_cached} tenants, {result.tiers_cached} tiers")
    except Exception as e:
        logger.error(f"Failed to warm application caches: {e}")


@asynccontextmanager
async def lifespan_with_admin(app: FastAPI) -> AsyncGenerator[None, None]:
    """Custom lifespan that includes admin initialization and cache warming."""
    from .core.logger import setup_logging

    setup_logging()

    from .core.env_validator import EnvironmentValidator

    if not EnvironmentValidator.validate_and_log():
        if settings.ENVIRONMENT == "production":
            raise RuntimeError("Environment validation failed - refusing to start in production")

    default_lifespan = lifespan_factory(settings, create_tables_on_start=settings.CREATE_TABLES_ON_STARTUP)

    async with default_lifespan(app):
        from .core.db.database import async_engine
        from .core.utils.pool_metrics import setup_pool_metrics

        try:
            setup_pool_metrics(async_engine)
        except Exception as e:
            logger.warning("Pool metrics setup failed: %s", e)

        from .core.events import event_bus
        from .core.utils.cache import client as redis_client

        if redis_client:
            event_bus.set_redis_client(redis_client)

        # Pre-register event handlers to avoid cold start penalties
        # This scans modules and registers @event_bus.subscribe decorated functions
        event_bus.register_all_handlers()

        # Initialize session manager for concurrent session limiting
        from .core.security.session_manager import session_manager

        if redis_client:
            session_manager.set_redis_client(redis_client)

        # Warm token blacklist cache from database
        await _warm_token_blacklist_cache()

        # Warm application caches
        logger.info("Starting application cache warming...")
        await _warm_application_caches()

        if CACHE_WARMING_DELAY.total_seconds() > 0:
            logger.info(f"Cache warming completed, sleeping {CACHE_WARMING_DELAY.total_seconds()}s before ready")
            import asyncio

            await asyncio.sleep(CACHE_WARMING_DELAY.total_seconds())

        # Initialize admin interface if it exists
        if admin:
            # Initialize admin database and setup
            await admin.initialize()

        yield


app = create_application(
    router=router,
    settings=settings,
    lifespan=lifespan_with_admin,
    default_response_class=ORJSONResponse,
)

# Middleware stack (LIFO - last added runs first on request)
# Request flow:  GZip → IPFilter → AttackDetection → Security → BodyLimit → Tenant → RateLimit → RequestID → Handler
# Response flow: Handler → RequestID → RateLimit → Tenant → BodyLimit → Security → AttackDetection → IPFilter → GZip
#
# Key ordering decisions:
# - RequestID: Added first = innermost (closest to handler for accurate tracing)
# - RateLimit: Rate limiting before expensive processing
# - Tenant: Extract tenant context for multi-tenant features
# - BodyLimit: Reject oversized requests early
# - Security: Add security headers to all responses
# - AttackDetection: Detect attacks early, after IPFilter to allow whitelist bypass
# - IPFilter: Block unwanted IPs early, sets ip_whitelisted state for RateLimit
# - GZip: Added last = outermost (compresses ALL responses including 4xx/5xx errors)

from fastapi.middleware.gzip import GZipMiddleware

app.add_middleware(RequestIDMiddleware)  # Innermost - must be first for accurate tracing
app.add_middleware(RateLimitMiddleware)  # User/tier-based rate limiting
app.add_middleware(TenantMiddleware)  # Tenant context for multi-tenant features
app.add_middleware(RequestBodyLimitMiddleware, max_body_size=10 * 1024 * 1024)  # 10MB limit
app.add_middleware(SecurityHeadersMiddleware)  # XSS prevention headers
app.add_middleware(AttackDetectionMiddleware)  # Detect SQLi, XSS, path traversal
app.add_middleware(IPFilterMiddleware)  # Block unwanted IPs, sets ip_whitelisted state
app.add_middleware(GZipMiddleware, minimum_size=500)  # Outermost - compresses all responses

# Mount admin interface if enabled
if admin:
    try:
        if hasattr(settings, "ADMIN_ENABLE") and settings.ADMIN_ENABLE:
            app.mount("/admin", admin)  # type: ignore[arg-type]
    except Exception as e:
        logger.warning(f"Failed to mount admin interface: {e}")


# OpenAPI custom schema
def custom_openapi():
    """Generate custom OpenAPI schema."""
    if app.openapi_schema:
        return app.openapi_schema

    project_name = getattr(settings, "PROJECT_NAME", "FastAPI SaaS")
    version = getattr(settings, "VERSION", "1.0.0")
    description = getattr(settings, "API_DESCRIPTION", "FastAPI SaaS Boilerplate")

    openapi_schema = get_openapi(
        title=project_name,
        version=version,
        description=description,
        routes=app.routes,
    )

    # Add JWT Bearer security scheme
    openapi_schema["components"]["securitySchemes"] = {
        "bearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Enter JWT token obtained from /api/v1/auth/login",
        }
    }

    # Apply security to protected routes
    for path in openapi_schema["paths"].values():
        for method in path.values():
            if method.get("security"):
                continue
            # Apply JWT auth to all non-public routes
            if path.get("/api/v1/auth", "").startswith("/auth"):
                continue
            method.setdefault("security", []).append({"bearerAuth": []})

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi  # type: ignore[method-assign]
