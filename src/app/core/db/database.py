import os
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
from sqlalchemy.orm import DeclarativeBase, MappedAsDataclass
from sqlalchemy.pool import NullPool

from src.app.core.config import settings
from src.app.core.db.rls import restore_rls_context
from src.app.core.logger import get_logger
from src.app.core.observability.tracing import trace_span
from src.app.core.tenancy.context import get_current_tenant
from src.app.shared.exceptions import AppException

logger = get_logger(__name__)


class Base(DeclarativeBase, MappedAsDataclass):
    """Base class for all SQLAlchemy models.

    R0-06: ``extend_existing=True`` was removed from the declarative base.
    It masked duplicate table registration (the same ``__tablename__``
    defined or imported via two different module paths) instead of failing
    fast. If a genuine duplicate appears, fix the import path rather than
    re-enabling the flag.

    Subclasses may define their own ``__table_args__`` as tuples containing
    Index/Constraint objects plus an optional dict, e.g.:
    ``(Index(...), {"extend_existing": True})`` — per-table only, with cause.
    """


from datetime import UTC, datetime


@event.listens_for(Base, "before_update", propagate=True)
def receive_before_update(mapper: Any, connection: Any, target: Any) -> None:
    """Ensure updated_at is always updated during session.commit() operations."""
    if hasattr(target, "updated_at"):
        target.updated_at = datetime.now(UTC)


def get_database_url() -> str:
    """Get the database URL dynamically to support test patching."""
    return f"{settings.postgres.POSTGRES_ASYNC_PREFIX}{settings.postgres.POSTGRES_URI}"


# P1-1: lock for the check-then-act race on first engine / session
# creation under burst startup.  Used as a synchronous mutex (the
# initialization is itself synchronous — it doesn't await), so a
# ``threading.Lock`` is the correct primitive (an ``asyncio.Lock`` here
# would deadlock because callers may not be in an async context).
import threading

_init_lock = threading.RLock()
_async_engine = None
_local_session = None

def _create_engine() -> AsyncEngine:
    """Internal helper to create the SQLAlchemy engine with proper settings."""
    # Use NullPool in test mode to prevent event loop binding issues
    # NullPool creates fresh connections per use, avoiding "Event loop is closed" errors
    _is_testing = "PYTEST_CURRENT_TEST" in os.environ or os.environ.get("TESTING", "").lower() in ("1", "true", "yes")

    if _is_testing:
        engine = create_async_engine(
            get_database_url(),
            echo=False,
            future=True,
            poolclass=NullPool,
        )
    else:
        engine = create_async_engine(
            get_database_url(),
            echo=False,
            future=True,
            pool_size=settings.postgres.DB_POOL_SIZE,
            max_overflow=settings.postgres.DB_MAX_OVERFLOW,
            # H-2: pool_pre_ping removed. pre_ping + the RLS checkout listener
            # fired 2 guaranteed RTTs before every real query (~5-10% p50 tax,
            # −30-40% effective pool throughput). Stale/broken connections are
            # still covered: pool_recycle=3600 bounds connection lifetime, and
            # asyncpg's TCP keepalive surfaces dead sockets on first use.
            # The RLS re-assertion on checkout (restore_rls_context) is
            # INTENTIONALLY KEPT — see rls.py:fail-closed invariant.
            pool_recycle=3600,
            pool_timeout=settings.postgres.DB_POOL_TIMEOUT,
            # P1-1: Configure asyncpg statement cache for better performance
            connect_args={
                "statement_cache_size": settings.postgres.DB_STATEMENT_CACHE_SIZE,
                "max_cacheable_statement_size": 10240,  # 10KB max statement size
            },
        )

    # Query profiling is NOT wired here, and that is deliberate (muse 12).
    #
    # This used to call `core/db/profiler.setup_query_profiler(engine, ...)`
    # when QUERY_PROFILER_ENABLED was set. That module registered its own
    # before/after cursor-execute pair on the same engine as
    # QueryProfilerMiddleware, behind the same flag, so every query was timed
    # twice and every slow query and N+1 finding reported twice.
    #
    # The two were MERGED rather than one being deleted: QueryProfilerMiddleware
    # now owns the single listener pair and calls
    # `core.db.profiler.track_global_query()` on every query, so the
    # cross-request view (patterns that recur across requests and background
    # tasks, which per-request QueryStats cannot see) is preserved. The second
    # attachment point is removed, not the capability. Pinned by
    # tests/unit/core/db/test_single_profiler.py, which fails if the engine
    # ever carries more than one listener per cursor-execute event.

    # Register RLS event listener
    # M8: Add idempotency check to prevent duplicate listener registration
    try:
        if not event.contains(engine.sync_engine, "checkout", restore_rls_context):
            event.listen(engine.sync_engine, "checkout", restore_rls_context)
    except Exception as e:
        logger.critical(
            "CRITICAL: Failed to register RLS event listener. Application cannot start without tenant isolation.",
            error=e,
        )
        raise RuntimeError("RLS event listener registration failed") from e

    return engine


def get_async_engine() -> AsyncEngine:
    """Get or create the async engine.

    P1-1: double-checked under ``_init_lock`` so a burst-startup race can
    never build two engines (the second assignment would orphan the first
    pool for the process lifetime).
    """
    global _async_engine
    if _async_engine is not None:
        return _async_engine
    with _init_lock:
        if _async_engine is None:
            _async_engine = _create_engine()
    return _async_engine


def _get_local_session() -> async_sessionmaker[AsyncSession]:
    """Internal helper to get or create the local session maker.

    P1-1: same double-checked lock as ``get_async_engine`` for the
    sessionmaker singleton.
    """
    global _local_session
    if _local_session is not None:
        return _local_session
    with _init_lock:
        if _local_session is None:
            # Ensure engine is created first
            engine = get_async_engine()
            _local_session = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    return _local_session


def __getattr__(name: str) -> Any:
    """Lazy-load engine and session maker to prevent connection leaks during module import.

    This ensures that database connections are only initialized when actually needed,
    avoiding leaks during configuration validation or middleware setup.
    """
    if name == "async_engine":
        return get_async_engine()

    if name in ("local_session", "async_session_maker"):
        return _get_local_session()

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


async def async_get_db() -> AsyncGenerator[AsyncSession]:
    """Get a database session with proper cleanup.

    T6 FIX (docstring honesty): this dependency does NOT itself set RLS
    context variables. Tenant/user RLS vars are applied by TenantMiddleware
    and by the restore_rls_context pool-checkout listener using the tenant
    ContextVar; ``app.current_user_id`` is currently never populated, so
    user-scoped RLS policies are inert. Do not rely on this dependency for
    per-user row filtering - use application-layer filters.

    Ensures proper rollback on exceptions and session cleanup to prevent connection pool exhaustion.

    Yields:
        AsyncSession: Database session

    Example:
        >>> async def get_user(db: AsyncSession = Depends(async_get_db)):
        ...     return await crud_users.get(db, id=1)
    """
    with trace_span("database_session"):
        session_maker = _get_local_session()
        db = session_maker()
        try:
            yield db
        except Exception as exc:
            # BUG FIX: Wrap rollback in try-except to prevent masking original exception
            try:
                await db.rollback()
            except Exception as rb_exc:
                logger.error("Database rollback failed", exc_info=rb_exc)

            tenant = get_current_tenant()
            tenant_context = str(tenant.id) if tenant else "None"
            # An AppException is an expected, deliberate rejection that
            # core/errors.py turns into a response, so no transaction failed and
            # the rollback above was routine. Logging it at error level with a
            # rendered traceback was both wrong and, per bead FastAPI_saas-1py8,
            # 345 us per call and 99.72% of the event loop's CPU on the 401
            # path. A genuine driver failure is not an AppException and still
            # reaches the log below.
            if not isinstance(exc, AppException):
                logger.error("Database transaction failed, rolling back", tenant=tenant_context, exc_info=exc)
            raise
        finally:
            # Always close session to prevent connection pool exhaustion
            await db.close()


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession]:
    """Get a database session for manual context manager usage.

    T6 FIX (docstring honesty): like :func:`async_get_db`, this does NOT set
    RLS context itself; see that function's docstring.

    Use this when you need to use `async with` syntax outside of FastAPI dependency injection.
    For FastAPI route handlers, use async_get_db with Depends() instead.

    Yields:
        AsyncSession: Database session

    Example:
        >>> async with get_db_context() as db:
        ...     result = await db.execute(select(User))
        ...     return result.scalars().all()
    """
    with trace_span("database_session_context"):
        session_maker = _get_local_session()
        db = session_maker()
        try:
            yield db
        except Exception as exc:
            # BUG FIX: Wrap rollback in try-except to prevent masking original exception
            try:
                await db.rollback()
            except Exception as rb_exc:
                logger.error("Database rollback failed", exc_info=rb_exc)

            tenant = get_current_tenant()
            tenant_context = str(tenant.id) if tenant else "None"
            # An AppException is an expected, deliberate rejection that
            # core/errors.py turns into a response, so no transaction failed and
            # the rollback above was routine. Logging it at error level with a
            # rendered traceback was both wrong and, per bead FastAPI_saas-1py8,
            # 345 us per call and 99.72% of the event loop's CPU on the 401
            # path. A genuine driver failure is not an AppException and still
            # reaches the log below.
            if not isinstance(exc, AppException):
                logger.error("Database transaction failed, rolling back", tenant=tenant_context, exc_info=exc)
            raise
        finally:
            # Always close session to prevent connection pool exhaustion
            await db.close()
