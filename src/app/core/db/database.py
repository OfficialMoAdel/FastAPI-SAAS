import os
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
from src.app.core.tenancy.context import get_current_tenant


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
# DATABASE_URI = settings.SQLITE_URI
# DATABASE_PREFIX = settings.SQLITE_ASYNC_PREFIX
DATABASE_URL = f"{DATABASE_PREFIX}{DATABASE_URI}"

# Use NullPool in test mode to prevent event loop binding issues
# NullPool creates fresh connections per use, avoiding "Event loop is closed" errors
_is_testing = (
    "pytest" in os.environ.get("_", "")
    or "PYTEST_CURRENT_TEST" in os.environ
    or os.environ.get("TESTING", "").lower() in ("1", "true", "yes")
    or "pytest" in __import__("sys").modules
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
event.listen(Session, "after_begin", restore_rls_context)

# Alias for compatibility with code expecting async_session_maker
async_session_maker = local_session


@asynccontextmanager
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
    except Exception:
        # Rollback on exception before re-raising
        await db.rollback()
        raise
    finally:
        # Always close session to prevent connection pool exhaustion
        await db.close()