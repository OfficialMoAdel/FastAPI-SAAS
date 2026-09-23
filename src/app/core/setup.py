import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import _AsyncGeneratorContextManager, asynccontextmanager
from functools import partial
from typing import TYPE_CHECKING, Any, NamedTuple

import anyio
import fastapi
from fastapi import APIRouter, Depends, FastAPI
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
    # A-M4 companion: make the schema-management path observable at boot.
    # Alembic is the canonical schema owner; create_all here is the
    # local-dev bootstrap only (see _resolve_create_tables_on_start).
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


class StartupPhase(NamedTuple):
    """One boot step: ``(name, startup, teardown)`` — order is data.

    The composer runs startups in list order and teardowns in reverse;
    shared teardown without a matching startup (pools owned elsewhere,
    logging shutdown) rides the ``tail`` list in listed order. Card 10:
    the two lifespan closures hard-coded this choreography inline
    (implicit order, teardown split across two functions, untestable);
    now both are built from these rows.
    """

    name: str
    startup: Callable[[], Awaitable[None]]
    teardown: Callable[[], Awaitable[None]] | None = None


@asynccontextmanager
async def compose_phases(
    phases: Sequence[StartupPhase],
    tail: Sequence[Callable[[], Awaitable[None]]] = (),
    teardown_on_startup_failure: bool = False,
) -> AsyncGenerator[None, None]:
    """Run phase startups in order, yield, then reverse teardowns + tail.

    Startup-failure handling differs per composer, kept verbatim from the
    legacy bodies: the factory's ``try/finally`` always ran every close
    even when a startup step raised (``teardown_on_startup_failure=True``);
    ``lifespan_with_admin`` runs its tail only on the clean path
    (``False`` — a failed admin phase unwinds through the factory
    lifespan, whose own teardown still runs).
    """
    try:
        for phase in phases:
            await phase.startup()
    except BaseException:
        if teardown_on_startup_failure:
            for phase in reversed(phases):
                if phase.teardown is not None:
                    await phase.teardown()
            for teardown in tail:
                await teardown()
        raise
    try:
        yield
    finally:
        for phase in reversed(phases):
            if phase.teardown is not None:
                await phase.teardown()
        for teardown in tail:
            await teardown()


async def _start_redis_cache_pool(enabled: bool) -> None:
    if enabled:
        await create_redis_cache_pool()


async def _stop_redis_cache_pool(enabled: bool) -> None:
    if enabled:
        await close_redis_cache_pool()


async def _start_redis_queue_pool(enabled: bool) -> None:
    if enabled:
        await create_redis_queue_pool()


async def _stop_redis_queue_pool(enabled: bool) -> None:
    if enabled:
        await close_redis_queue_pool()


async def _start_create_tables(enabled: bool) -> None:
    if enabled:
        await create_tables()


async def _start_users_security_lookups() -> None:
    # Explicit user-domain security lookup wiring (deterministic;
    # backstops the import-time registration in users/__init__.py).
    from src.app.modules.users import register_users_security_lookups

    register_users_security_lookups()


async def _start_jwt_lookup_gate() -> None:
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


async def _start_cache_warmer(done: asyncio.Event) -> None:
    # Warm caches after DB and Redis are ready. Fire and forget with safe
    # error handling and hard reference; ``done`` signals "startup
    # teardown is allowed", not "caches are warm" (P3-5 note at the call
    # site — the warmer completes asynchronously).
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

    from src.app.core.utils.background_task import create_safe_task

    create_safe_task(_warm_caches_background(), name="cache_warming_startup")
    done.set()


async def _remove_init_event(app: FastAPI) -> None:
    if hasattr(app.state, "initialization_complete"):
        delattr(app.state, "initialization_complete")


async def _dispose_engine() -> None:
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


async def _close_sso_http_client() -> None:
    # Close SSO HTTP client pool
    from src.app.modules.auth.http_client import close_sso_http_client

    await close_sso_http_client()


async def _close_webhook_http_client() -> None:
    # Close Webhook HTTP client pool
    from src.app.modules.webhooks.http_client import close_webhook_http_client

    await close_webhook_http_client()


async def _shutdown_ai_providers() -> None:
    # Close AI providers
    from src.app.modules.ai.factory import AIProviderFactory

    await AIProviderFactory.shutdown_all()


async def _start_pool_metrics() -> None:
    _init_pool_metrics()


async def _start_event_bus_and_queue(redis_client: Any) -> None:
    _init_event_bus_and_queue(redis_client)


async def _teardown_logging() -> None:
    from src.app.core.logger import shutdown_logging

    shutdown_logging()


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

        phases = [
            StartupPhase("threadpool", set_threadpool_tokens, None),
            StartupPhase(
                "redis_cache",
                partial(_start_redis_cache_pool, redis_cache_enabled),
                partial(_stop_redis_cache_pool, redis_cache_enabled),
            ),
            StartupPhase(
                "redis_queue",
                partial(_start_redis_queue_pool, redis_queue_enabled),
                partial(_stop_redis_queue_pool, redis_queue_enabled),
            ),
            StartupPhase("tables", partial(_start_create_tables, create_tables_on_start), None),
            StartupPhase("users_lookups", _start_users_security_lookups, None),
            StartupPhase("jwt_gate", _start_jwt_lookup_gate, None),
            # "warm_caches" + "_start_cache_warmer": pinned by name in
            # tests/performance/test_optimizations_r4/test_cache_warming.py
            # (source-string assertions) — keep both spellings on this row.
            StartupPhase("warm_caches", partial(_start_cache_warmer, initialization_complete), None),
        ]
        # Teardown pairing: the pool closes ride their phases (reverse
        # order: queue before cache — independent pools, both closed before
        # the shared pool below, so the reorder is behavior-preserving).
        # Everything else keeps its verbatim finally-block position in tail.
        factory_tail: list[Callable[[], Awaitable[None]]] = [
            partial(_remove_init_event, app),
            _dispose_engine,
            # Close the shared Redis connection pool AFTER clients are closed
            close_redis_pool,
            _close_sso_http_client,
            _close_webhook_http_client,
            _shutdown_ai_providers,
        ]
        async with compose_phases(phases, tail=factory_tail, teardown_on_startup_failure=True):
            yield

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

    # C4: CORS is registered by the single middleware wiring point
    # (core/guard/ordering.py, outermost entry) so guard short-circuits
    # (403/429/413) also carry Access-Control-Allow-Origin. Nothing to add
    # here — registering it in the factory would nest it INSIDE the guards.

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

    # I11: the central registry owns L1 listener lifecycle. Importing the
    # owner modules triggers their self-registration; one start call replaces
    # the five hand-wired listeners that used to live here.
    from src.app.core.cache.l1_registry import import_l1_owner_modules, start_all_l1_listeners

    import_l1_owner_modules()
    start_all_l1_listeners()


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

    # I11: registry owns teardown — one call stops every registered listener.
    from src.app.core.cache.l1_registry import stop_all_l1_listeners

    await stop_all_l1_listeners()
    # SSO HTTP client teardown is single-owned by lifespan_factory's finally
    # (P2): it always runs — lifespan_with_admin composes that lifespan — so
    # closing here too was a guaranteed double close on every shutdown.


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
        admin_phases = [
            StartupPhase("pool_metrics", _start_pool_metrics, None),
            StartupPhase("event_bus_queue", partial(_start_event_bus_and_queue, redis_client), None),
            StartupPhase(
                "rate_limiting_security", partial(_init_rate_limiting_and_security, redis_client), None
            ),
            StartupPhase("runtime_config", _load_security_runtime_config, None),
            StartupPhase("warm_caches", _warm_startup_caches, None),
            StartupPhase("admin", _init_admin, None),
        ]
        async with compose_phases(admin_phases, tail=(_teardown_security_listeners, _teardown_logging)):
            yield
