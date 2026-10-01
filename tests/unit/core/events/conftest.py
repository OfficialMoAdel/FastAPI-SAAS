"""Shared fixtures for R1-UNIFIED unit tests."""

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


def make_pipeline_ctx(pipe: MagicMock) -> MagicMock:
    """Wrap *pipe* in an async context manager so it works with ``async with``."""
    ctx = MagicMock()

    async def _aenter(*args):
        return pipe

    async def _aexit(*args):
        return False

    ctx.__aenter__ = _aenter
    ctx.__aexit__ = _aexit
    return ctx


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
    mock.eval = AsyncMock(return_value=[])

    # Default pipeline: returns an async context manager wrapping an AsyncMock pipe.
    # Individual tests may override: mock_redis_client.pipeline = MagicMock(return_value=make_pipeline_ctx(my_pipe))
    _default_pipe = MagicMock()
    _default_pipe.execute = AsyncMock(return_value=[])
    mock.pipeline = MagicMock(return_value=make_pipeline_ctx(_default_pipe))

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


# NOTE: event_loop fixture REMOVED - pytest-asyncio 0.23+ handles this via
# pyproject.toml settings (asyncio_default_fixture_loop_scope = "session").
# Custom event_loop fixtures conflict with the global config and cause
# test isolation failures during coverage runs.


@pytest.fixture(autouse=True)
def reset_metrics():
    """Reset R1-UNIFIED metrics before each test.

    Ensures metrics don't leak between tests.
    """
    from src.app.core.monitoring import reset_r1_metrics

    reset_r1_metrics()
    yield
    reset_r1_metrics()


@pytest.fixture
def mock_settings():
    """Mock settings with R1-UNIFIED feature flags enabled.

    Useful for testing optimized code paths.
    """
    from unittest.mock import MagicMock

    class MockComponent(MagicMock):
        pass

    r1_mock = MockComponent()
    r1_mock.PASSKEY_USE_COLUMN_SELECT = True
    r1_mock.PASSKEY_MAX_CREDENTIALS_PER_USER = 100
    r1_mock.PASSKEY_DECRYPT_CONCURRENCY_LIMIT = 10
    r1_mock.CIRCUIT_BREAKER_USE_DOUBLE_CHECK = True
    r1_mock.RBAC_USE_REDIS_CACHE = True
    r1_mock.RBAC_CACHE_TTL_SECONDS = 3600
    r1_mock.DLQ_MAX_SIZE = 10000
    r1_mock.DLQ_ALERT_THRESHOLD_PCT = 80
    r1_mock.SESSION_MAX_EVICTION_BATCH_SIZE = 50
    r1_mock.IDEMPOTENCY_TTL_SECONDS = 300
    r1_mock.EVENT_BUS_USE_LUA_IDEMPOTENCY = True

    # EVENT_HANDLER_TIMEOUT_SECONDS lives on the `events` component, not
    # `r1_unified` (H-7: verified against the live delegation map). The proxy
    # modelled only `r1_unified`, so reading `settings.events.X` returned a
    # bare MagicMock, the handler timeout was never a number, and every
    # handler "timed out" instead of running. Both components are modelled so
    # the double matches the real read path.
    events_mock = MockComponent()
    events_mock.EVENT_HANDLER_TIMEOUT_SECONDS = 30

    class MockSettingsProxy:
        def __init__(self, r1, events=None):
            self.r1_unified = r1
            self.events = events if events is not None else MockComponent()

        def __getattr__(self, name):
            if hasattr(self.r1_unified, name):
                return getattr(self.r1_unified, name)
            return getattr(self.r1_unified, name)

        def __setattr__(self, name, value):
            if name in ("r1_unified", "events"):
                object.__setattr__(self, name, value)
            else:
                setattr(self.r1_unified, name, value)

    settings = MockSettingsProxy(r1_mock, events_mock)
    return settings
