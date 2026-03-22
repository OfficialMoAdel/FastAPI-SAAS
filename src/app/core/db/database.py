import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.asyncio.session import AsyncSession
from sqlalchemy.orm import DeclarativeBase, MappedAsDataclass, Session, declared_attr
from sqlalchemy.pool import NullPool

from src.app.core.config import settings
from src.app.core.db.profiler import is_profiler_enabled, setup_query_profiler
from src.app.core.db.rls import restore_rls_context, rls_context
from src.app.core.logger import get_logger
from src.app.core.tenancy.context import get_current_tenant

logger = get_logger(__name__)


class Base(DeclarativeBase, MappedAsDataclass):
    """Base class for all SQLAlchemy models.

    Includes extend_existing=True to prevent duplicate table registration
    errors during alembic migrations when models are imported from multiple paths.

    Subclasses can override __table_args__ with tuples containing Index/Constraint
    objects plus dict, e.g.: (Index(...), {"extend_existing": True})
    """

    @declared_attr.directive
    def __table_args__(cls) -> dict[str, Any] | tuple[Any, ...]:  # type: ignore[override]
        return {"extend_existing": True}


DATABASE_URI = settings.POSTGRES_URI
DATABASE_PREFIX = settings.POSTGRES_ASYNC_PREFIX
DATABASE_URL = f"{DATABASE_PREFIX}{DATABASE_URI}"

# Use NullPool in test mode to prevent event loop binding issues
# NullPool creates fresh connections per use, avoiding "Event loop is closed" errors
_is_testing = (
    "pytest" in os.environ.get("_", "")
    or "PYTEST_CURRENT_TEST" in os.environ
    or os.environ.get("TESTING", "").lower() in ("1", "true", "yes")
    or "pytest" in sys.modules
)

if _is_testing:
    async_engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        future=True,
        poolclass=NullPool,
    )
else:
    async_engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        future=True,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_pre_ping=True,
        pool_recycle=3600,
        pool_timeout=settings.DB_POOL_TIMEOUT,
        # P1-1: Configure asyncpg statement cache for better performance
        # Default 100 statements, 1KB max - increase for complex workloads
        connect_args={
            "statement_cache_size": getattr(
                settings, "DB_STATEMENT_CACHE_SIZE", 100
            ),  # Configurable statement cache size for pg_bouncer/asyncpg usage
            "max_cacheable_statement_size": 10240,  # 10KB max statement size
        },
    )

# Setup query profiler in development mode
if settings.QUERY_PROFILER_ENABLED and is_profiler_enabled(settings.ENVIRONMENT.value):
    setup_query_profiler(
        async_engine,
        slow_query_threshold_ms=settings.QUERY_PROFILER_SLOW_THRESHOLD_MS,
        n_plus_1_threshold=settings.QUERY_PROFILER_N_PLUS_1_THRESHOLD,
        detailed_logs=settings.QUERY_PROFILER_DETAILED_LOGS,
    )

local_session = async_sessionmaker(bind=async_engine, class_=AsyncSession, expire_on_commit=False)

# Register RLS event listener
# We listen to the underlying sync Session class because AsyncSession wraps it
# and events are dispatched from the inner session.
# M8: Add idempotency check to prevent duplicate listener registration during testing
try:
    if not event.contains(Session, "after_begin", restore_rls_context):
        event.listen(Session, "after_begin", restore_rls_context)
except Exception as e:
    logger.warning("Failed to check or register RLS event listener", error=e)

# Alias for compatibility with code expecting async_session_maker
async_session_maker = local_session


async def async_get_db() -> AsyncGenerator[AsyncSession, None]:
    """Get database session with RLS context and proper cleanup.

    Yields a database session with RLS context automatically set for the current tenant.
    Ensures proper rollback on exceptions and session cleanup to prevent connection pool exhaustion.

    Yields:
        AsyncSession: Database session with RLS context set

    Example:
        >>> async def get_user(db: AsyncSession = Depends(async_get_db)):
        ...     return await crud_users.get(db, id=1)
    """
    db = local_session()
    try:
        tenant = get_current_tenant()
        if tenant:
            # Apply RLS context for the current tenant
            async with rls_context(db, str(tenant.id)):
                yield db
        else:
            yield db
    except Exception as exc:
        # BUG FIX: Wrap rollback in try-except to prevent masking original exception
        try:
            await db.rollback()
        except Exception as rb_exc:
            logger.error("Database rollback failed", exc_info=rb_exc)

        tenant = get_current_tenant()
        tenant_context = str(tenant.id) if tenant else "None"
        logger.error("Database transaction failed, rolling back", tenant=tenant_context, exc_info=exc)
        raise
    finally:
        # Always close session to prevent connection pool exhaustion
        await db.close()


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """Get database session with RLS context for manual context manager usage.

    Use this when you need to use `async with` syntax outside of FastAPI dependency injection.
    For FastAPI route handlers, use async_get_db with Depends() instead.

    Yields:
        AsyncSession: Database session with RLS context set

    Example:
        >>> async with get_db_context() as db:
        ...     result = await db.execute(select(User))
        ...     return result.scalars().all()
    """
    db = local_session()
    try:
        tenant = get_current_tenant()
        if tenant:
            # Apply RLS context for the current tenant
            async with rls_context(db, str(tenant.id)):
                yield db
        else:
            yield db
    except Exception as exc:
        # BUG FIX: Wrap rollback in try-except to prevent masking original exception
        try:
            await db.rollback()
        except Exception as rb_exc:
            logger.error("Database rollback failed", exc_info=rb_exc)

        tenant = get_current_tenant()
        tenant_context = str(tenant.id) if tenant else "None"
        logger.error("Database transaction failed, rolling back", tenant=tenant_context, exc_info=exc)
        raise
    finally:
        # Always close session to prevent connection pool exhaustion
        await db.close()
