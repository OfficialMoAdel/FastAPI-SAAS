import os
import sys
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    async_engine: AsyncEngine
    local_session: async_sessionmaker[AsyncSession]
    async_session_maker: async_sessionmaker[AsyncSession]
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

# Internal state for lazy initialization
_async_engine = None
_local_session = None
_init_lock = threading.RLock()  # RLock: reentrant, prevents deadlock when _get_local_session calls __getattr__


def _create_engine() -> AsyncEngine:
    """Internal helper to create the SQLAlchemy engine with proper settings."""
    # Use NullPool in test mode to prevent event loop binding issues
    # NullPool creates fresh connections per use, avoiding "Event loop is closed" errors
    _is_testing = (
        "pytest" in os.environ.get("_", "")
        or "PYTEST_CURRENT_TEST" in os.environ
        or os.environ.get("TESTING", "").lower() in ("1", "true", "yes")
        or "pytest" in sys.modules
    )

    if _is_testing:
        engine = create_async_engine(
            DATABASE_URL,
            echo=False,
            future=True,
            poolclass=NullPool,
        )
    else:
        engine = create_async_engine(
            DATABASE_URL,
            echo=False,
            future=True,
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_timeout=settings.DB_POOL_TIMEOUT,
            # P1-1: Configure asyncpg statement cache for better performance
            connect_args={
                "statement_cache_size": getattr(settings, "DB_STATEMENT_CACHE_SIZE", 100),
                "max_cacheable_statement_size": 10240,  # 10KB max statement size
            },
        )

    # Setup query profiler in development mode
    if settings.QUERY_PROFILER_ENABLED and is_profiler_enabled(settings.ENVIRONMENT.value):
        setup_query_profiler(
            engine,
            slow_query_threshold_ms=settings.QUERY_PROFILER_SLOW_THRESHOLD_MS,
            n_plus_1_threshold=settings.QUERY_PROFILER_N_PLUS_1_THRESHOLD,
            detailed_logs=settings.QUERY_PROFILER_DETAILED_LOGS,
        )

    # Register RLS event listener
    # M8: Add idempotency check to prevent duplicate listener registration
    try:
        if not event.contains(Session, "after_begin", restore_rls_context):
            event.listen(Session, "after_begin", restore_rls_context)
    except Exception as e:
        logger.warning("Failed to check or register RLS event listener", error=e)

    return engine


def _get_local_session() -> async_sessionmaker[AsyncSession]:
    """Internal helper to get or create the local session maker."""
    global _local_session
    if _local_session is None:
        with _init_lock:
            # Double-check pattern to prevent race condition
            if _local_session is None:
                # Ensure engine is created first
                engine = __getattr__("async_engine")
                _local_session = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    return _local_session


def __getattr__(name: str) -> Any:
    """Lazy-load engine and session maker to prevent connection leaks during module import.

    This ensures that database connections are only initialized when actually needed,
    avoiding leaks during configuration validation or middleware setup.
    """
    global _async_engine

    if name == "async_engine":
        if _async_engine is None:
            with _init_lock:
                # Double-check pattern to prevent race condition
                if _async_engine is None:
                    _async_engine = _create_engine()
        return _async_engine

    if name in ("local_session", "async_session_maker"):
        return _get_local_session()

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    session_maker = _get_local_session()
    db = session_maker()
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
    session_maker = _get_local_session()
    db = session_maker()
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
