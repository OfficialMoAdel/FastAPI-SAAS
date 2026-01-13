from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.asyncio.session import AsyncSession
from sqlalchemy.orm import DeclarativeBase, MappedAsDataclass, declared_attr

from ..config import settings


class Base(DeclarativeBase, MappedAsDataclass):
    """Base class for all SQLAlchemy models.

    Includes extend_existing=True to prevent duplicate table registration
    errors during alembic migrations when models are imported from multiple paths.
    """

    @declared_attr.directive
    def __table_args__(cls) -> dict:
        return {"extend_existing": True}


DATABASE_URI = settings.POSTGRES_URI
DATABASE_PREFIX = settings.POSTGRES_ASYNC_PREFIX
# DATABASE_URI = settings.SQLITE_URI
# DATABASE_PREFIX = settings.SQLITE_ASYNC_PREFIX
DATABASE_URL = f"{DATABASE_PREFIX}{DATABASE_URI}"


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
        yield db
