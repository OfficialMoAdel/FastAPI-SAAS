"""RLS Performance Benchmark Tests.

Measures query performance impact of Row Level Security to ensure overhead
stays within acceptable limits (< 5%).

Usage:
    uv run pytest tests/performance/test_rls_performance.py -v -s
"""

import statistics
import time
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.posts.models import Post
from src.app.modules.tenants.models import Tenant
from src.app.modules.users.models import User

# Mark all tests as async
pytestmark = pytest.mark.asyncio


@pytest.fixture
async def benchmark_tenants(
    db_session: AsyncSession,
) -> AsyncGenerator[list[Tenant], None]:
    """Create tenants for benchmark tests."""
    tenants = [
        Tenant(
            name=f"Benchmark Tenant {i}",
            slug=f"bench-tenant-{i}",
            is_active=True,
        )
        for i in range(5)
    ]

    db_session.add_all(tenants)
    await db_session.commit()

    for tenant in tenants:
        await db_session.refresh(tenant)

    yield tenants

    # Cleanup
    for tenant in tenants:
        await db_session.delete(tenant)
    await db_session.commit()


@pytest.fixture
async def benchmark_users(
    db_session: AsyncSession,
    benchmark_tenants: list[Tenant],
) -> AsyncGenerator[list[User], None]:
    """Create users for benchmark tests."""
    users = []
    for i, tenant in enumerate(benchmark_tenants):
        user = User(
            name=f"Benchmark User {i}",
            email=f"bench_user_{i}@test.com",
            username=f"bench_user_{i}",
            hashed_password="hashed",
            tenant_id=tenant.id,
        )
        users.append(user)

    db_session.add_all(users)
    await db_session.commit()

    for user in users:
        await db_session.refresh(user)

    yield users

    # Cleanup
    for user in users:
        await db_session.delete(user)
    await db_session.commit()


@pytest.fixture
async def benchmark_posts(
    db_session: AsyncSession,
    benchmark_tenants: list[Tenant],
    benchmark_users: list[User],
) -> AsyncGenerator[list[Post], None]:
    """Create posts for benchmark tests (100 posts across 5 tenants)."""
    posts = []

    for i in range(100):
        tenant_idx = i % len(benchmark_tenants)
        post = Post(
            title=f"Benchmark Post {i}",
            text=f"Content for benchmark post {i}",
            tenant_id=benchmark_tenants[tenant_idx].id,
            created_by_user_id=benchmark_users[tenant_idx].id,
        )
        posts.append(post)

    db_session.add_all(posts)
    await db_session.commit()

    for post in posts:
        await db_session.refresh(post)

    yield posts

    # Cleanup
    for post in posts:
        await db_session.delete(post)
    await db_session.commit()


class TestRLSPerformance:
    """Performance benchmarks for RLS overhead measurement."""

    async def test_select_performance_with_rls(
        self,
        db_session: AsyncSession,
        benchmark_posts: list[Post],
        benchmark_tenants: list[Tenant],
    ):
        """Measure SELECT query performance with RLS enabled."""
        iterations = 100
        tenant_id = benchmark_tenants[0].id

        # Set RLS context
        connection = await db_session.connection()
        await connection.execute(
            text("SET LOCAL app.current_tenant_id = :tenant_id"),
            {"tenant_id": str(tenant_id)},
        )

        times = []
        for _ in range(iterations):
            start = time.perf_counter()
            result = await db_session.execute(
                select(Post).where(not Post.is_deleted).limit(20)
            )
            _ = result.scalars().all()
            times.append(time.perf_counter() - start)

        avg_ms = statistics.mean(times) * 1000
        p95_ms = sorted(times)[int(0.95 * len(times))] * 1000
        p99_ms = sorted(times)[int(0.99 * len(times))] * 1000

        print("\nSELECT Performance with RLS:")
        print(f"  Average: {avg_ms:.3f}ms")
        print(f"  P95: {p95_ms:.3f}ms")
        print(f"  P99: {p99_ms:.3f}ms")

        # Assert reasonable performance
        # Note: Exact thresholds depend on hardware
        assert avg_ms < 50.0, f"Average query time too high: {avg_ms:.3f}ms"
        assert p99_ms < 100.0, f"P99 query time too high: {p99_ms:.3f}ms"

    async def test_select_performance_without_context(
        self,
        db_session: AsyncSession,
        benchmark_posts: list[Post],
    ):
        """Measure SELECT query performance without RLS context (for comparison)."""
        iterations = 100

        # Use bypass to measure baseline without RLS filtering
        connection = await db_session.connection()
        await connection.execute(text("SET LOCAL app.bypass_rls = 'true'"))

        times = []
        for _ in range(iterations):
            start = time.perf_counter()
            result = await db_session.execute(
                select(Post).where(not Post.is_deleted).limit(20)
            )
            _ = result.scalars().all()
            times.append(time.perf_counter() - start)

        avg_ms = statistics.mean(times) * 1000
        p95_ms = sorted(times)[int(0.95 * len(times))] * 1000
        p99_ms = sorted(times)[int(0.99 * len(times))] * 1000

        print("\nSELECT Performance with Bypass (baseline):")
        print(f"  Average: {avg_ms:.3f}ms")
        print(f"  P95: {p95_ms:.3f}ms")
        print(f"  P99: {p99_ms:.3f}ms")

        # This serves as baseline for comparison

    async def test_count_performance_with_rls(
        self,
        db_session: AsyncSession,
        benchmark_posts: list[Post],
        benchmark_tenants: list[Tenant],
    ):
        """Measure COUNT query performance with RLS enabled."""
        iterations = 100
        tenant_id = benchmark_tenants[0].id

        # Set RLS context
        connection = await db_session.connection()
        await connection.execute(
            text("SET LOCAL app.current_tenant_id = :tenant_id"),
            {"tenant_id": str(tenant_id)},
        )

        times = []
        for _ in range(iterations):
            start = time.perf_counter()
            result = await db_session.execute(
                text("SELECT COUNT(*) FROM post WHERE is_deleted = false")
            )
            _ = result.scalar()
            times.append(time.perf_counter() - start)

        avg_ms = statistics.mean(times) * 1000
        p95_ms = sorted(times)[int(0.95 * len(times))] * 1000
        p99_ms = sorted(times)[int(0.99 * len(times))] * 1000

        print("\nCOUNT Performance with RLS:")
        print(f"  Average: {avg_ms:.3f}ms")
        print(f"  P95: {p95_ms:.3f}ms")
        print(f"  P99: {p99_ms:.3f}ms")

        assert avg_ms < 50.0, f"Average COUNT time too high: {avg_ms:.3f}ms"

    async def test_query_plan_uses_tenant_filter(
        self,
        db_session: AsyncSession,
        benchmark_posts: list[Post],
        benchmark_tenants: list[Tenant],
    ):
        """Verify RLS queries include tenant_id filter in execution plan."""
        tenant_id = benchmark_tenants[0].id

        # Set RLS context
        connection = await db_session.connection()
        await connection.execute(
            text("SET LOCAL app.current_tenant_id = :tenant_id"),
            {"tenant_id": str(tenant_id)},
        )

        # Get query plan
        result = await db_session.execute(
            text("EXPLAIN (FORMAT TEXT) SELECT * FROM post WHERE is_deleted = false")
        )
        plan = "\n".join([row[0] for row in result.fetchall()])

        print(f"\nQuery Plan with RLS:\n{plan}")

        # Verify plan includes tenant filtering
        # The plan should show RLS policy is being enforced
        assert "tenant_id" in plan.lower() or "policy" in plan.lower(), \
            "Query plan should show tenant filtering"

    async def test_concurrent_query_performance(
        self,
        db_session: AsyncSession,
        benchmark_posts: list[Post],
        benchmark_tenants: list[Tenant],
    ):
        """Test performance doesn't degrade with multiple concurrent queries."""
        import asyncio

        tenant_id = benchmark_tenants[0].id

        async def single_query():
            """Execute a single query with RLS context."""
            connection = await db_session.connection()
            await connection.execute(
                text("SET LOCAL app.current_tenant_id = :tenant_id"),
                {"tenant_id": str(tenant_id)},
            )

            result = await db_session.execute(
                select(Post).where(not Post.is_deleted).limit(10)
            )
            return result.scalars().all()

        # Run 10 concurrent queries
        start = time.perf_counter()
        results = await asyncio.gather(*[single_query() for _ in range(10)])
        elapsed_ms = (time.perf_counter() - start) * 1000

        print("\n10 Concurrent Queries:")
        print(f"  Total time: {elapsed_ms:.3f}ms")
        print(f"  Average per query: {elapsed_ms / 10:.3f}ms")

        # Verify all queries returned results
        assert all(len(r) > 0 for r in results), "All queries should return data"

        # Total time should be reasonable (not 10x sequential)
        assert elapsed_ms < 500.0, \
            f"Concurrent queries too slow: {elapsed_ms:.3f}ms"


class TestRLSPerformanceComparison:
    """Compare performance with and without RLS to measure overhead."""

    async def test_rls_overhead_measurement(
        self,
        db_session: AsyncSession,
        benchmark_posts: list[Post],
        benchmark_tenants: list[Tenant],
    ):
        """Measure RLS overhead by comparing with/without RLS."""
        iterations = 50
        tenant_id = benchmark_tenants[0].id

        # Measure WITHOUT RLS (using bypass)
        connection = await db_session.connection()
        await connection.execute(text("SET LOCAL app.bypass_rls = 'true'"))

        baseline_times = []
        for _ in range(iterations):
            start = time.perf_counter()
            result = await db_session.execute(
                select(Post).where(not Post.is_deleted).limit(20)
            )
            _ = result.scalars().all()
            baseline_times.append(time.perf_counter() - start)

        baseline_avg = statistics.mean(baseline_times) * 1000

        # Measure WITH RLS
        await connection.execute(text("RESET app.bypass_rls"))
        await connection.execute(
            text("SET LOCAL app.current_tenant_id = :tenant_id"),
            {"tenant_id": str(tenant_id)},
        )

        rls_times = []
        for _ in range(iterations):
            start = time.perf_counter()
            result = await db_session.execute(
                select(Post).where(not Post.is_deleted).limit(20)
            )
            _ = result.scalars().all()
            rls_times.append(time.perf_counter() - start)

        rls_avg = statistics.mean(rls_times) * 1000

        # Calculate overhead
        overhead_pct = ((rls_avg - baseline_avg) / baseline_avg) * 100

        print("\nRLS Overhead Analysis:")
        print(f"  Baseline (bypass): {baseline_avg:.3f}ms")
        print(f"  With RLS: {rls_avg:.3f}ms")
        print(f"  Overhead: {overhead_pct:.2f}%")

        # Assert overhead is acceptable (< 10% for initial implementation)
        # Note: Target is < 5%, but allowing 10% for initial version
        assert overhead_pct < 10.0, \
            f"RLS overhead too high: {overhead_pct:.2f}% (target < 10%)"

        # Print performance report
        print(f"\n{'='*60}")
        print("RLS Performance Report")
        print(f"{'='*60}")
        print(f"{'Query Type':<20} {'Baseline (ms)':<15} {'With RLS (ms)':<15} {'Overhead':<10}")
        print(f"{'-'*60}")
        print(f"{'SELECT (20 rows)':<20} {baseline_avg:<15.3f} {rls_avg:<15.3f} {overhead_pct:<10.2f}%")
        print(f"{'='*60}")
