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
    AppSettings,
    ClientSideCacheSettings,
    CORSSettings,
    DatabaseSettings,
    EnvironmentOption,
    EnvironmentSettings,
    RedisCacheSettings,
    RedisQueueSettings,
)
from src.app.core.db.database import Base
from src.app.core.db.database import async_engine as engine
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
    """Close Redis cache client and pool."""
    if cache.client is not None:
        # Use aclose() for proper async cleanup if available (redis-py 5.0+)
        if hasattr(cache.client, "aclose"):
            await cache.client.aclose()
        else:
            await cache.client.close()
        cache.client = None

    # We close the shared pool separately via factory
    # This ensures all clients using the pool are shut down before the pool itself
    await redis_factory.close_redis_pool()
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


# -------------- rate limit --------------
# Rate limiter now uses the shared Redis cache pool
# See src.app.core.utils.rate_limit.RateLimiter


# -------------- application --------------
async def set_threadpool_tokens(number_of_tokens: int = 100) -> None:
    limiter = anyio.to_thread.current_default_thread_limiter()
    limiter.total_tokens = number_of_tokens


def lifespan_factory(
    settings: (
        DatabaseSettings
        | RedisCacheSettings
        | AppSettings
        | ClientSideCacheSettings
        | CORSSettings
        | RedisQueueSettings
        | EnvironmentSettings
    ),
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
            if isinstance(settings, RedisCacheSettings) and settings.REDIS_CACHE_ENABLED:
                await create_redis_cache_pool()

            if isinstance(settings, RedisQueueSettings) and settings.REDIS_QUEUE_ENABLED:
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
            # Dispose database engine to release connection pool
            await engine.dispose()

            if isinstance(settings, RedisCacheSettings):
                await close_redis_cache_pool()

            if isinstance(settings, RedisQueueSettings):
                await close_redis_queue_pool()

            # Close SSO HTTP client pool
            from src.app.modules.sso.http_client import close_sso_http_client

            await close_sso_http_client()

    return lifespan


# -------------- application --------------
def create_application(
    router: APIRouter,
    settings: (
        DatabaseSettings
        | RedisCacheSettings
        | AppSettings
        | ClientSideCacheSettings
        | CORSSettings
        | RedisQueueSettings
        | EnvironmentSettings
    ),
    create_tables_on_start: bool = True,
    lifespan: Callable[[FastAPI], _AsyncGeneratorContextManager[Any]] | None = None,
    **kwargs: Any,
) -> FastAPI:
    """Creates and configures a FastAPI application based on the provided settings.

    This function initializes a FastAPI application and configures it with various settings
    and handlers based on the type of the `settings` object provided.

    Parameters
    ----------
    router : APIRouter
        The APIRouter object containing the routes to be included in the FastAPI application.

    settings
        An instance representing the settings for configuring the FastAPI application.
        It determines the configuration applied:

        - AppSettings: Configures basic app metadata like name, description, contact, and license info.
        - DatabaseSettings: Adds event handlers for initializing database tables during startup.
        - RedisCacheSettings: Sets up event handlers for creating and closing a Redis cache pool.
        - ClientSideCacheSettings: Integrates middleware for client-side caching.
        - CORSSettings: Integrates CORS middleware with specified origins.
        - RedisQueueSettings: Sets up event handlers for creating and closing a Redis queue pool.
        - EnvironmentSettings: Conditionally sets documentation URLs and integrates custom routes for API documentation
          based on the environment type.

    create_tables_on_start : bool
        A flag to indicate whether to create database tables on application startup.
        Defaults to True.

    **kwargs
        Additional keyword arguments passed directly to the FastAPI constructor.

    Returns
    -------
    FastAPI
        A fully configured FastAPI application instance.

    The function configures the FastAPI application with different features and behaviors
    based on the provided settings. It includes setting up database connections, Redis pools
    for caching, queue, and rate limiting, client-side caching, and customizing the API documentation
    based on the environment settings.
    """
    # --- before creating application ---
    if isinstance(settings, AppSettings):
        to_update = {
            "title": settings.APP_NAME,
            "description": settings.APP_DESCRIPTION,
            "contact": {"name": settings.CONTACT_NAME, "email": settings.CONTACT_EMAIL},
            "license_info": {"name": settings.LICENSE_NAME},
        }
        kwargs.update(to_update)

    if isinstance(settings, EnvironmentSettings):
        kwargs.update({"docs_url": None, "redoc_url": None, "openapi_url": None})

    # Use custom lifespan if provided, otherwise use default factory
    if lifespan is None:
        lifespan = lifespan_factory(settings, create_tables_on_start=create_tables_on_start)

    application = FastAPI(lifespan=lifespan, **kwargs)
    application.include_router(router)

    if isinstance(settings, ClientSideCacheSettings):
        application.add_middleware(ClientCacheMiddleware, max_age=settings.CLIENT_CACHE_MAX_AGE)

    if isinstance(settings, CORSSettings):
        allow_credentials = True
        if "*" in settings.CORS_ORIGINS:
            allow_credentials = False

        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.CORS_ORIGINS,
            allow_credentials=allow_credentials,
            allow_methods=settings.CORS_METHODS,
            allow_headers=settings.CORS_HEADERS,
        )

    if isinstance(settings, EnvironmentSettings):
        if settings.ENVIRONMENT != EnvironmentOption.PRODUCTION:
            docs_router = APIRouter()
            if settings.ENVIRONMENT != EnvironmentOption.LOCAL:
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
