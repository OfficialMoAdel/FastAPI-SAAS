import os
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.asyncio.session import AsyncSession
from sqlalchemy.orm import DeclarativeBase, MappedAsDataclass, declared_attr
from sqlalchemy.pool import NullPool

from src.app.core.db.rls import rls_context
from src.app.core.tenancy.context import get_tenant_context

from ..config import settings


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
        poolclass=NullPool,  # Critical: fresh connection per use in tests
    )
else:
    async_engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        future=True,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_pre_ping=True,  # Verify connections before use
        pool_recycle=3600,  # Recycle connections after 1 hour
        pool_timeout=settings.DB_POOL_TIMEOUT,
    )

local_session = async_sessionmaker(bind=async_engine, class_=AsyncSession, expire_on_commit=False)

# Alias for compatibility with code expecting async_session_maker
async_session_maker = local_session


async def async_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with local_session() as db:
        try:
            tenant = get_tenant_context()
            if tenant:
                # Apply RLS context for the current tenant
                async with rls_context(db, str(tenant.id)):
                    yield db
            else:
                yield db
        except Exception:
            raise
