from typing import Any

import anyio

from src.app.core.config import EnvironmentOption, Settings
from src.app.core.db.database import Base, get_async_engine
from src.app.core.logger import get_logger
from src.app.core.redis_provider import close_redis_pool
from src.app.core.runtime import StartupPhase, compose_phases
from src.app.core.utils import queue

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
    # local-dev bootstrap only (see resolve_create_tables_on_start).
    engine = get_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def resolve_create_tables_on_start(settings_obj: Settings | Any, requested: bool) -> bool:
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


__all__ = [
    "StartupPhase",
    "close_redis_cache_pool",
    "close_redis_pool",
    "close_redis_queue_pool",
    "compose_phases",
    "create_redis_cache_pool",
    "create_redis_queue_pool",
    "create_tables",
    "resolve_create_tables_on_start",
    "set_threadpool_tokens",
]
