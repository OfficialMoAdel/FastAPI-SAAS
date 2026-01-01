from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

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


@asynccontextmanager
async def lifespan_with_admin(app: FastAPI) -> AsyncGenerator[None, None]:
    """Custom lifespan that includes admin initialization and pool metrics."""
    # Initialize structured logging first
    from .core.logger import setup_logging

    setup_logging()

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

        # Initialize session manager for concurrent session limiting
        from .core.security.session_manager import session_manager

        if redis_client:
            session_manager.set_redis_client(redis_client)

        # Initialize admin interface if it exists
        if admin:
            # Initialize admin database and setup
            await admin.initialize()

        yield


app = create_application(router=router, settings=settings, lifespan=lifespan_with_admin)

# Add tenant middleware for multi-tenancy support
app.add_middleware(TenantMiddleware)


# Request ID must be first (outermost) for tracing
app.add_middleware(RequestIDMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestBodyLimitMiddleware, max_body_size=10 * 1024 * 1024)  # 10MB limit
app.add_middleware(SecurityHeadersMiddleware)  # XSS prevention headers

# GZip compression for responses > 500 bytes
from fastapi.middleware.gzip import GZipMiddleware

app.add_middleware(GZipMiddleware, minimum_size=500)

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
            "description": "JWT access token from /api/v1/auth/login",
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
