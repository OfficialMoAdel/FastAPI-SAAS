"""FastAPI SaaS application entry point."""

from dotenv import load_dotenv

load_dotenv()

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from crudadmin import CRUDAdmin
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from redis.exceptions import RedisError  # P1-4

from src.app.admin.initialize import create_admin_interface
from src.app.api import get_router
from src.app.core.config import EnvironmentOption, settings
from src.app.core.logger import get_logger
from src.app.core.setup import create_application, lifespan_factory
from src.app.middleware.attack_detection import AttackDetectionMiddleware
from src.app.middleware.body_limit import RequestBodyLimitMiddleware
from src.app.middleware.gzip_filter import GZipFilterMiddleware
from src.app.middleware.ip_filter import IPFilterMiddleware
from src.app.middleware.query_profiler import QueryProfilerMiddleware
from src.app.middleware.rate_limit import RateLimitMiddleware
from src.app.middleware.request_id import RequestIDMiddleware
from src.app.middleware.security_headers import SecurityHeadersMiddleware
from src.app.middleware.tenant import TenantMiddleware
from src.app.shared.exceptions import ServiceUnavailableError

# admin interface initialized inside lifespan to eliminate import-time side effects
admin: "CRUDAdmin | None" = None
logger = get_logger(__name__)


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


async def _load_security_runtime_config() -> None:
    """Load dynamic security configuration overrides from Redis store via table loop."""
    from pydantic import TypeAdapter, ValidationError

    from src.app.core.utils.config_store import get_runtime_config

    config = await get_runtime_config()
    if not config:
        return

    security_keys = (
        "IP_WHITELIST_ENABLED",
        "IP_BLACKLIST_ENABLED",
        "IP_WHITELIST_BYPASS_RATE_LIMIT",
        "AUTO_BAN_ENABLED",
        "AUTO_BAN_THRESHOLD",
        "AUTO_BAN_DURATION",
        "AUTO_BAN_ESCALATION",
    )
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
        except (ValidationError, ValueError, TypeError) as e:
            logger.error("Skipping invalid security runtime config value", key=key, error=str(e))
            continue
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


app = create_application(
    router=get_router(),
    settings=settings,
    lifespan=lifespan_with_admin,
    default_response_class=ORJSONResponse,
)


# Middleware stack (LIFO - last added runs first on request)
# Request flow:
#   GZip → QueryProfiler → IPFilter → BodyLimit → SecurityHeaders
#     → RateLimit → Tenant → AttackDetection → RequestID → CORS → Handler
# Response flow:
#   Handler → CORS → RequestID → AttackDetection → Tenant → RateLimit
#     → SecurityHeaders → BodyLimit → IPFilter → QueryProfiler → GZip
#
# Pinned by tests/unit/test_middleware_order.py (R1-16 security contract).
#
# Key ordering decisions:
# - RequestID: Added first = innermost (closest to handler for accurate tracing)
# - RateLimit: Runs before Tenant so a cheap 429 never pays the tenant
#   lookup (L1+L2+DB). Safe: the middleware resolves tenant_id from JWT
#   claims/request.state, never from the tenant ContextVar (P1-15; the old
#   P2-5 Tenant-first rationale is stale — nothing in the rate-limit request
#   path reads get_current_tenant; dep-layer is_rate_limited still sees the
#   context because TenantMiddleware runs before the handler regardless).
# - Tenant: Extract tenant context for multi-tenant features
# - SecurityHeaders: Add security headers to all responses
# - AttackDetection: Registered before RateLimit so it runs AFTER it in LIFO —
#   the expensive body-buffering scan never runs for requests a cheap 429
#   rejects (R4). On the request path it sits after BodyLimit, so oversized
#   payloads are rejected by 413 before the scan buffers them.
# - BodyLimit: Reject oversized requests early
# - IPFilter: Block unwanted IPs early, sets ip_whitelisted state for RateLimit
# - GZip: Added last = outermost (compresses ALL responses including 4xx/5xx errors)
app.add_middleware(RequestIDMiddleware)  # Innermost - must be first for accurate tracing
# R4 FIX: AttackDetection is now registered BEFORE RateLimitMiddleware, so in
# LIFO execution order it runs AFTER rate limiting. Previously the expensive
# body-buffering scan ran first on every request, including ones a cheap
# 429 would have rejected immediately.
app.add_middleware(AttackDetectionMiddleware)  # Detect SQLi, XSS, path traversal
app.add_middleware(TenantMiddleware)  # Tenant context for multi-tenant features
# P1-15: RateLimit added AFTER Tenant so it runs FIRST on the request path
# (LIFO) — 429s reject before the tenant lookup, not after it.
app.add_middleware(RateLimitMiddleware)  # User/tier-based rate limiting
app.add_middleware(SecurityHeadersMiddleware)  # XSS prevention headers
app.add_middleware(
    RequestBodyLimitMiddleware, max_body_size=settings.app.MAX_BODY_SIZE
)  # Configurable limit (runs AFTER AttackDetection in LIFO)
app.add_middleware(IPFilterMiddleware)  # Block unwanted IPs, sets ip_whitelisted state
app.add_middleware(QueryProfilerMiddleware)  # Dev-mode query profiling (disabled in production)
# Setup GZip with a content-type filter (exempts SSE and binary)
# P2-7: compresslevel 4 over zlib's default 6 — roughly 2x faster compression
# for a small size penalty; the right trade for a JSON API serving many small
# bodies where CPU per request matters more than bytes on the wire.
app.add_middleware(GZipFilterMiddleware, minimum_size=500, compresslevel=4)

# Admin mounting moved to lifespan


# OpenAPI custom schema (single wiring point in api/openapi.py)
from src.app.api.openapi import setup_custom_openapi

setup_custom_openapi(app)
