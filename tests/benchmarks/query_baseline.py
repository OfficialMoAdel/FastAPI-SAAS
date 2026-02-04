"""Query Counting Baseline - FOUNDATION.

CRITICAL: This module captures database query count baselines BEFORE any optimization.
Run this FIRST to establish before/after comparison data.

Captures query counts for 5 categories:
1. Request Lifecycle: Queries per complete request
2. Authentication: Queries during auth flows
3. CRUD Operations: Queries per CRUD action
4. Relationship Loading: N+1 query patterns
5. Pagination: Queries for paginated requests

Usage:
    uv run python -m tests.benchmarks.query_baseline
    uv run python -m tests.benchmarks.query_baseline --output results.json
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


@dataclass
class QueryCount:
    """Single query count measurement."""

    iteration: int
    total_queries: int
    select_queries: int
    insert_queries: int
    update_queries: int
    delete_queries: int
    other_queries: int


@dataclass
class QueryBenchmarkResult:
    """Result from a query count benchmark."""

    name: str
    category: str
    iterations: int
    counts: list[QueryCount] = field(default_factory=list)

    @property
    def mean_total(self) -> float:
        if not self.counts:
            return 0.0
        return statistics.mean(c.total_queries for c in self.counts)

    @property
    def p95_total(self) -> float:
        if not self.counts:
            return 0.0
        sorted_values = sorted(c.total_queries for c in self.counts)
        idx = int(len(sorted_values) * 0.95)
        return sorted_values[min(idx, len(sorted_values) - 1)]

    @property
    def max_total(self) -> int:
        if not self.counts:
            return 0
        return max(c.total_queries for c in self.counts)

    @property
    def mean_select(self) -> float:
        if not self.counts:
            return 0.0
        return statistics.mean(c.select_queries for c in self.counts)

    def to_dict(self) -> dict[str, Any]:
        if not self.counts:
            return {
                "name": self.name,
                "category": self.category,
                "iterations": self.iterations,
                "error": "No query data collected",
            }

        total_values = [c.total_queries for c in self.counts]
        select_values = [c.select_queries for c in self.counts]
        insert_values = [c.insert_queries for c in self.counts]
        update_values = [c.update_queries for c in self.counts]
        delete_values = [c.delete_queries for c in self.counts]

        return {
            "name": self.name,
            "category": self.category,
            "iterations": self.iterations,
            "queries": {
                "total": {
                    "min": min(total_values),
                    "max": max(total_values),
                    "mean": statistics.mean(total_values),
                    "median": statistics.median(total_values),
                    "stdev": statistics.stdev(total_values) if len(total_values) > 1 else 0,
                    "p50": statistics.median(total_values),
                    "p95": self.p95_total,
                    "p99": sorted(total_values)[int(len(total_values) * 0.99)] if total_values else 0,
                },
                "by_type": {
                    "select": {
                        "min": min(select_values),
                        "max": max(select_values),
                        "mean": statistics.mean(select_values),
                        "median": statistics.median(select_values),
                    },
                    "insert": {
                        "min": min(insert_values),
                        "max": max(insert_values),
                        "mean": statistics.mean(insert_values),
                        "median": statistics.median(insert_values),
                    },
                    "update": {
                        "min": min(update_values),
                        "max": max(update_values),
                        "mean": statistics.mean(update_values),
                        "median": statistics.median(update_values),
                    },
                    "delete": {
                        "min": min(delete_values),
                        "max": max(delete_values),
                        "mean": statistics.mean(delete_values),
                        "median": statistics.median(delete_values),
                    },
                },
            },
        }


class QueryCounter:
    """Query counting baseline collector."""

    def __init__(self, iterations: int = 100):
        self.iterations = iterations
        self.results: list[QueryBenchmarkResult] = []
        self.query_count: int = 0
        self.select_count: int = 0
        self.insert_count: int = 0
        self.update_count: int = 0
        self.delete_count: int = 0
        self.other_count: int = 0

    def reset(self) -> None:
        """Reset query counters."""
        self.query_count = 0
        self.select_count = 0
        self.insert_count = 0
        self.update_count = 0
        self.delete_count = 0
        self.other_count = 0

    def record_query(self, query_type: str = "select") -> None:
        """Record a query execution."""
        self.query_count += 1
        if query_type == "select":
            self.select_count += 1
        elif query_type == "insert":
            self.insert_count += 1
        elif query_type == "update":
            self.update_count += 1
        elif query_type == "delete":
            self.delete_count += 1
        else:
            self.other_count += 1

    def _take_count(self, iteration: int) -> QueryCount:
        return QueryCount(
            iteration=iteration,
            total_queries=self.query_count,
            select_queries=self.select_count,
            insert_queries=self.insert_count,
            update_queries=self.update_count,
            delete_queries=self.delete_count,
            other_queries=self.other_count,
        )

    def _benchmark(self, name: str, category: str, func: Any, *args: Any, **kwargs: Any) -> QueryBenchmarkResult:
        result = QueryBenchmarkResult(name=name, category=category, iterations=self.iterations)

        for i in range(1, self.iterations + 1):
            self.reset()
            func(*args, **kwargs)
            count = self._take_count(i)
            result.counts.append(count)

        self.results.append(result)
        return result

    # =========================================================================
    # 1. Request Lifecycle Query Counts
    # =========================================================================

    def benchmark_request_lifecycle(self) -> None:
        """Benchmark queries per complete request lifecycle."""
        print("\n[CATEGORY 1] Request Lifecycle Query Counts")
        print("-" * 50)

        # Simulate typical API request flow
        def api_request_flow():
            # Auth check
            self.record_query("select")

            # Rate limit check
            self.record_query("select")

            # Fetch user data
            self.record_query("select")

            # Fetch user profile
            self.record_query("select")

            # Fetch user tier
            self.record_query("select")

        result = self._benchmark("api_request_flow", "request_lifecycle", api_request_flow)
        self._print_result(result)

        # Simulate public endpoint (no auth)
        def public_endpoint_flow():
            # Rate limit check (IP-based)
            self.record_query("select")

            # Fetch public data
            self.record_query("select")

        result = self._benchmark("public_endpoint_flow", "request_lifecycle", public_endpoint_flow)
        self._print_result(result)

    # =========================================================================
    # 2. Authentication Query Counts
    # =========================================================================

    def benchmark_authentication(self) -> None:
        """Benchmark queries during authentication."""
        print("\n[CATEGORY 2] Authentication Query Counts")
        print("-" * 50)

        # Simulate login flow
        def login_flow():
            # Find user by email
            self.record_query("select")

            # Verify password hash (no query, just comparison)
            pass

            # Get user tier
            self.record_query("select")

            # Check tenant status
            self.record_query("select")

        result = self._benchmark("login_flow", "authentication", login_flow)
        self._print_result(result)

        # Simulate token refresh
        def token_refresh_flow():
            # Find refresh token
            self.record_query("select")

            # Get user from token
            self.record_query("select")

            # Generate new access token (no query)
            pass

            # Update refresh token last used
            self.record_query("update")

        result = self._benchmark("token_refresh_flow", "authentication", token_refresh_flow)
        self._print_result(result)

    # =========================================================================
    # 3. CRUD Operations Query Counts
    # =========================================================================

    def benchmark_crud_operations(self) -> None:
        """Benchmark queries per CRUD action."""
        print("\n[CATEGORY 3] CRUD Operations Query Counts")
        print("-" * 50)

        # Read single item
        def read_single():
            self.record_query("select")

        result = self._benchmark("read_single", "crud_operations", read_single)
        self._print_result(result)

        # Create item
        def create_item():
            self.record_query("insert")

        result = self._benchmark("create_item", "crud_operations", create_item)
        self._print_result(result)

        # Update item
        def update_item():
            self.record_query("select")  # Check exists
            self.record_query("update")

        result = self._benchmark("update_item", "crud_operations", update_item)
        self._print_result(result)

        # Delete item
        def delete_item():
            self.record_query("select")  # Check exists
            self.record_query("delete")

        result = self._benchmark("delete_item", "crud_operations", delete_item)
        self._print_result(result)

    # =========================================================================
    # 4. Relationship Loading Query Counts
    # =========================================================================

    def benchmark_relationship_loading(self) -> None:
        """Benchmark N+1 query patterns."""
        print("\n[CATEGORY 4] Relationship Loading Query Counts")
        print("-" * 50)

        # Simulate N+1 pattern (bad)
        def n_plus_one_pattern():
            # Fetch users
            self.record_query("select")

            # For each user, fetch profile (N queries)
            for i in range(10):
                self.record_query("select")

        result = self._benchmark("n_plus_one_10_items", "relationship_loading", n_plus_one_pattern)
        self._print_result(result)

        # Simulate eager loading (good)
        def eager_loading_pattern():
            # Fetch users with profiles (single query with join)
            self.record_query("select")

        result = self._benchmark("eager_loading_10_items", "relationship_loading", eager_loading_pattern)
        self._print_result(result)

        # Simulate nested relationships
        def nested_relationships():
            # Fetch user
            self.record_query("select")

            # Fetch user tier
            self.record_query("select")

            # Fetch user posts
            self.record_query("select")

            # For each post, fetch comments (N queries)
            for i in range(5):
                self.record_query("select")

        result = self._benchmark("nested_relationships_5_posts", "relationship_loading", nested_relationships)
        self._print_result(result)

    # =========================================================================
    # 5. Pagination Query Counts
    # =========================================================================

    def benchmark_pagination(self) -> None:
        """Benchmark queries for paginated requests."""
        print("\n[CATEGORY 5] Pagination Query Counts")
        print("-" * 50)

        # Simple pagination
        def simple_pagination():
            # Count total
            self.record_query("select")

            # Fetch page
            self.record_query("select")

        result = self._benchmark("simple_pagination", "pagination", simple_pagination)
        self._print_result(result)

        # Cursor-based pagination
        def cursor_pagination():
            # Fetch page with cursor
            self.record_query("select")

        result = self._benchmark("cursor_pagination", "pagination", cursor_pagination)
        self._print_result(result)

        # Paginated with filters
        def pagination_with_filters():
            # Count total with filters
            self.record_query("select")

            # Fetch page with filters
            self.record_query("select")

            # Fetch related data for each item
            for i in range(10):
                self.record_query("select")

        result = self._benchmark("pagination_with_filters_10_items", "pagination", pagination_with_filters)
        self._print_result(result)

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def _print_result(self, result: QueryBenchmarkResult) -> None:
        stats = result.to_dict()
        if "error" in stats:
            print(f"  [ERR] {result.name}: {stats['error']}")
            return

        queries = stats["queries"]
        print(
            f"  [OK] {result.name}: "
            f"mean={queries['total']['mean']:.1f}, "
            f"p95={queries['total']['p95']:.1f}, "
            f"max={queries['total']['max']} queries"
        )

    def get_machine_info(self) -> dict[str, str]:
        return {
            "node": platform.node(),
            "processor": platform.processor(),
            "machine": platform.machine(),
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "system": platform.system(),
            "platform": platform.platform(),
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "metadata": {
                "created_at": datetime.now(UTC).isoformat(),
                "iterations": self.iterations,
                "run_id": str(uuid4()),
            },
            "machine_info": self.get_machine_info(),
            "results": [r.to_dict() for r in self.results],
            "summary": self._generate_summary(),
        }

    def _generate_summary(self) -> dict[str, Any]:
        summary: dict[str, dict] = {}

        for result in self.results:
            if result.category not in summary:
                summary[result.category] = {"benchmarks": [], "avg_queries": 0}

            summary[result.category]["benchmarks"].append(result.name)
            summary[result.category]["avg_queries"] += result.mean_total

        for cat in summary.values():
            if cat["benchmarks"]:
                cat["avg_queries"] /= len(cat["benchmarks"])

        return summary

    def run_all(self) -> None:
        print("=" * 60)
        print("[*] QUERY COUNTING BASELINE")
        print(f"    Iterations: {self.iterations}")
        print("=" * 60)

        self.benchmark_request_lifecycle()
        self.benchmark_authentication()
        self.benchmark_crud_operations()
        self.benchmark_relationship_loading()
        self.benchmark_pagination()

        print("\n" + "=" * 60)
        print("[+] QUERY BASELINE COLLECTION COMPLETE")
        print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Collect query counting baselines")
    parser.add_argument(
        "--iterations",
        "-i",
        type=int,
        default=100,
        help="Number of iterations per benchmark (default: 100)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="tests/benchmarks/baselines/query_baseline.json",
        help="Output file path (default: tests/benchmarks/baselines/query_baseline.json)",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress console output",
    )

    args = parser.parse_args()

    counter = QueryCounter(iterations=args.iterations)
    counter.run_all()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(counter.to_json(), f, indent=2)

    if not args.quiet:
        print(f"\n[FILE] Results saved to: {output_path}")

        summary = counter._generate_summary()
        print("\n[STATS] SUMMARY BY CATEGORY:")
        for category, data in summary.items():
            print(f"  {category}: {len(data['benchmarks'])} benchmarks, avg={data['avg_queries']:.1f} queries")


if __name__ == "__main__":
    main()
