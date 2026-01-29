"""
Capture query performance baseline BEFORE RLS implementation.

This script benchmarks key database queries to establish a performance baseline
before enabling Row Level Security (RLS) policies. The results will be used to
measure the overhead introduced by RLS.

CRITICAL: Run this BEFORE applying RLS migration!
"""

import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.session import get_session


class PerformanceBaseline:
    """Capture performance baseline for database queries."""

    def __init__(self, iterations: int = 1000, warmup: int = 100) -> None:
        """
        Initialize baseline capture.

        Args:
            iterations: Number of iterations per query (after warmup)
            warmup: Number of warmup iterations to discard
        """
        self.iterations = iterations
        self.warmup = warmup
        self.results: dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat(),
            "rls_enabled": False,
            "iterations": iterations,
            "warmup": warmup,
            "queries": {},
        }

    async def benchmark_query(
        self,
        session: AsyncSession,
        query: str,
        params: dict[str, Any],
        name: str,
    ) -> dict[str, float]:
        """
        Benchmark a single query.

        Args:
            session: Database session
            query: SQL query to benchmark
            params: Query parameters
            name: Name for this benchmark

        Returns:
            Dictionary with performance metrics
        """
        times: list[float] = []

        # Warmup + actual iterations
        for i in range(self.warmup + self.iterations):
            start = time.perf_counter()
            await session.execute(text(query), params)
            elapsed = time.perf_counter() - start

            # Only record after warmup
            if i >= self.warmup:
                times.append(elapsed * 1000)  # Convert to milliseconds

        # Calculate statistics
        sorted_times = sorted(times)
        metrics = {
            "avg_latency_ms": statistics.mean(times),
            "median_latency_ms": statistics.median(times),
            "p50_latency_ms": sorted_times[int(0.50 * len(sorted_times))],
            "p95_latency_ms": sorted_times[int(0.95 * len(sorted_times))],
            "p99_latency_ms": sorted_times[int(0.99 * len(sorted_times))],
            "min_latency_ms": min(times),
            "max_latency_ms": max(times),
            "stddev_ms": statistics.stdev(times) if len(times) > 1 else 0.0,
            "throughput_qps": 1000 / statistics.mean(times) if statistics.mean(times) > 0 else 0.0,
        }

        print(f"\n{name}:")
        print(f"  Avg: {metrics['avg_latency_ms']:.3f}ms")
        print(f"  P50: {metrics['p50_latency_ms']:.3f}ms")
        print(f"  P95: {metrics['p95_latency_ms']:.3f}ms")
        print(f"  P99: {metrics['p99_latency_ms']:.3f}ms")
        print(f"  Throughput: {metrics['throughput_qps']:.1f} qps")

        return metrics

    async def run_all_benchmarks(self, session: AsyncSession) -> None:
        """Run all benchmark queries."""
        # Use a test tenant ID (assuming it exists in test data)
        tenant_id = "test-tenant-001"
        user_id = "test-user-001"
        post_id = "test-post-001"

        print("\n" + "=" * 60)
        print("PERFORMANCE BASELINE - PRE-RLS")
        print("=" * 60)

        # 1. Simple SELECT by tenant
        query = """
            SELECT * FROM post
            WHERE tenant_id = :tenant_id
            LIMIT 100
        """
        self.results["queries"]["select_posts"] = await self.benchmark_query(
            session,
            query,
            {"tenant_id": tenant_id},
            "1. SELECT posts by tenant",
        )

        # 2. COUNT by tenant
        query = """
            SELECT COUNT(*) FROM audit_logs
            WHERE tenant_id = :tenant_id
        """
        self.results["queries"]["count_audit_logs"] = await self.benchmark_query(
            session,
            query,
            {"tenant_id": tenant_id},
            "2. COUNT audit logs by tenant",
        )

        # 3. JOIN across tenant tables
        query = """
            SELECT p.*, u.email
            FROM post p
            JOIN "user" u ON p.created_by_user_id = u.id
            WHERE p.tenant_id = :tenant_id
            LIMIT 50
        """
        self.results["queries"]["join_post_user"] = await self.benchmark_query(
            session,
            query,
            {"tenant_id": tenant_id},
            "3. JOIN posts with users",
        )

        # 4. Aggregate with GROUP BY
        query = """
            SELECT DATE(created_at) as date, COUNT(*) as count
            FROM audit_logs
            WHERE tenant_id = :tenant_id
            GROUP BY DATE(created_at)
            LIMIT 30
        """
        self.results["queries"]["aggregate_audit_logs"] = await self.benchmark_query(
            session,
            query,
            {"tenant_id": tenant_id},
            "4. Aggregate audit logs by date",
        )

        # 5. INSERT single row (using a test table or rollback)
        # Note: This will be rolled back by pytest transaction
        query = """
            INSERT INTO audit_logs (
                id, tenant_id, user_id, action, resource_type,
                resource_id, created_at
            )
            VALUES (
                gen_random_uuid(), :tenant_id, :user_id, 'TEST', 'benchmark',
                gen_random_uuid(), NOW()
            )
        """
        self.results["queries"]["insert_audit_log"] = await self.benchmark_query(
            session,
            query,
            {"tenant_id": tenant_id, "user_id": user_id},
            "5. INSERT audit log",
        )

        # 6. UPDATE by tenant
        query = """
            UPDATE post
            SET updated_at = NOW()
            WHERE tenant_id = :tenant_id
            AND id = :post_id
        """
        self.results["queries"]["update_post"] = await self.benchmark_query(
            session,
            query,
            {"tenant_id": tenant_id, "post_id": post_id},
            "6. UPDATE post by tenant",
        )

        # 7. Soft DELETE by tenant
        query = """
            UPDATE post
            SET is_deleted = true, updated_at = NOW()
            WHERE tenant_id = :tenant_id
            AND id = :post_id
        """
        self.results["queries"]["soft_delete_post"] = await self.benchmark_query(
            session,
            query,
            {"tenant_id": tenant_id, "post_id": post_id},
            "7. Soft DELETE post by tenant",
        )

        print("\n" + "=" * 60)
        print("BASELINE CAPTURE COMPLETE")
        print("=" * 60)

    def save_results(self, output_path: Path) -> None:
        """Save results to JSON file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(self.results, f, indent=2)
        print(f"\nResults saved to: {output_path}")


@pytest.mark.asyncio
async def test_capture_baseline_pre_rls() -> None:
    """
    Capture performance baseline before RLS implementation.

    This test should be run BEFORE applying the RLS migration.
    Results are saved to tests/performance/baselines/baseline_pre_rls.json
    """
    baseline = PerformanceBaseline(iterations=1000, warmup=100)

    # Get database session
    async for session in get_session():
        try:
            await baseline.run_all_benchmarks(session)

            # Save results
            output_path = Path(__file__).parent / "baselines" / "baseline_pre_rls.json"
            baseline.save_results(output_path)

            # Print summary
            print("\n" + "=" * 60)
            print("SUMMARY")
            print("=" * 60)
            for query_name, metrics in baseline.results["queries"].items():
                print(f"{query_name}:")
                print(f"  Avg: {metrics['avg_latency_ms']:.3f}ms")
                print(f"  P95: {metrics['p95_latency_ms']:.3f}ms")

            # Assert reasonable performance (sanity check)
            for query_name, metrics in baseline.results["queries"].items():
                # All queries should complete in reasonable time
                assert (
                    metrics["avg_latency_ms"] < 1000
                ), f"{query_name} too slow: {metrics['avg_latency_ms']:.3f}ms"

                # P99 should not be excessively high
                assert (
                    metrics["p99_latency_ms"] < 5000
                ), f"{query_name} P99 too high: {metrics['p99_latency_ms']:.3f}ms"

            print("\n✓ Baseline captured successfully!")
            print("✓ All queries completed in < 1000ms average")
            print(f"✓ Results saved to: {output_path}")

        finally:
            # Rollback any changes (INSERT/UPDATE operations)
            await session.rollback()
            break


@pytest.mark.asyncio
async def test_quick_baseline_check() -> None:
    """
    Quick baseline check with fewer iterations for CI/CD.

    This is a faster version for continuous integration.
    """
    baseline = PerformanceBaseline(iterations=100, warmup=10)

    async for session in get_session():
        try:
            # Just test a few key queries
            tenant_id = "test-tenant-001"

            # Simple SELECT
            query = "SELECT * FROM post WHERE tenant_id = :tenant_id LIMIT 100"
            metrics = await baseline.benchmark_query(
                session, query, {"tenant_id": tenant_id}, "Quick SELECT test"
            )

            # Sanity check
            assert metrics["avg_latency_ms"] < 1000, "Query too slow"

            print("\n✓ Quick baseline check passed!")

        finally:
            await session.rollback()
            break


if __name__ == "__main__":
    """
    Run baseline capture directly.

    Usage:
        uv run python tests/performance/baseline_pre_rls.py
    """
    import asyncio

    async def main() -> None:
        """Main entry point for direct execution."""
        baseline = PerformanceBaseline(iterations=1000, warmup=100)

        async for session in get_session():
            try:
                await baseline.run_all_benchmarks(session)

                output_path = (
                    Path(__file__).parent / "baselines" / "baseline_pre_rls.json"
                )
                baseline.save_results(output_path)

                print("\n✓ Baseline captured successfully!")

            finally:
                await session.rollback()
                break

    asyncio.run(main())
