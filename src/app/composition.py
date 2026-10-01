"""Application composition root for web runtime wiring.

The reusable lifecycle mechanism lives in :mod:`src.app.core.runtime`. This
module owns the concrete phase plan, feature startup/cleanup callbacks, and
FastAPI application assembly.
"""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import _AsyncGeneratorContextManager, asynccontextmanager
from functools import partial
from typing import TYPE_CHECKING, Any

import fastapi
from fastapi import APIRouter, Depends, FastAPI
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from src.app.api.deps import get_current_superuser
from src.app.core import setup as runtime_setup
from src.app.core.config import EnvironmentOption, Settings, settings
from src.app.core.logger import get_logger
from src.app.core.runtime import StartupPhase, compose_phases
from src.app.core.utils import cache
from src.app.shared.exceptions import ServiceUnavailableError

if TYPE_CHECKING:
    from crudadmin import CRUDAdmin

logger = get_logger(__name__)


async def _start_redis_cache_pool(enabled: bool) -> None:
    if enabled:
        await runtime_setup.create_redis_cache_pool()


async def _stop_redis_cache_pool(enabled: bool) -> None:
    if enabled:
        await runtime_setup.close_redis_cache_pool()


async def _start_redis_queue_pool(enabled: bool) -> None:
    if enabled:
        await runtime_setup.create_redis_queue_pool()


async def _stop_redis_queue_pool(enabled: bool) -> None:
    if enabled:
        await runtime_setup.close_redis_queue_pool()


async def _start_create_tables(enabled: bool) -> None:
    if enabled:
        await runtime_setup.create_tables()


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


async def _start_tenant_model_gate() -> None:
    # H-5: core.tenancy resolves and warms tenants from a row type the
    # application registers (src.app.models). An unwired process would fail
    # on the first slug lookup, which is far harder to diagnose than a boot
    # failure — so check it here, beside the other DIP gates.
    from src.app.core.tenancy.models import ensure_tenant_model_registered

    ensure_tenant_model_registered()


async def _start_bypass_recorder_gate() -> None:
    # The RLS-bypass recorder is supplied by the audit module. Unwired, the
    # worker would still bypass RLS — _record_bypass_audit swallows failures
    # by design — so without this gate a bypass could be silently unaudited.
    from src.app.core.worker.audit import ensure_bypass_recorder_registered

    ensure_bypass_recorder_registered()


async def _start_rate_policy_gate() -> None:
    # H-5: the tier path-policy rows are fetched by a callable the application
    # registers. Unwired, the failure would surface on the first cache miss at
    # request time — check it at boot with the other DIP gates.
    from src.app.core.security.policies import ensure_rate_policy_fetcher_registered

    ensure_rate_policy_fetcher_registered()


async def _start_permission_graph_gate() -> None:
    # H-5: core.rbac joins four row types the application registers. An
    # unwired process would fail on the first permission lookup, so check it
    # at boot with the other DIP gates.
    from src.app.core.rbac.models import ensure_permission_graph_registered

    ensure_permission_graph_registered()


async def _start_permission_catalog_reconcile() -> None:
    """Make the permissions table match the Permission enum (run-3 finding #6).

    Migration 03_access_control seeded a fixed slug list that drifted from the
    enum, leaving 15 declared permissions ungrantable — add_permission_to_role
    resolves against the table and raises ModelNotFoundError when a slug is
    absent, so a permission added to the enum alone yields an INERT gate.

    The migration is the primary fix; this phase makes the invariant self-healing
    so drift cannot silently return when someone adds an enum value and forgets
    the migration.

    Deliberately logs rather than raises on failure. Every other boot gate here
    fails loud because an unwired seam causes mass 401s; a catalog row that is
    missing only degrades *new permission grants* for one slug. Tearing down a
    serving process for that would be disproportionate, so the error is loud in
    the log and the migration remains the authoritative repair path.
    phase the successor mapping is applied when any role actually holds
    ``files:manage`` (a legacy database, or one carrying operator-created rows).
    """
    from src.app.core.db.database import local_session
    from src.app.modules.rbac.catalog import (
        migrate_successor_grants,
        reconcile_permission_catalog,
    )

    try:
        async with local_session() as db:
            report = await reconcile_permission_catalog(db)
            if not report.stale:
                # A database built from the corrected 03_access_control has no
                # stale slugs, so there is nothing to carry forward.
                await migrate_successor_grants(db)
    except Exception as e:  # noqa: BLE001 - catalog sync must not block startup
        logger.error("permission_catalog_reconcile_failed", error=str(e))
        return
    if report.stale:
        logger.info(
            "permission_catalog_stale_slugs_retained",
            slugs=list(report.stale),
            reason="kept for backward compatibility; not grantable via the enum",
        )


async def _start_security_budget_gate() -> None:
    # Every leg's budget name now comes from the security-budget seam's table,
    # so an operator who removes an entry (e.g. mfa_verify_global) would
    # otherwise hit a KeyError on the first MFA verification in production.
    # This is also what retires the old call-site settings sniff, which
    # silently charged a different budget when the preferred one was absent.
    from src.app.core.security.budgets import validate_budget_definitions

    validate_budget_definitions()


async def _start_principal_provider_gate() -> None:
    # Candidate 2 (one principal-resolution seam): fail fast when the
    # principal DIP seam is unwired instead of surfacing as mass 401s.
    # ``core.principal`` owns the ``get_auth_context`` dependency so that
    # ``core.rbac`` need not import the api layer (H-5); ``api.deps.auth``
    # registers the resolver at import time. The per-request path still
    # fails closed (503); this is the loud boot gate.
    try:
        from src.app.core.principal import ensure_principal_provider_registered

        ensure_principal_provider_registered()
    except Exception as e:  # noqa: BLE001 — startup must stay loud, not crash on log path
        from src.app.core.logger import get_logger as _gl

        _gl(__name__).error("principal_provider_startup_check_failed", error=str(e))
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
            logger.warning("cache_warming_failed", error=str(e))

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


async def _close_storage_providers() -> None:
    """Drain the provider cache; teardown must not mask shutdown."""
    try:
        from src.app.modules.storage.admin import close_cached_providers

        await close_cached_providers()
    except Exception as e:  # noqa: BLE001 — teardown is best-effort
        get_logger(__name__).warning("Failed to close cached storage providers", error=str(e))


async def _teardown_logging() -> None:
    from src.app.core.logger import shutdown_logging

    shutdown_logging()


def lifespan_factory(
    settings: Settings | Any,
    create_tables_on_start: bool = True,
) -> Callable[[FastAPI], _AsyncGeneratorContextManager[Any]]:
    """Factory to create a lifespan async context manager for a FastAPI app.

    A-M4: ``create_tables_on_start`` is gated by
    ``resolve_create_tables_on_start`` — outside local it defaults to off
    unless settings.app.CREATE_TABLES_ON_STARTUP explicitly opts in.
    """
    create_tables_on_start = runtime_setup.resolve_create_tables_on_start(settings, create_tables_on_start)

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

        # NOTE: the threadpool is sized by the "threadpool" StartupPhase
        # below, not here. It used to run both ways — once as a bare await
        # and once as a phase — which is idempotent today and a trap the
        # moment the body is not.
        #
        # S1 FIX: initialize flags BEFORE the try block. If a fallible create
        # call below raises, the finally block references these names; assigning
        # them inside the try let an early failure raise NameError and mask the
        # original startup exception.
        redis_cache_enabled = settings.redis_cache.REDIS_CACHE_ENABLED
        redis_queue_enabled = settings.redis_queue.REDIS_QUEUE_ENABLED

        phases = [
            StartupPhase("threadpool", runtime_setup.set_threadpool_tokens, None),
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
            StartupPhase("principal_gate", _start_principal_provider_gate, None),
            StartupPhase("security_budget_gate", _start_security_budget_gate, None),
            StartupPhase("tenant_model_gate", _start_tenant_model_gate, None),
            StartupPhase("permission_graph_gate", _start_permission_graph_gate, None),
            StartupPhase("permission_catalog_reconcile", _start_permission_catalog_reconcile, None),
            StartupPhase("rate_policy_gate", _start_rate_policy_gate, None),
            StartupPhase("bypass_recorder_gate", _start_bypass_recorder_gate, None),
            # The phase NAME is part of the observable startup contract and is
            # asserted behaviourally (the phase sequence handed to
            # compose_phases), not by grepping this file.
            StartupPhase("warm_caches", partial(_start_cache_warmer, initialization_complete), None),
        ]
        # Teardown pairing: the pool closes ride their phases (reverse
        # order: queue before cache — independent pools, both closed before
        # the shared pool below, so the reorder is behavior-preserving).
        # Everything else keeps its verbatim finally-block position in tail.
        factory_tail: list[Callable[[], Awaitable[None]]] = [
            partial(_remove_init_event, app),
            # Close cached storage providers (S3/Azure hold HTTP clients)
            # before the shared pool teardown below.
            _close_storage_providers,
            _dispose_engine,
            # Close the shared Redis connection pool AFTER clients are closed
            runtime_setup.close_redis_pool,
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
    from src.app.core.cache.l1 import start_all_l1_listeners

    import_l1_owner_modules()
    start_all_l1_listeners()


def import_l1_owner_modules() -> None:
    """The one enumeration of L1 owner modules (I11).

    Core owns four of the five owners and exposes
    ``import_l1_core_owner_modules``; the fifth lives in the users feature
    module, which core may not name (H-5). Composing the full list here —
    the application seam — keeps the enumeration single and explicit, which is
    what makes a deterministic boot possible: Python has no reliable
    auto-discovery for "every module that declares an L1", and a scan would
    reintroduce the silent publish-into-the-void failure the registry exists to
    prevent.

    Adding an L1 means declaring its seam in the owner module and adding one
    import here. The contract test asserts the resulting channel set.
    """
    from src.app.core.cache.l1 import import_l1_core_owner_modules

    import_l1_core_owner_modules()
    from src.app.modules.users import cache as _users_cache  # noqa: F401


async def _load_security_runtime_config() -> None:
    """Apply the stored security overrides to ``settings.security``.

    The phase callback is a delegation: the store, its key grammar, the
    apply loop, and the fail-open-on-outage decision all live in
    ``core.security.runtime_config`` — the one module that owns them.
    """
    from src.app.core.security.runtime_config import load_runtime_config

    await load_runtime_config()


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
    from src.app.core.cache.l1 import stop_all_l1_listeners

    await stop_all_l1_listeners()
    # SSO HTTP client teardown is single-owned by lifespan_factory's finally
    # (P2): it always runs — lifespan_with_admin composes that lifespan — so
    # closing here too was a guaranteed double close on every shutdown.


@asynccontextmanager
async def lifespan_with_admin(app: FastAPI) -> AsyncGenerator[None]:
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
            StartupPhase("rate_limiting_security", partial(_init_rate_limiting_and_security, redis_client), None),
            StartupPhase("runtime_config", _load_security_runtime_config, None),
            # No tenant_model_gate here: lifespan_with_admin composes
            # lifespan_factory's lifespan (see above), whose `phases` list
            # already runs tenant_model_gate before these admin phases start.
            # The old copy here carried a comment justifying it by what "the
            # worker" needs — a worker concern sitting in the web phase list.
            # muse 18: this phase used to be named "warm_caches", the SAME
            # name as the web phase above, while doing different work — this
            # one is the blocking, 15s-bounded token-blacklist warm; the web
            # one is a fire-and-forget application cache warm. Two phases
            # sharing a name make per-phase timing and any name-keyed
            # operator signal ambiguous. tests/unit/core/
            # test_startup_phase_duplication.py asserts the union of BOTH
            # lists has no repeated name.
            StartupPhase("warm_token_blacklist", _warm_startup_caches, None),
            StartupPhase("admin", _init_admin, None),
        ]
        async with compose_phases(admin_phases, tail=(_teardown_security_listeners, _teardown_logging)):
            yield
