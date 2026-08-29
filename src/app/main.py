"""FastAPI SaaS application entry point."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.responses import ORJSONResponse

from src.app.admin.initialize import create_admin_interface
from src.app.api import get_router
from src.app.core.cache.warmer import warmer
from src.app.core.config import EnvironmentOption, settings
from src.app.core.logger import get_logger
from src.app.core.setup import create_application, lifespan_factory
from src.app.middleware.attack_detection import AttackDetectionMiddleware
from src.app.middleware.body_limit import RequestBodyLimitMiddleware
from src.app.middleware.ip_filter import IPFilterMiddleware
from src.app.middleware.query_profiler import QueryProfilerMiddleware
from src.app.middleware.rate_limit import RateLimitMiddleware
from src.app.middleware.request_id import RequestIDMiddleware
from src.app.middleware.security_headers import SecurityHeadersMiddleware
from src.app.middleware.tenant import TenantMiddleware

# admin will be initialized before lifespan
admin: Any = create_admin_interface()
logger = get_logger(__name__)


async def _warm_token_blacklist_cache() -> None:
    """Warm the token blacklist Redis cache from database on startup."""
    from src.app.core.db.crud_token_blacklist import crud_token_blacklist
    from src.app.core.db.database import async_session_maker
    from src.app.core.security.token_blacklist_cache import warm_cache_from_db

    try:
        async with async_session_maker() as db:
            tokens = await crud_token_blacklist.get_active_tokens(db)
            if tokens:
                count = await warm_cache_from_db(tokens)
                logger.info("Token blacklist cache warmed", count=count)
    except Exception as e:
        logger.warning("Failed to warm token blacklist cache", error=str(e))


async def _warm_application_caches() -> None:
    """Warm application caches on startup.

    Uses CacheWarmer registry to warm all registered caches including:
    - Feature flags
    - Tiers
    - SSO settings
    """
    if not settings.app.CACHE_WARMING_ENABLED:
        logger.info("Cache warming is disabled")
        return

    from src.app.core.db.database import async_session_maker

    try:
        async with async_session_maker() as session:
            logger.info("Starting cache warming", warmer_count=len(warmer.list_warmers()))
            result = await warmer.warm_all(session)

            if result.errors:
                logger.warning("Cache warming completed with errors", errors=result.errors)
            else:
                logger.info(
                    "Cache warming successful",
                    tenants_cached=result.tenants_cached,
                    tiers_cached=result.tiers_cached,
                )
    except Exception as e:
        logger.error("Failed to warm application caches", error=str(e))


@asynccontextmanager
async def lifespan_with_admin(app: FastAPI) -> AsyncGenerator[None, None]:
    """Custom lifespan that includes admin initialization and cache warming."""
    from src.app.core.logger import setup_logging

    setup_logging()

    from src.app.core.env_validator import EnvironmentValidator

    if not EnvironmentValidator.validate_and_log():
        if settings.ENVIRONMENT == EnvironmentOption.PRODUCTION:
            raise RuntimeError("Environment validation failed - refusing to start in production")

    default_lifespan = lifespan_factory(settings, create_tables_on_start=settings.CREATE_TABLES_ON_STARTUP)

    async with default_lifespan(app):
        from src.app.core.db.database import get_async_engine
        from src.app.core.utils.pool_metrics import setup_pool_metrics

        try:
            async_engine = get_async_engine()
            setup_pool_metrics(async_engine)
        except Exception as e:
            logger.warning("Pool metrics setup failed", error=str(e))

        from src.app.core.events import event_bus
        from src.app.core.redis_provider import get_redis

        redis_client = get_redis()

        if redis_client:
            event_bus.set_redis_client(redis_client)
            # Initialize ARQ queue so NORMAL/LOW events dispatch asynchronously
            # Without this, all events fall back to synchronous in-process execution
            # Task 26: Only initialize if ARQ_ENABLED is True
            if settings.ARQ_ENABLED:
                try:
                    from src.app.core.utils.queue import pool as arq_queue

                    if arq_queue:
                        event_bus.set_arq_queue(arq_queue)
                        logger.info("ARQ queue initialized for async event dispatch (reusing shared pool)")
                    else:
                        logger.warning("Shared ARQ queue pool not initialized, events will run synchronously")
                except Exception as e:
                    logger.warning("ARQ queue initialization failed, events will run synchronously", error=str(e))
            else:
                logger.info("ARQ background tasks are disabled, events will run synchronously")

        # Pre-register event handlers to avoid cold start penalties
        # Explicitly registers module-level event handlers without dynamic scanning
        event_bus.register_all_handlers()

        # Initialize session manager for concurrent session limiting
        from src.app.core.security.session_manager import session_manager

        if redis_client:
            session_manager.set_redis_client(redis_client)

        # FIXED: Initialize GCRA rate limiter for algorithm selection
        from src.app.core.utils.gcra_rate_limit import init_gcra_rate_limiter
        from src.app.core.utils.lua_manager import get_lua_manager

        if redis_client:
            init_gcra_rate_limiter(redis_client)
            logger.info("GCRA rate limiter initialized")

            try:
                loaded_scripts = await get_lua_manager().preload_scripts(redis_client)
                logger.info("Lua scripts preloaded", count=len(loaded_scripts))
            except Exception as e:
                logger.warning("Failed to preload Lua scripts", error=str(e))

        # Warm application caches
        logger.info("Loading security runtime config...")
        from src.app.core.utils.config_store import get_runtime_config

        config = await get_runtime_config()
        if config:
            if "IP_WHITELIST_ENABLED" in config:
                settings.IP_WHITELIST_ENABLED = config["IP_WHITELIST_ENABLED"]
            if "IP_BLACKLIST_ENABLED" in config:
                settings.IP_BLACKLIST_ENABLED = config["IP_BLACKLIST_ENABLED"]
            if "IP_WHITELIST_BYPASS_RATE_LIMIT" in config:
                settings.IP_WHITELIST_BYPASS_RATE_LIMIT = config["IP_WHITELIST_BYPASS_RATE_LIMIT"]
            if "AUTO_BAN_ENABLED" in config:
                settings.AUTO_BAN_ENABLED = config["AUTO_BAN_ENABLED"]
            if "AUTO_BAN_THRESHOLD" in config:
                settings.AUTO_BAN_THRESHOLD = config["AUTO_BAN_THRESHOLD"]
            if "AUTO_BAN_DURATION" in config:
                settings.AUTO_BAN_DURATION = config["AUTO_BAN_DURATION"]
            if "AUTO_BAN_ESCALATION" in config:
                settings.AUTO_BAN_ESCALATION = config["AUTO_BAN_ESCALATION"]
            logger.info("Security runtime config loaded from Redis")

        logger.info("Starting application cache warming...")
        # A-7: the two warmers are independent (each opens its own session and
        # catches its own errors), so run them concurrently instead of serially.
        await asyncio.gather(_warm_token_blacklist_cache(), _warm_application_caches())

        # R1-06: the fixed post-warming sleep was removed. Cache warming is
        # already awaited above; sleeping before yield only delayed worker
        # readiness (2s x workers per boot) with no functional benefit.
        # Initialize admin interface if it exists
        if admin:
            try:
                # Initialize admin database and setup
                await admin.initialize()
                logger.info("Admin interface initialized successfully")
            except Exception as e:
                logger.error("Failed to initialize admin interface", error=str(e))

        yield

        # Shutdown bcrypt executor
        try:
            from src.app.core.security import _bcrypt_executor

            if _bcrypt_executor is not None:
                _bcrypt_executor.shutdown(wait=True)
                import src.app.core.security

                src.app.core.security._bcrypt_executor = None
                logger.info("Bcrypt thread pool shutdown successfully")
        except Exception as e:
            logger.error("Failed to shutdown bcrypt thread pool", error=str(e))

        # M-3: cancel the tenant L1 invalidation listener (leaked pubsub task).
        # No try/except needed: stop_l1_invalidation_listener is fully
        # self-guarded (catches CancelledError/Exception internally).
        from src.app.middleware.tenant import stop_l1_invalidation_listener

        await stop_l1_invalidation_listener()


app = create_application(
    router=get_router(),
    settings=settings,
    lifespan=lifespan_with_admin,
    default_response_class=ORJSONResponse,
)


# Middleware stack (LIFO - last added runs first on request)
# Request flow:
#   GZip → QueryProfiler → IPFilter → BodyLimit → AttackDetection
#     → SecurityHeaders → Tenant → RateLimit → RequestID → Handler
# Response flow:
#   Handler → RequestID → RateLimit → Tenant → SecurityHeaders
#     → AttackDetection → BodyLimit → IPFilter → QueryProfiler → GZip
#
# Key ordering decisions:
# - RequestID: Added first = innermost (closest to handler for accurate tracing)
# - RateLimit: Rate limiting before expensive processing
# - Tenant: Extract tenant context for multi-tenant features
# - SecurityHeaders: Add security headers to all responses
# - AttackDetection: Detect attacks after body limit to prevent large payload parsing attacks
# - BodyLimit: Reject oversized requests early
# - IPFilter: Block unwanted IPs early, sets ip_whitelisted state for RateLimit
# - GZip: Added last = outermost (compresses ALL responses including 4xx/5xx errors)

from src.app.middleware.gzip_filter import GZipFilterMiddleware

app.add_middleware(RequestIDMiddleware)  # Innermost - must be first for accurate tracing
# R4 FIX: AttackDetection is now registered BEFORE RateLimitMiddleware, so in
# LIFO execution order it runs AFTER rate limiting. Previously the expensive
# body-buffering scan ran first on every request, including ones a cheap
# 429 would have rejected immediately.
app.add_middleware(AttackDetectionMiddleware)  # Detect SQLi, XSS, path traversal
app.add_middleware(RateLimitMiddleware)  # User/tier-based rate limiting
app.add_middleware(TenantMiddleware)  # Tenant context for multi-tenant features
app.add_middleware(SecurityHeadersMiddleware)  # XSS prevention headers
app.add_middleware(
    RequestBodyLimitMiddleware, max_body_size=settings.MAX_BODY_SIZE
)  # Configurable limit (runs AFTER AttackDetection in LIFO)
app.add_middleware(IPFilterMiddleware)  # Block unwanted IPs, sets ip_whitelisted state
app.add_middleware(QueryProfilerMiddleware)  # Dev-mode query profiling (disabled in production)
# Setup GZip with a content-type filter (exempts SSE and binary)
app.add_middleware(GZipFilterMiddleware, minimum_size=500, compresslevel=6)

# Admin mounting moved to lifespan


# OpenAPI custom schema
def custom_openapi():
    """Generate custom OpenAPI schema."""
    if app.openapi_schema:
        return app.openapi_schema

    project_name = settings.app.APP_NAME
    version = settings.app.APP_VERSION or "1.0.0"
    description = settings.app.APP_DESCRIPTION

    openapi_schema = get_openapi(
        title=project_name,
        version=version,
        description=description,
        routes=app.routes,
    )

    components = openapi_schema.setdefault("components", {})
    security_schemes = components.setdefault("securitySchemes", {})
    security_schemes.update(
        {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "Enter JWT token obtained from /api/v1/auth/login",
            },
            "ApiKeyAuth": {
                "type": "apiKey",
                "in": "header",
                "name": "X-API-Key",
                "description": "Enter API Key",
            },
        }
    )

    # Identify paths that explicitly depend on authentication.
    # HARDENED: previously matched dependency __name__ against a string list,
    # so any same-named local function (or a renamed auth dep) silently
    # dropped OpenAPI protection. Now compare against the CANONICAL callables
    # imported from their owning modules — identity, not name similarity.
    protected_paths = set()
    from fastapi.params import Security

    from src.app.api.deps.auth import (
        get_current_superuser,
        get_current_user,
        get_optional_user,
        get_user_by_api_key,
    )

    _auth_callables = {
        id(fn) for fn in (get_current_user, get_current_superuser, get_optional_user, get_user_by_api_key)
    }

    for route in app.routes:
        if not hasattr(route, "dependencies"):
            continue
        for dep in getattr(route, "dependencies", []):
            is_auth_dep = isinstance(dep, Security)
            if not is_auth_dep and hasattr(dep, "dependency") and callable(dep.dependency):
                fn = dep.dependency
                if id(fn) in _auth_callables:
                    is_auth_dep = True
                else:
                    qualname = f"{getattr(fn, '__module__', '')}.{getattr(fn, '__qualname__', '')}"
                    # requires_permission() returns a closure; detect via its
                    # fully-qualified name rather than display name prefixes.
                    if qualname.startswith("src.app.core.rbac.dependencies.requires_permission"):
                        is_auth_dep = True

            if is_auth_dep and hasattr(route, "path"):
                protected_paths.add(route.path)
                break

    # Apply security only to protected routes
    for path_str, path_item in openapi_schema["paths"].items():
        is_protected = path_str in protected_paths
        for method in path_item.values():
            if method.get("security"):
                continue
            if is_protected:
                # Add both bearerAuth and ApiKeyAuth as OR conditions
                method.setdefault("security", []).append({"bearerAuth": []})
                method["security"].append({"ApiKeyAuth": []})

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi  # type: ignore[method-assign]
