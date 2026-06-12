from collections.abc import AsyncGenerator, Callable
from contextlib import _AsyncGeneratorContextManager, asynccontextmanager
from typing import Any

import anyio
import fastapi
import redis.asyncio as redis
from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi

from src.app.api.dependencies import get_current_superuser
from src.app.core import redis_factory
from src.app.core.config import (
    EnvironmentOption,
    Settings,
)
from src.app.core.db.database import Base, get_async_engine
from src.app.core.utils import cache, queue
from src.app.middleware.client_cache_middleware import ClientCacheMiddleware

# Import all models to register them with SQLAlchemy's Base.metadata
# This ensures create_all() creates all tables
from src.app.models import (  # noqa: F401
    AuditLog,
    EmailTemplateOverride,
    FeatureFlagOverride,
    Notification,
    Permission,
    Post,
    RateLimit,
    Role,
    RolePermission,
    Tenant,
    Tier,
    User,
    UserRole,
    WebhookDelivery,
    WebhookEndpoint,
)


# -------------- database --------------
async def create_tables() -> None:
    engine = get_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# -------------- cache --------------
async def create_redis_cache_pool() -> None:
    """Initialize Redis cache pool and client."""
    # P2-2: Use shared connection pool from factory
    if cache.pool is None:
        cache.pool = await redis_factory.get_redis_pool()

    # Cache client needs string decoding
    # Note: The shared pool is raw (bytes), but client overrides decoding
    # SECURITY FIX: Prevent redundant re-instantiation to avoid connection leaks
    if cache.client is None:
        cache.client = redis.Redis(connection_pool=cache.pool, decode_responses=True)


async def close_redis_cache_pool() -> None:
    """Close Redis cache client."""
    if cache.client is not None:
        # Use aclose() for proper async cleanup if available (redis-py 5.0+)
        if hasattr(cache.client, "aclose"):
            await cache.client.aclose()
        else:
            await cache.client.close()
        cache.client = None
    cache.pool = None


# -------------- queue --------------
async def create_redis_queue_pool() -> None:
    """Initialize ARQ queue pool."""
    # P2-2: Reuse the shared connection pool for ARQ
    pool = await redis_factory.get_redis_pool()

    # Create ArqRedis using the same pool
    # ARQ uses default decoding (bytes) which matches the pool
    # SECURITY FIX: Prevent redundant re-instantiation
    if queue.pool is None:
        queue.pool = ArqRedis(connection_pool=pool)


async def close_redis_queue_pool() -> None:
    """Close ARQ queue pool."""
    if queue.pool is not None:
        await queue.pool.close()
        queue.pool = None


# -------------- application --------------
async def set_threadpool_tokens(number_of_tokens: int = 100) -> None:
    limiter = anyio.to_thread.current_default_thread_limiter()
    limiter.total_tokens = number_of_tokens


def lifespan_factory(
    settings: Settings | Any,
    create_tables_on_start: bool = True,
) -> Callable[[FastAPI], _AsyncGeneratorContextManager[Any]]:
    """Factory to create a lifespan async context manager for a FastAPI app."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator:
        from asyncio import Event

        initialization_complete = Event()
        app.state.initialization_complete = initialization_complete

        await set_threadpool_tokens()

        try:
            # R1-UNIFIED: Support both composition (Settings) and legacy mixins
            redis_cache_enabled = getattr(settings, "REDIS_CACHE_ENABLED", False)
            if redis_cache_enabled:
                await create_redis_cache_pool()

            redis_queue_enabled = getattr(settings, "REDIS_QUEUE_ENABLED", False)
            if redis_queue_enabled:
                await create_redis_queue_pool()

            if create_tables_on_start:
                await create_tables()

            # Warm caches after DB and Redis are ready
            from src.app.core.utils.cache_warming import warm_caches

            try:
                await warm_caches()
            except Exception as e:
                # Log but don't fail startup - caches will be populated on-demand
                from src.app.core.logger import get_logger

                get_logger(__name__).warning("Cache warming failed: %s", e)

            initialization_complete.set()

            yield

        finally:
            if hasattr(app.state, "initialization_complete"):
                delattr(app.state, "initialization_complete")

            # Dispose database engine to release connection pool
            import src.app.core.db.database as db_module

            try:
                engine = db_module.get_async_engine()
                await engine.dispose()
            except Exception:
                pass  # Engine may not have been created if startup failed
            db_module._async_engine = None

            if redis_cache_enabled:
                await close_redis_cache_pool()

            if redis_queue_enabled:
                await close_redis_queue_pool()

            # Close the shared Redis connection pool AFTER clients are closed
            await redis_factory.close_redis_pool()

            # Close SSO HTTP client pool
            from src.app.modules.sso.http_client import close_sso_http_client
            await close_sso_http_client()

            # Close Webhook HTTP client pool
            try:
                from src.app.modules.webhooks.http_client import close_webhook_http_client
                await close_webhook_http_client()
            except ImportError:
                pass

            # Close AI providers
            try:
                from src.app.modules.ai.factory import AIProviderFactory
                await AIProviderFactory.shutdown_all()
            except ImportError:
                pass

            try:
                from src.app.modules.ai.ollama_client import close_ollama_client
                await close_ollama_client()
            except ImportError:
                pass

    return lifespan


# -------------- application --------------
def create_application(
    router: APIRouter,
    settings: Settings | Any,
    create_tables_on_start: bool = True,
    lifespan: Callable[[FastAPI], _AsyncGeneratorContextManager[Any]] | None = None,
    **kwargs: Any,
) -> FastAPI:
    """Creates and configures a FastAPI application based on the provided settings."""

    # --- before creating application ---
    # R1-UNIFIED: Support both composition (Settings) and legacy mixins
    app_name = getattr(settings, "APP_NAME", "FastAPI app")
    app_description = getattr(settings, "APP_DESCRIPTION", None)
    contact_name = getattr(settings, "CONTACT_NAME", None)
    contact_email = getattr(settings, "CONTACT_EMAIL", None)
    license_name = getattr(settings, "LICENSE_NAME", None)

    to_update = {
        "title": app_name,
        "description": app_description,
        "contact": {"name": contact_name, "email": contact_email},
        "license_info": {"name": license_name},
    }
    kwargs.update(to_update)

    # Environment-specific docs control
    environment = getattr(settings, "ENVIRONMENT", EnvironmentOption.LOCAL)
    # If ENVIRONMENT is set, we might want to hide docs in production
    # but the legacy logic was checking for EnvironmentSettings instance.
    # We'll use a more direct check for production.
    if environment == EnvironmentOption.PRODUCTION:
        kwargs.update({"docs_url": None, "redoc_url": None, "openapi_url": None})

    # Use custom lifespan if provided, otherwise use default factory
    if lifespan is None:
        lifespan = lifespan_factory(settings, create_tables_on_start=create_tables_on_start)

    application = FastAPI(lifespan=lifespan, **kwargs)
    application.include_router(router)

    # Middleware integration via feature detection
    client_cache_max_age = getattr(settings, "CLIENT_CACHE_MAX_AGE", None)
    if client_cache_max_age is not None:
        application.add_middleware(ClientCacheMiddleware, max_age=client_cache_max_age)

    cors_origins = getattr(settings, "CORS_ORIGINS", None)
    if cors_origins:
        allow_credentials = True
        if "*" in cors_origins:
            allow_credentials = False

        application.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=allow_credentials,
            allow_methods=getattr(settings, "CORS_METHODS", ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]),
            allow_headers=getattr(
                settings,
                "CORS_HEADERS",
                [
                    "Content-Type",
                    "Authorization",
                    "X-Request-ID",
                    "X-Webhook-Signature",
                    "X-Webhook-Event",
                    "X-Webhook-ID",
                    "X-Webhook-Timestamp",
                    "X-Tenant-ID",
                ],
            ),
        )

    if environment != EnvironmentOption.PRODUCTION:
        docs_router = APIRouter()
        if environment != EnvironmentOption.LOCAL:
            docs_router = APIRouter(dependencies=[Depends(get_current_superuser)])

        @docs_router.get("/docs", include_in_schema=False)
        async def get_swagger_documentation() -> fastapi.responses.HTMLResponse:
            return get_swagger_ui_html(openapi_url="/openapi.json", title="docs")

        @docs_router.get("/redoc", include_in_schema=False)
        async def get_redoc_documentation() -> fastapi.responses.HTMLResponse:
            return get_redoc_html(openapi_url="/openapi.json", title="docs")

        @docs_router.get("/openapi.json", include_in_schema=False)
        async def openapi() -> dict[str, Any]:
            out: dict = get_openapi(title=application.title, version=application.version, routes=application.routes)
            return out

        application.include_router(docs_router)

    # Register standardized error handlers
    from src.app.core.errors import setup_exception_handlers

    setup_exception_handlers(application)

    return application
