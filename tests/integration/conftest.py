"""Integration test fixtures for performance optimization validation.

Provides real Redis and PostgreSQL connections for integration testing.
"""

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
# Event Loop Configuration - CRITICAL for session-scoped async fixtures
# -----------------------------------------------------------------------------
# NOTE: pytest-asyncio >= 0.23 uses loop_scope parameter on fixtures instead
# of the deprecated event_loop fixture. See real_postgres_engine and real_redis.


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
@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def real_postgres_engine():
    """Create real PostgreSQL engine for integration tests."""
    # Build full async URL with scheme prefix
    # Use TEST_POSTGRES_PORT env var if set, otherwise use main POSTGRES_PORT
    import os

    test_port = os.getenv("TEST_POSTGRES_PORT", str(settings.POSTGRES_PORT))
    test_db = os.getenv("TEST_POSTGRES_DB", "test_db")

    base_uri = str(settings.POSTGRES_URI)
    # Replace port if TEST_POSTGRES_PORT is different
    if test_port != str(settings.POSTGRES_PORT):
        base_uri = base_uri.replace(f":{settings.POSTGRES_PORT}", f":{test_port}")
    # Replace database name with test database
    base_uri = base_uri.replace(f"/{settings.POSTGRES_DB}", f"/{test_db}")

    # Add async scheme prefix
    test_db_url = f"{settings.POSTGRES_ASYNC_PREFIX}{base_uri}"

    engine = create_async_engine(
        test_db_url,
        echo=True,  # Log all SQL
        pool_size=5,
        max_overflow=10,
    )

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
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


@pytest_asyncio.fixture(loop_scope="session")
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
@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def real_redis() -> AsyncGenerator[Redis, None]:
    """Real Redis connection for integration tests."""
    import os

    # Use TEST_REDIS_PORT env var if set, otherwise use main REDIS_CACHE_PORT
    test_port = int(os.getenv("TEST_REDIS_PORT", str(settings.REDIS_CACHE_PORT)))
    test_password = os.getenv("TEST_REDIS_PASSWORD", settings.REDIS_CACHE_PASSWORD)

    redis = Redis(
        host=settings.REDIS_CACHE_HOST,
        port=test_port,
        password=test_password if test_password else None,
        db=0,
        decode_responses=True,
    )

    yield redis

    # Handle event loop closed during teardown (common with session-scoped fixtures)
    try:
        await redis.aclose()
    except RuntimeError:
        # Event loop may be closed during test session teardown
        pass


@pytest_asyncio.fixture(loop_scope="session")
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


# -----------------------------------------------------------------------------
# RLS Test Fixtures
# -----------------------------------------------------------------------------
@pytest_asyncio.fixture
async def db_session(real_postgres):
    """Alias for real_postgres session."""
    yield real_postgres


@pytest_asyncio.fixture
async def test_posts(real_postgres, tenant_factory, user_factory):
    """Create test posts for RLS testing - two posts in different tenants."""
    from uuid import uuid4
    from src.app.modules.posts.models import Post
    from src.app.modules.tenants.models import Tenant
    from src.app.modules.users.models import User
    from src.app.modules.tiers.models import Tier

    # Create tiers first
    tier_a = Tier(
        id=1,
        uuid=uuid4(),
        name="Free",
        max_users=10,
        max_storage_gb=1,
        price_monthly=0,
        price_yearly=0,
    )
    tier_b = Tier(
        id=2,
        uuid=uuid4(),
        name="Pro",
        max_users=100,
        max_storage_gb=10,
        price_monthly=10,
        price_yearly=100,
    )

    try:
        real_postgres.add(tier_a)
        real_postgres.add(tier_b)
        await real_postgres.flush()
    except Exception:
        await real_postgres.rollback()
        # Tiers may already exist
        pass

    # Create tenants
    tenant_a_data = tenant_factory(name="Tenant A", slug="tenant-a")
    tenant_b_data = tenant_factory(name="Tenant B", slug="tenant-b")

    tenant_a = Tenant(**tenant_a_data, tier_id=1)
    tenant_b = Tenant(**tenant_b_data, tier_id=2)
    real_postgres.add(tenant_a)
    real_postgres.add(tenant_b)
    await real_postgres.flush()

    # Create users
    user_a_data = user_factory(tenant_id=tenant_a.id)
    user_b_data = user_factory(tenant_id=tenant_b.id)

    user_a = User(**user_a_data)
    user_b = User(**user_b_data)
    real_postgres.add(user_a)
    real_postgres.add(user_b)
    await real_postgres.flush()

    # Create posts
    post_a = Post(
        title="Post in Tenant A",
        text="Content A",
        uuid=uuid4(),
        tenant_id=tenant_a.id,
        user_id=user_a.id,
        created_by_user_id=user_a.id,
    )
    post_b = Post(
        title="Post in Tenant B",
        text="Content B",
        uuid=uuid4(),
        tenant_id=tenant_b.id,
        user_id=user_b.id,
        created_by_user_id=user_b.id,
    )
    real_postgres.add(post_a)
    real_postgres.add(post_b)
    await real_postgres.flush()

    yield (post_a, post_b)

    # Cleanup
    await real_postgres.delete(post_a)
    await real_postgres.delete(post_b)
    await real_postgres.delete(user_a)
    await real_postgres.delete(user_b)
    await real_postgres.delete(tenant_a)
    await real_postgres.delete(tenant_b)
    await real_postgres.commit()


@pytest.fixture
def rls_context():
    """RLS context manager for setting tenant context in database session."""
    from src.app.core.db.database import local_session

    class RLSContext:
        def __init__(self):
            self.current_tenant_id = None

        async def set(self, tenant_id: int):
            """Set RLS context for tenant."""
            self.current_tenant_id = tenant_id
            # In real RLS, this would execute:
            # SET app.current_tenant_id = tenant_id
            # For now, this is a placeholder

        async def clear(self):
            """Clear RLS context."""
            self.current_tenant_id = None

    return RLSContext()


@pytest.fixture(autouse=True)
def skip_without_services(request):
    """Skip integration tests if services unavailable."""
    if "integration" in [m.name for m in request.node.iter_markers()]:
        # Check if Redis is available
        try:
            import os
            import socket

            # Use TEST_REDIS_PORT or main REDIS_CACHE_PORT
            test_port = int(os.getenv("TEST_REDIS_PORT", str(settings.REDIS_CACHE_PORT)))
            redis_host = settings.REDIS_CACHE_HOST

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex((redis_host, test_port))
            sock.close()
            if result != 0:
                pytest.skip(f"Redis not available at {redis_host}:{test_port}")
        except Exception:
            pytest.skip("Cannot check Redis availability")
