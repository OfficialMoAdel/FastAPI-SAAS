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

from src.app.api.deps import get_current_superuser
from src.app.core.config import (
    EnvironmentOption,
    Settings,
)
from src.app.core.db.database import Base, get_async_engine
from src.app.core.logger import get_logger
from src.app.core.redis_provider import close_redis_pool, get_redis_pool
from src.app.core.utils import cache, queue

logger = get_logger(__name__)

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
    """Initialize Redis cache pool and client via the Redis provider."""
    # Cache client needs string decoding; the shared pool is raw (bytes),
    # but the client overrides decoding.
    if cache.client is None or cache.pool is None:
        pool = await get_redis_pool()
        cache.pool = pool
        cache.client = redis.Redis(connection_pool=pool, decode_responses=True)


async def close_redis_cache_pool() -> None:
    """Close the shared Redis cache client and clear its global slots."""
    import src.app.core.utils.cache as _cache

    if _cache.client is not None:
        # Use aclose() for proper async cleanup if available (redis-py 5.0+)
        if hasattr(_cache.client, "aclose"):
            await _cache.client.aclose()
        else:
            await _cache.client.close()
        _cache.client = None
    _cache.pool = None


# -------------- queue --------------
async def create_redis_queue_pool() -> None:
    """Initialize ARQ queue pool (reuses the shared connection pool)."""
    # K1 FIX: use unified ARQ wire serializer adapter (Candidate 5)
    from src.app.core.events.arq_adapter import get_arq_pool_kwargs

    if queue.pool is None:
        pool = await get_redis_pool()
        queue.pool = ArqRedis(
            connection_pool=pool,
            **get_arq_pool_kwargs(),
        )


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

        # P3-5: ``initialization_complete`` signals "startup teardown is
        # allowed", not "caches are warm". The cache warmers run as a
        # background task (line 176) and complete asynchronously. Anything
        # that gates on this event expecting a warm cache is a bug — the
        # DB and Redis pools ARE ready at this point, but the warmer
        # results are not. The runtime dependency at api/deps/core.py:31
        # uses this for a different reason: rate_limiter_dependency waits
        # so concurrent requests don't all try to seed tier limits
        # simultaneously during burst startup.
        initialization_complete = Event()
        app.state.initialization_complete = initialization_complete

        await set_threadpool_tokens()

        # S1 FIX: initialize flags BEFORE the try block. If a fallible create
        # call below raises, the finally block references these names; assigning
        # them inside the try let an early failure raise NameError and mask the
        # original startup exception.
        redis_cache_enabled = settings.redis_cache.REDIS_CACHE_ENABLED
        redis_queue_enabled = settings.redis_queue.REDIS_QUEUE_ENABLED

        try:
            if redis_cache_enabled:
                await create_redis_cache_pool()

            if redis_queue_enabled:
                await create_redis_queue_pool()

            if create_tables_on_start:
                await create_tables()

            # Explicit user-domain security lookup wiring (deterministic;
            # backstops the import-time registration in users/__init__.py).
            from src.app.modules.users import register_users_security_lookups

            register_users_security_lookups()

            # Warm caches after DB and Redis are ready

            from src.app.core.cache.warmer import warm_caches

            async def _warm_caches_background() -> None:
                from src.app.core.logger import get_logger

                logger = get_logger(__name__)
                try:
                    if cache.client:
                        # Distributed lock to prevent all workers from hitting DB simultaneously
                        lock_key = "system:startup:cache_warmup_lock"
                        acquired = await cache.client.set(lock_key, "1", nx=True, ex=300)
                        if acquired:
                            logger.info("Acquired startup cache warming lock, starting warmup...")
                            await warm_caches()
                            logger.info("Startup cache warmup complete.")
                        else:
                            logger.debug("Another worker is handling cache warmup, skipping.")
                    else:
                        await warm_caches()
                except Exception as e:
                    logger.warning("Cache warming failed: %s", e)

            # Fire and forget with safe error handling and hard reference
            from src.app.core.utils.background_task import create_safe_task

            create_safe_task(_warm_caches_background(), name="cache_warming_startup")

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
            except Exception as exc:  # noqa: BLE001
                # R1-17: engine may not exist if startup failed — but a real
                # dispose failure must be visible, not swallowed.
                logger.warning("engine_dispose_failed", error=str(exc))
            db_module._async_engine = None

            if redis_cache_enabled:
                await close_redis_cache_pool()

            if redis_queue_enabled:
                await close_redis_queue_pool()

            # Close the shared Redis connection pool AFTER clients are closed
            await close_redis_pool()

            # Close SSO HTTP client pool
            from src.app.modules.auth.http_client import close_sso_http_client

            await close_sso_http_client()

            # Close Webhook HTTP client pool
            from src.app.modules.webhooks.http_client import close_webhook_http_client

            await close_webhook_http_client()

            # Close AI providers
            from src.app.modules.ai.factory import AIProviderFactory

            await AIProviderFactory.shutdown_all()

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
    app_name = settings.app.APP_NAME
    app_description = settings.app.APP_DESCRIPTION
    contact_name = settings.app.CONTACT_NAME
    contact_email = settings.app.CONTACT_EMAIL
    license_name = settings.app.LICENSE_NAME

    to_update = {
        "title": app_name,
        "description": app_description,
        "contact": {"name": contact_name, "email": contact_email},
        "license_info": {"name": license_name},
    }
    kwargs.update(to_update)

    # Environment-specific docs control
    environment = settings.env.ENVIRONMENT
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

    cors_origins = settings.cors.CORS_ORIGINS
    if cors_origins:
        allow_credentials = True
        if "*" in cors_origins:
            allow_credentials = False

        application.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=allow_credentials,
            allow_methods=settings.cors.CORS_METHODS,
            allow_headers=settings.cors.CORS_HEADERS,
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
