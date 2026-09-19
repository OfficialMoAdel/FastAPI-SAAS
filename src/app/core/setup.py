import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import _AsyncGeneratorContextManager, asynccontextmanager
from typing import TYPE_CHECKING, Any

import anyio
import fastapi
from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from redis.exceptions import RedisError

from src.app.api.deps import get_current_superuser
from src.app.core.config import (
    EnvironmentOption,
    Settings,
    settings,
)
from src.app.core.db.database import Base, get_async_engine
from src.app.core.logger import get_logger
from src.app.core.redis_provider import close_redis_pool
from src.app.core.utils import cache, queue
from src.app.shared.exceptions import ServiceUnavailableError

if TYPE_CHECKING:
    # K10: annotation-only use — defer the crudadmin import out of module
    # import time. _init_admin() imports the real module at runtime.
    from crudadmin import CRUDAdmin

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


def _resolve_create_tables_on_start(settings_obj: Settings | Any, requested: bool) -> bool:
    """A-M4: default CREATE_TABLES_ON_STARTUP to False outside local.

    ``create_all`` coexists with Alembic migrations, and a table created by
    ``create_all`` is NOT covered by migration 08's hand-maintained
    ``TENANT_TABLES`` list — it silently lacks RLS policies and row-level
    tenant isolation. A non-local environment must therefore never default
    to creating tables at startup; only an explicit
    ``CREATE_TABLES_ON_STARTUP=true`` in settings.app opts in. Local keeps
    the legacy behavior (``requested`` — main.py passes the flag itself, so
    an explicit False is honored there) so the bootstrap workflow is
    unchanged.
    """
    environment = getattr(settings_obj.env, "ENVIRONMENT", EnvironmentOption.LOCAL)
    if environment == EnvironmentOption.LOCAL:
        return requested
    return bool(getattr(settings_obj.app, "CREATE_TABLES_ON_STARTUP", False)) and requested


# -------------- cache --------------
async def create_redis_cache_pool() -> None:
    """Initialize Redis cache pool and client via the Redis provider.

    Thin lifespan hook (P1-#3): construction lives in the provider —
    :func:`redis_provider.create_cache_client` is the single construction
    seam. Retained under this name because fixtures and tests patch it here.
    """
    from src.app.core.redis_provider import create_cache_client

    await create_cache_client()


async def close_redis_cache_pool() -> None:
    """Close the shared Redis cache client and clear its global slots.

    Thin lifespan hook (P1-#3): teardown lives in the provider; see
    :func:`redis_provider.shutdown`.
    """
    import src.app.core.utils.cache as _cache

    if _cache.client is not None:
        # Use aclose() for proper async cleanup if available (redis-py 5.0+)
        if hasattr(_cache.client, "aclose"):
            await _cache.client.aclose()  # type: ignore[attr-defined]
        else:
            await _cache.client.close()
        _cache.client = None
    _cache.pool = None


# -------------- queue --------------
async def create_redis_queue_pool() -> None:
    """Initialize ARQ queue pool (reuses the shared connection pool).

    Thin lifespan hook (P1-#3): construction lives in the provider —
    :func:`redis_provider.create_arq_pool` is the single construction seam
    (K1: unified ARQ wire serializers via arq_adapter.get_arq_pool_kwargs).
    """
    from src.app.core.redis_provider import create_arq_pool

    if queue.pool is None:
        queue.pool = await create_arq_pool()


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
    """Factory to create a lifespan async context manager for a FastAPI app.

    A-M4: ``create_tables_on_start`` is gated by
    ``_resolve_create_tables_on_start`` — outside local it defaults to off
    unless settings.app.CREATE_TABLES_ON_STARTUP explicitly opts in.
    """
    create_tables_on_start = _resolve_create_tables_on_start(settings, create_tables_on_start)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator:
        from asyncio import Event

        # P3-5: ``initialization_complete`` signals "startup teardown is
        # allowed", not "caches are warm". The cache warmers run as a
        # background task (line 176) and complete asynchronously. Anything
        # that gates on this event expecting a warm cache is a bug — the
        # DB and Redis pools ARE ready at this point, but the warmer
        # results are not. (Historical note: the deleted
        # api/deps/core.rate_limiter_dependency used to wait on this event so
        # concurrent requests didn't all try to seed tier limits during
        # burst startup; the ASGI rate-limit path does not wait — its cache
        # misses simply fall through to the next resolution tier.)
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

            # P1 FIX (code-review): fail fast when the JWT DIP seams are
            # unwired instead of surfacing as mass 401s at runtime. The
            # per-request path still fails closed; this is the loud gate.
            try:
                from src.app.core.security.jwt_service import ensure_jwt_lookups_registered

                ensure_jwt_lookups_registered()
            except Exception as e:  # noqa: BLE001 — startup must stay loud, not crash on log path
                from src.app.core.logger import get_logger as _gl

                _gl(__name__).error("jwt_lookups_startup_check_failed", error=str(e))
                raise

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
                except Exception as e:  # noqa: BLE001 - P1-17: background warming is best-effort by design
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

    # Use custom lifespan if provided, otherwise use default factory.
    # The factory applies the A-M4 gate: outside local, create_all at startup
    # requires an explicit settings.app.CREATE_TABLES_ON_STARTUP opt-in.
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


# -------------- application lifespan (P3-#15) --------------
# The full startup/shutdown orchestration lives here — one home, as a
# declarative phase list composed by ``lifespan_with_admin`` — so main.py
# stays a thin composition root (app factory + middleware + OpenAPI).
# Every phase keeps its historical behavior verbatim; only the address
# changed (moved from main.py).

# admin interface initialized inside lifespan to eliminate import-time side effects
admin: "CRUDAdmin | None" = None


async def _warm_token_blacklist_cache() -> None:
    """Warm the token blacklist Redis cache from database on startup."""
    # RedisError imported at module top (M-3: duplicate import removed).

    # P1-4: narrow excepts to actual infra failure types so a bug in the
    # warmer surfaces immediately. The blacklist is auth-critical, so a
    # warmup failure in production must fail-closed (raised) rather than
    # silently leave the app serving with a cold cache.
    from sqlalchemy.exc import SQLAlchemyError

    from src.app.core.db.crud_token_blacklist import crud_token_blacklist
    from src.app.core.db.database import async_session_maker
    from src.app.core.security.token_blacklist_cache import warm_cache_from_db

    try:
        async with async_session_maker() as db:
            # P-M9: cap the warmup set — loading every active blacklisted
            # token made boot cost O(total sessions). Keep the N
            # longest-lived entries (they stay in the cache longest, so the
            # warm-up value per slot is highest); the rest fall back to the
            # on-demand DB check on first lookup. TOKEN_BLACKLIST_WARMUP_LIMIT
            # is env-tunable (APP_TOKEN_BLACKLIST_WARMUP_LIMIT); 0 disables
            # warmup entirely (and skips the query). P2: the cap is pushed
            # into the DB (ORDER BY expires_at DESC + LIMIT) so boot is
            # O(limit) even before the first row is read.
            limit = settings.app.TOKEN_BLACKLIST_WARMUP_LIMIT
            if limit == 0:
                logger.info("Token blacklist warmup disabled by TOKEN_BLACKLIST_WARMUP_LIMIT=0")
            else:
                tokens = await crud_token_blacklist.get_active_tokens(db, limit=limit)
                if tokens:
                    count = await warm_cache_from_db(tokens)
                    logger.info("Token blacklist cache warmed", count=count)
    except (RedisError, SQLAlchemyError, ServiceUnavailableError) as e:
        logger.error("Failed to warm token blacklist cache", error=str(e))
        if settings.env.ENVIRONMENT == EnvironmentOption.PRODUCTION:
            raise
        logger.warning("Continuing with cold blacklist cache (non-production)")


def _init_pool_metrics() -> None:
    """Initialize database pool metrics collection."""
    from src.app.core.db.database import get_async_engine
    from src.app.core.utils.pool_metrics import setup_pool_metrics

    try:
        async_engine = get_async_engine()
        setup_pool_metrics(async_engine)
    except Exception as e:  # noqa: BLE001 - P1-17: observability only; metrics must not block boot
        logger.warning("Pool metrics setup failed", error=str(e))


def _init_event_bus_and_queue(redis_client: Any) -> None:
    """Initialize event bus, background queue, and pre-register handlers."""
    from src.app.core.events import event_bus

    if redis_client:
        event_bus.set_redis_client(redis_client)
        if settings.arq.ARQ_ENABLED:
            try:
                from src.app.core.utils.queue import pool as arq_queue

                if arq_queue:
                    event_bus.set_arq_queue(arq_queue)
                    logger.info("ARQ queue initialized for async event dispatch (reusing shared pool)")
                else:
                    logger.warning("Shared ARQ queue pool not initialized, events will run synchronously")
            except Exception as e:  # noqa: BLE001 - P1-17: sync dispatch is the designed degraded mode
                logger.warning("ARQ queue initialization failed, events will run synchronously", error=str(e))
        else:
            logger.info("ARQ background tasks are disabled, events will run synchronously")

    event_bus.register_all_handlers()


async def _init_rate_limiting_and_security(redis_client: Any) -> None:
    """Initialize session manager, GCRA rate limiter, and Lua scripts."""
    if not redis_client:
        return

    from src.app.core.security.session_manager import session_manager

    session_manager.set_redis_client(redis_client)

    from src.app.core.utils.gcra_rate_limit import init_gcra_rate_limiter
    from src.app.core.utils.lua_manager import get_lua_manager

    init_gcra_rate_limiter(redis_client)
    logger.info("GCRA rate limiter initialized")
    try:
        loaded_scripts = await get_lua_manager().preload_scripts(redis_client)
        logger.info("Lua scripts preloaded", count=len(loaded_scripts))
    except RedisError as e:
        logger.error("Failed to preload Lua scripts", error=str(e))
        if settings.env.ENVIRONMENT == EnvironmentOption.PRODUCTION:
            raise

    from src.app.core.security.auto_ban import _start_ban_l1_invalidation_listener
    from src.app.core.security.rate_limit_cache import start_policy_invalidation_listener
    from src.app.core.tenancy.cache import start_l1_invalidation_listener

    _start_ban_l1_invalidation_listener()
    start_policy_invalidation_listener()
    # T9: without this listener, tenant L1 invalidation publishes into the
    # void and other workers serve a suspended tenant as active for up to
    # the L1 TTL (60s).
    start_l1_invalidation_listener()
    # R1-OPT: auth-info (8s) + bitmask (30s) L1s need the same cross-worker
    # fan-out, else a role revoke / deactivation stays visible to other
    # workers until TTL expiry.
    from src.app.core.rbac.dependencies import start_bitmask_l1_invalidation_listener
    from src.app.modules.users.cache import start_auth_info_l1_invalidation_listener

    start_auth_info_l1_invalidation_listener()
    start_bitmask_l1_invalidation_listener()


def apply_security_runtime_config(config: dict[str, Any]) -> list[str]:
    """Validate + inject runtime security overrides into process-global settings.

    P3-#15: this is the ONE sanctioned site that mutates
    ``settings.security`` after boot — the old inline ``setattr`` loop in
    main.py's ``_load_security_runtime_config``. Making it an explicit,
    named, testable function (instead of an anonymous loop inside a
    lifespan phase) is the "explicit runtime-config injection": every key
    is validated with ``TypeAdapter`` against the model field before it is
    applied, unknown/invalid keys are logged and skipped, and the applied
    key list is returned for observability.

    Args:
        config: Raw key/value overrides from the Redis runtime-config store.

    Returns:
        The keys that were successfully applied.
    """
    from pydantic import TypeAdapter, ValidationError

    security_keys = (
        "IP_WHITELIST_ENABLED",
        "IP_BLACKLIST_ENABLED",
        "IP_WHITELIST_BYPASS_RATE_LIMIT",
        "AUTO_BAN_ENABLED",
        "AUTO_BAN_THRESHOLD",
        "AUTO_BAN_DURATION",
        "AUTO_BAN_ESCALATION",
    )
    applied: list[str] = []
    model_fields = type(settings.security).model_fields
    for key in security_keys:
        if key not in config:
            continue
        field = model_fields.get(key)
        if field is None or field.annotation is None:
            logger.error("Skipping unknown security runtime config key", key=key)
            continue
        raw = config[key]
        try:
            coerced = raw.strip() if isinstance(raw, str) else raw
            validated = TypeAdapter(field.annotation).validate_python(coerced)
            setattr(settings.security, key, validated)
            applied.append(key)
        except (ValidationError, ValueError, TypeError) as e:
            logger.error("Skipping invalid security runtime config value", key=key, error=str(e))
            continue
    return applied


async def _load_security_runtime_config() -> None:
    """Load dynamic security configuration overrides from Redis store via table loop."""
    from src.app.core.utils.config_store import get_runtime_config

    config = await get_runtime_config()
    if not config:
        return

    apply_security_runtime_config(config)
    logger.info("Security runtime config loaded from Redis")


async def _warm_startup_caches() -> None:
    """Warm the token blacklist cache on startup.

    P2-4: application caches (tenants/tiers/feature flags) are warmed
    exactly once by the setup.py background task (cross-worker Redis
    lock). Warming them here too doubled DB + Redis load on every boot,
    so this lifespan phase now covers only the auth-critical blacklist.
    """
    logger.info("Starting application cache warming...")
    try:
        await asyncio.wait_for(
            _warm_token_blacklist_cache(),
            timeout=15.0,
        )
    except TimeoutError:
        logger.warning("Cache warming exceeded 15s timeout; continuing boot with partial state")


async def _init_admin() -> None:
    """Initialize admin interface if enabled."""
    global admin
    if admin is None:
        from src.app.admin.initialize import create_admin_interface

        admin = create_admin_interface()
    if admin:
        try:
            await admin.initialize()
            logger.info("Admin interface initialized successfully")
        except Exception as e:  # noqa: BLE001 - P1-17: admin is optional; API must boot without it
            logger.error("Failed to initialize admin interface", error=str(e))


async def _teardown_security_listeners() -> None:
    """Shutdown bcrypt thread pool and cancel L1 pubsub invalidation listeners."""
    try:
        from src.app.core.security import _bcrypt_executor

        if _bcrypt_executor is not None:
            _bcrypt_executor.shutdown(wait=True)
            import src.app.core.security

            src.app.core.security._bcrypt_executor = None
            logger.info("Bcrypt thread pool shutdown successfully")
    except Exception as e:  # noqa: BLE001 - P1-17: teardown must not mask shutdown
        logger.error("Failed to shutdown bcrypt thread pool", error=str(e))

    from src.app.core.security.auto_ban import stop_ban_l1_invalidation_listener
    from src.app.core.security.rate_limit_cache import stop_policy_invalidation_listener
    from src.app.core.tenancy.cache import stop_l1_invalidation_listener

    await stop_ban_l1_invalidation_listener()
    await stop_l1_invalidation_listener()
    await stop_policy_invalidation_listener()
    from src.app.core.rbac.dependencies import stop_bitmask_l1_invalidation_listener
    from src.app.modules.users.cache import stop_auth_info_l1_invalidation_listener

    await stop_auth_info_l1_invalidation_listener()
    await stop_bitmask_l1_invalidation_listener()
    try:
        from src.app.modules.auth.http_client import close_sso_http_client

        await close_sso_http_client()
    except Exception as e:  # noqa: BLE001 - P1-17: teardown must not mask shutdown
        logger.error("Failed to close SSO HTTP client", error=str(e))


@asynccontextmanager
async def lifespan_with_admin(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown orchestration composing modular phases."""
    from src.app.core.logger import setup_logging

    setup_logging()

    from src.app.core.env_validator import EnvironmentValidator

    if not EnvironmentValidator.validate_and_log():
        if settings.env.ENVIRONMENT == EnvironmentOption.PRODUCTION:
            raise RuntimeError("Environment validation failed - refusing to start in production")

    default_lifespan = lifespan_factory(settings, create_tables_on_start=settings.app.CREATE_TABLES_ON_STARTUP)

    async with default_lifespan(app):
        from src.app.core.redis_provider import get_redis

        redis_client = get_redis()
        _init_pool_metrics()
        _init_event_bus_and_queue(redis_client)
        await _init_rate_limiting_and_security(redis_client)
        await _load_security_runtime_config()
        await _warm_startup_caches()
        await _init_admin()

        yield

        await _teardown_security_listeners()
        from src.app.core.logger import shutdown_logging

        shutdown_logging()
