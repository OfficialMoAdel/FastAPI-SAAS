from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.responses import ORJSONResponse

from .admin.initialize import create_admin_interface
from .api import router
from .core.config import settings
from .core.setup import create_application, lifespan_factory
from .middleware.body_limit import RequestBodyLimitMiddleware
from .middleware.rate_limit import RateLimitMiddleware
from .middleware.request_id import RequestIDMiddleware
from .middleware.security_headers import SecurityHeadersMiddleware
from .middleware.tenant import TenantMiddleware

admin = create_admin_interface()


async def _warm_token_blacklist_cache() -> None:
    """Warm the token blacklist Redis cache from database on startup."""
    import logging

    from .core.db.crud_token_blacklist import crud_token_blacklist
    from .core.db.database import async_session_maker
    from .core.security.token_blacklist_cache import warm_cache_from_db

    logger = logging.getLogger(__name__)

    try:
        async with async_session_maker() as db:
            # Get all non-expired blacklisted tokens
            tokens = await crud_token_blacklist.get_active_tokens(db)
            if tokens:
                count = await warm_cache_from_db(tokens)
                logger.info(f"Token blacklist cache warmed with {count} tokens")
    except Exception as e:
        logger.warning(f"Failed to warm token blacklist cache: {e}")


@asynccontextmanager
async def lifespan_with_admin(app: FastAPI) -> AsyncGenerator[None, None]:
    """Custom lifespan that includes admin initialization and pool metrics."""
    # Initialize structured logging first
    from .core.logger import setup_logging

    setup_logging()

    # Environment Validation
    from .core.env_validator import EnvironmentValidator

    # Validate environment (optional - can block on errors)
    if not EnvironmentValidator.validate_and_log():
        if settings.ENVIRONMENT == "production":
            raise RuntimeError("Environment validation failed - refusing to start in production")

    # Get the default lifespan
    default_lifespan = lifespan_factory(settings, create_tables_on_start=settings.CREATE_TABLES_ON_STARTUP)

    # Run the default lifespan initialization and our admin initialization
    async with default_lifespan(app):
        # Initialize pool metrics for observability
        from .core.db.database import async_engine
        from .core.utils.pool_metrics import setup_pool_metrics

        try:
            setup_pool_metrics(async_engine)
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning("Pool metrics setup failed: %s", e)

        # Initialize event bus Redis client for DLQ persistence
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
# Request flow:  GZip → Security → BodyLimit → Tenant → RateLimit → RequestID → Handler
# Response flow: Handler → RequestID → RateLimit → Tenant → BodyLimit → Security → GZip
#
# Key ordering decisions:
# - RequestID: Added first = innermost (closest to handler for accurate tracing)
# - RateLimit: Rate limiting before expensive processing
# - Tenant: Extract tenant context for multi-tenant features
# - BodyLimit: Reject oversized requests early
# - Security: Add security headers to all responses
# - GZip: Added last = outermost (compresses ALL responses including 4xx/5xx errors)

from fastapi.middleware.gzip import GZipMiddleware

app.add_middleware(RequestIDMiddleware)  # Innermost - must be first for accurate tracing
app.add_middleware(RateLimitMiddleware)  # User/tier-based rate limiting
app.add_middleware(TenantMiddleware)  # Tenant context for multi-tenant features
app.add_middleware(RequestBodyLimitMiddleware, max_body_size=10 * 1024 * 1024)  # 10MB limit
app.add_middleware(SecurityHeadersMiddleware)  # XSS prevention headers
app.add_middleware(GZipMiddleware, minimum_size=500)  # Outermost - compresses all responses

# Mount admin interface if enabled
if admin:
    app.mount(settings.CRUD_ADMIN_MOUNT_PATH, admin.app)


# OpenAPI security schemes
def custom_openapi():
    """Custom OpenAPI schema with security definitions."""
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    # Add security schemes
    openapi_schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "JWT access token from /api/v1/account/login",
        },
        "APIKeyHeader": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "API key for programmatic access",
        },
    }

    # Apply default security to all endpoints
    openapi_schema["security"] = [{"BearerAuth": []}, {"APIKeyHeader": []}]

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi
