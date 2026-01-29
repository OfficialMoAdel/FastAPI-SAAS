"""Shared fixtures for R1-UNIFIED unit tests."""
import asyncio
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.app.core.db.database import Base


@pytest.fixture(scope="function")
async def redis_client() -> AsyncGenerator[Redis, None]:
    """Redis client with automatic cleanup.

    Uses test database (15) to avoid conflicts with development data.
    Flushes database before and after each test for isolation.
    """
    try:
        client = Redis.from_url("redis://localhost:6379/15", decode_responses=False)
        await client.ping()
        await client.flushdb()  # Clean before test
        yield client
        await client.flushdb()  # Clean after test
        await client.aclose()
    except Exception:
        # If Redis is not available, skip tests that require it
        pytest.skip("Redis not available")


@pytest.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Database session with transaction rollback.

    Uses in-memory SQLite for fast, isolated tests.
    Automatically rolls back after each test.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        async with session.begin():
            yield session
            await session.rollback()  # Rollback after test

    await engine.dispose()


@pytest.fixture
def mock_redis_client() -> AsyncMock:
    """Mock Redis client for unit tests without real Redis.

    Useful for testing logic without external dependencies.
    """
    mock = AsyncMock(spec=Redis)
    mock.get = AsyncMock(return_value=None)
    mock.set = AsyncMock(return_value=True)
    mock.setex = AsyncMock(return_value=True)
    mock.delete = AsyncMock(return_value=1)
    mock.exists = AsyncMock(return_value=0)
    mock.ttl = AsyncMock(return_value=-1)
    mock.incr = AsyncMock(return_value=1)  # For event sequence numbers
    mock.pipeline = MagicMock()
    mock.evalsha = AsyncMock(return_value=None)
    mock.script_load = AsyncMock(return_value=b"mock_sha")
    return mock


@pytest.fixture
def mock_db_session() -> AsyncMock:
    """Mock database session for unit tests without real database."""
    mock = AsyncMock(spec=AsyncSession)
    mock.execute = AsyncMock()
    mock.commit = AsyncMock()
    mock.rollback = AsyncMock()
    mock.close = AsyncMock()
    return mock


@pytest.fixture
def event_loop():
    """Event loop for async tests.

    Creates a fresh event loop for each test to ensure isolation.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def reset_metrics():
    """Reset R1-UNIFIED metrics before each test.

    Ensures metrics don't leak between tests.
    """
    from src.app.core.monitoring.r1_unified_metrics import reset_r1_metrics

    reset_r1_metrics()
    yield
    reset_r1_metrics()


@pytest.fixture
def mock_settings():
    """Mock settings with R1-UNIFIED feature flags enabled.

    Useful for testing optimized code paths.
    """
    from unittest.mock import MagicMock

    settings = MagicMock()
    settings.r1_unified.PASSKEY_USE_COLUMN_SELECT = True
    settings.r1_unified.PASSKEY_MAX_CREDENTIALS_PER_USER = 100
    settings.r1_unified.PASSKEY_DECRYPT_CONCURRENCY_LIMIT = 10
    settings.r1_unified.CIRCUIT_BREAKER_USE_DOUBLE_CHECK = True
    settings.r1_unified.RBAC_USE_REDIS_CACHE = True
    settings.r1_unified.RBAC_CACHE_TTL_SECONDS = 3600
    settings.r1_unified.DLQ_USE_BOUNDED_SIZE = True
    settings.r1_unified.DLQ_MAX_SIZE = 10000
    settings.r1_unified.DLQ_ALERT_THRESHOLD_PCT = 80
    settings.r1_unified.SESSION_MAX_EVICTION_BATCH_SIZE = 50
    settings.r1_unified.IDEMPOTENCY_TTL_SECONDS = 300
    settings.r1_unified.EVENT_BUS_USE_LUA_IDEMPOTENCY = True
    settings.EVENT_HANDLER_TIMEOUT_SECONDS = 30
    return settings
