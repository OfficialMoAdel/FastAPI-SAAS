"""Integration test fixtures for performance optimization validation.

Provides real Redis and PostgreSQL connections for integration testing.
"""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
import structlog
from redis.asyncio import Redis
from sqlalchemy import event as sqla_event
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.app.core.config import settings

logger = structlog.get_logger(__name__)


# -----------------------------------------------------------------------------
# Baseline Performance Metrics (from task description)
# -----------------------------------------------------------------------------
@pytest.fixture
def baseline_metrics() -> dict[str, Any]:
    """Baseline metrics for comparison testing."""
    return {
        # Latency baselines
        "p50_latency_ms": 45,
        "p95_latency_ms": 180,
        "p99_latency_ms": 450,
        # Redis baselines
        "redis_ops_per_sec": 2400,
        "redis_roundtrips_cache_user": 3,  # Before pipeline
        # DB baselines
        "db_pool_utilization": 0.85,
        "query_time_user_with_relations_ms": 15,  # Before joinedload
    }


# -----------------------------------------------------------------------------
# Database Fixtures
# -----------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="session")
async def real_postgres_engine():
    """Create real PostgreSQL engine for integration tests."""
    # Use test database URL (port 5433 for test container)
    test_db_url = str(settings.POSTGRES_URI).replace(":5432", ":5433")
    if "test" not in test_db_url:
        test_db_url = test_db_url.replace("/postgres", "/test_db")

    engine = create_async_engine(
        test_db_url,
        echo=True,  # Log all SQL
        pool_size=5,
        max_overflow=10,
    )

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture
async def real_postgres(real_postgres_engine) -> AsyncGenerator[AsyncSession, None]:
    """Real PostgreSQL session for integration tests."""
    async_session = sessionmaker(
        real_postgres_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session() as session:
        yield session
        await session.rollback()


@pytest.fixture
def query_counter():
    """Counter for tracking SQL query count."""

    class QueryCounter:
        def __init__(self):
            self.count = 0
            self.queries: list[str] = []

        def increment(self, query: str):
            self.count += 1
            self.queries.append(query)

        def reset(self):
            self.count = 0
            self.queries.clear()

    return QueryCounter()


@pytest.fixture
def query_logger(real_postgres, query_counter):
    """Log all SQL queries for N+1 detection."""
    engine = real_postgres.get_bind()

    @sqla_event.listens_for(engine.sync_engine, "before_cursor_execute")
    def log_query(conn, cursor, statement, parameters, context, executemany):
        query_counter.increment(statement)
        logger.info(
            "sql_query",
            statement=statement[:200],
            params=str(parameters)[:100],
        )

    yield query_counter

    sqla_event.remove(engine.sync_engine, "before_cursor_execute", log_query)


@pytest_asyncio.fixture
async def clean_db(real_postgres):
    """Clean database state between tests."""
    # Truncate relevant tables
    tables = ["users", "tenants", "rate_limits", "tiers", "storage_settings"]
    for table in tables:
        try:
            await real_postgres.execute(text(f"TRUNCATE {table} CASCADE"))
        except Exception:
            pass  # Table may not exist
    await real_postgres.commit()


# -----------------------------------------------------------------------------
# Redis Fixtures
# -----------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="session")
async def real_redis() -> AsyncGenerator[Redis, None]:
    """Real Redis connection for integration tests."""
    # Use test Redis (port 6380 for test container)
    redis = Redis(
        host=settings.REDIS_HOST,
        port=6380,  # Test container port
        db=0,
        decode_responses=True,
    )

    yield redis

    await redis.aclose()


@pytest_asyncio.fixture
async def clean_redis(real_redis):
    """Clean Redis state between tests."""
    await real_redis.flushdb()
    yield real_redis


@pytest.fixture
def redis_command_logger():
    """Capture Redis commands for verification."""

    class RedisCommandLogger:
        def __init__(self):
            self.commands: list[dict[str, Any]] = []

        def log(self, command: str, *args):
            self.commands.append({"command": command, "args": args})
            logger.info("redis_command", command=command, args=args[:3])

        def count(self, command: str) -> int:
            return sum(1 for c in self.commands if c["command"] == command)

        def reset(self):
            self.commands.clear()

    return RedisCommandLogger()


# -----------------------------------------------------------------------------
# Performance Measurement Fixtures
# -----------------------------------------------------------------------------
@pytest.fixture
def perf_timer():
    """Performance timing helper."""
    import time

    class PerfTimer:
        def __init__(self):
            self.start_time = None
            self.elapsed_ms = 0

        def start(self):
            self.start_time = time.perf_counter()

        def stop(self) -> float:
            if self.start_time:
                self.elapsed_ms = (time.perf_counter() - self.start_time) * 1000
            return self.elapsed_ms

        def __enter__(self):
            self.start()
            return self

        def __exit__(self, *args):
            self.stop()

    return PerfTimer()


@pytest.fixture
def baseline_comparator(baseline_metrics):
    """Compare measured values against baselines."""

    class BaselineComparator:
        def __init__(self, baselines: dict[str, Any]):
            self.baselines = baselines

        def assert_improved(
            self,
            metric_name: str,
            actual_value: float,
            improvement_threshold: float = 0.2,
        ):
            """Assert actual value is improved over baseline by threshold."""
            baseline = self.baselines.get(metric_name)
            if baseline is None:
                pytest.skip(f"No baseline for {metric_name}")

            max_allowed = baseline * (1 - improvement_threshold)
            logger.info(
                "baseline_comparison",
                metric=metric_name,
                baseline=baseline,
                actual=actual_value,
                max_allowed=max_allowed,
                improvement_percent=(1 - actual_value / baseline) * 100,
            )
            assert actual_value <= max_allowed, (
                f"{metric_name}: {actual_value:.2f} not improved by {improvement_threshold * 100}% "
                f"over baseline {baseline}"
            )

        def log_comparison(self, metric_name: str, actual_value: float):
            """Log comparison without assertion."""
            baseline = self.baselines.get(metric_name, 0)
            improvement = (1 - actual_value / baseline) * 100 if baseline else 0
            logger.info(
                "baseline_comparison",
                metric=metric_name,
                baseline=baseline,
                actual=actual_value,
                improvement_percent=improvement,
            )

    return BaselineComparator(baseline_metrics)


# -----------------------------------------------------------------------------
# Test Data Factories
# -----------------------------------------------------------------------------
@pytest.fixture
def user_factory():
    """Factory for creating test users."""
    from uuid import uuid4

    def create(
        username: str | None = None,
        email: str | None = None,
        tenant_id: int = 1,
    ) -> dict[str, Any]:
        uid = uuid4().hex[:8]
        return {
            "username": username or f"user_{uid}",
            "email": email or f"user_{uid}@example.com",
            "name": f"Test User {uid}",
            "tenant_id": tenant_id,
            "hashed_password": "hashed_password_placeholder",
            "uuid": uuid4(),
        }

    return create


@pytest.fixture
def tenant_factory():
    """Factory for creating test tenants."""
    from uuid import uuid4

    def create(name: str | None = None, slug: str | None = None) -> dict[str, Any]:
        uid = uuid4().hex[:8]
        return {
            "name": name or f"Tenant {uid}",
            "slug": slug or f"tenant-{uid}",
            "uuid": uuid4(),
        }

    return create


# -----------------------------------------------------------------------------
# Markers and Skip Conditions
# -----------------------------------------------------------------------------
def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests (require real services)",
    )
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow (>1s execution time)",
    )


@pytest.fixture(autouse=True)
def skip_without_services(request):
    """Skip integration tests if services unavailable."""
    if "integration" in [m.name for m in request.node.iter_markers()]:
        # Check if Redis is available
        try:
            import socket

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(("localhost", 6380))
            sock.close()
            if result != 0:
                pytest.skip("Redis test container not available on port 6380")
        except Exception:
            pytest.skip("Cannot check Redis availability")
