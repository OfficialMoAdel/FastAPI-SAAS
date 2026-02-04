"""Memory Profiling Baseline - FOUNDATION.

CRITICAL: This module captures memory usage baselines BEFORE any optimization.
Run this FIRST to establish before/after comparison data.

Captures 5 memory metric categories:
1. Request Handling: Memory overhead per request lifecycle
2. Cache Operations: Memory growth with cache size
3. Database Operations: Memory usage during queries
4. JSON Serialization: Memory allocation patterns
5. Session Management: Memory per session object

Usage:
    uv run python -m tests.benchmarks.memory_baseline
    uv run python -m tests.benchmarks.memory_baseline --output results.json
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import statistics
import sys
import tracemalloc
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


@dataclass
class MemorySnapshot:
    """Single memory measurement snapshot."""

    snapshot_id: int
    current_mb: float
    peak_mb: float
    allocated_kb: int
    freed_kb: int
    diff_kb: int


@dataclass
class MemoryBenchmarkResult:
    """Result from a memory benchmark."""

    name: str
    category: str
    iterations: int
    snapshots: list[MemorySnapshot] = field(default_factory=list)

    @property
    def mean_current_mb(self) -> float:
        if not self.snapshots:
            return 0.0
        return statistics.mean(s.current_mb for s in self.snapshots)

    @property
    def p95_current_mb(self) -> float:
        if not self.snapshots:
            return 0.0
        sorted_values = sorted(s.current_mb for s in self.snapshots)
        idx = int(len(sorted_values) * 0.95)
        return sorted_values[min(idx, len(sorted_values) - 1)]

    @property
    def max_peak_mb(self) -> float:
        if not self.snapshots:
            return 0.0
        return max(s.peak_mb for s in self.snapshots)

    @property
    def mean_diff_kb(self) -> float:
        if not self.snapshots:
            return 0.0
        return statistics.mean(s.diff_kb for s in self.snapshots)

    def to_dict(self) -> dict[str, Any]:
        if not self.snapshots:
            return {
                "name": self.name,
                "category": self.category,
                "iterations": self.iterations,
                "error": "No memory data collected",
            }

        current_values = [s.current_mb for s in self.snapshots]
        peak_values = [s.peak_mb for s in self.snapshots]
        diff_values = [s.diff_kb for s in self.snapshots]

        return {
            "name": self.name,
            "category": self.category,
            "iterations": self.iterations,
            "memory_mb": {
                "current": {
                    "min": min(current_values),
                    "max": max(current_values),
                    "mean": statistics.mean(current_values),
                    "median": statistics.median(current_values),
                    "stdev": statistics.stdev(current_values) if len(current_values) > 1 else 0,
                    "p50": statistics.median(current_values),
                    "p95": self.p95_current_mb,
                    "p99": sorted(current_values)[int(len(current_values) * 0.99)] if current_values else 0,
                },
                "peak": {
                    "min": min(peak_values),
                    "max": max(peak_values),
                    "mean": statistics.mean(peak_values),
                    "median": statistics.median(peak_values),
                },
            },
            "diff_kb": {
                "min": min(diff_values),
                "max": max(diff_values),
                "mean": statistics.mean(diff_values),
                "median": statistics.median(diff_values),
            },
        }


class MemoryProfiler:
    """Memory profiling baseline collector."""

    def __init__(self, iterations: int = 100):
        self.iterations = iterations
        self.results: list[MemoryBenchmarkResult] = []
        self.baseline_snapshot: tracemalloc.Snapshot | None = None
        tracemalloc.start()
        self.baseline_snapshot = tracemalloc.take_snapshot()

    def _take_snapshot(self, snapshot_id: int) -> MemorySnapshot:
        current_snapshot = tracemalloc.take_snapshot()
        stats = current_snapshot.statistics("lineno")

        current = sum(stat.size for stat in stats) / 1024 / 1024
        peak = tracemalloc.get_traced_memory()[1] / 1024 / 1024
        allocated = sum(stat.size for stat in stats) / 1024

        # Calculate diff from baseline
        if self.baseline_snapshot:
            diff_stats = current_snapshot.compare_to(self.baseline_snapshot, "lineno")
            diff = sum(stat.size_diff for stat in diff_stats if stat.size_diff > 0) / 1024
            freed = sum(abs(stat.size_diff) for stat in diff_stats if stat.size_diff < 0) / 1024
        else:
            diff = 0
            freed = 0

        return MemorySnapshot(
            snapshot_id=snapshot_id,
            current_mb=current,
            peak_mb=peak,
            allocated_kb=allocated,
            freed_kb=freed,
            diff_kb=diff,
        )

    def _benchmark(self, name: str, category: str, func: Any, *args: Any, **kwargs: Any) -> MemoryBenchmarkResult:
        result = MemoryBenchmarkResult(name=name, category=category, iterations=self.iterations)

        baseline = self._take_snapshot(0)

        for i in range(1, self.iterations + 1):
            gc.collect()
            func(*args, **kwargs)
            snapshot = self._take_snapshot(i)
            result.snapshots.append(snapshot)

        self.results.append(result)
        return result

    # =========================================================================
    # 1. Request Handling Memory
    # =========================================================================

    def benchmark_request_handling(self) -> None:
        """Benchmark memory overhead of request processing."""
        print("\n[CATEGORY 1] Request Handling Memory")
        print("-" * 50)

        # Simulate request object creation
        def create_request():
            return {
                "method": "GET",
                "path": "/api/v1/users/me",
                "headers": {
                    "content-type": "application/json",
                    "authorization": "Bearer " + "x" * 100,
                    "x-request-id": str(uuid4()),
                },
                "query_params": {"include": "profile"},
                "body": None,
            }

        result = self._benchmark("request_object_creation", "request_handling", create_request)
        self._print_result(result)

        # Simulate response object creation
        def create_response():
            return {
                "status_code": 200,
                "headers": {
                    "content-type": "application/json",
                    "x-request-id": str(uuid4()),
                },
                "body": {
                    "id": 1,
                    "name": "Test User",
                    "email": "test@example.com",
                    "tier": {"id": 1, "name": "pro"},
                },
            }

        result = self._benchmark("response_object_creation", "request_handling", create_response)
        self._print_result(result)

    # =========================================================================
    # 2. Cache Operations Memory
    # =========================================================================

    def benchmark_cache_operations(self) -> None:
        """Benchmark memory growth with cache operations."""
        print("\n[CATEGORY 2] Cache Operations Memory")
        print("-" * 50)

        # Simulate cache entry storage
        def cache_entry():
            return {
                "key": f"tenant:{uuid4()}",
                "value": {
                    "id": 1,
                    "slug": "acme-corp",
                    "is_active": True,
                    "settings": {f"key_{i}": f"value_{i}" for i in range(10)},
                },
                "ttl": 3600,
            }

        result = self._benchmark("cache_entry_creation", "cache_operations", cache_entry)
        self._print_result(result)

        # Simulate session storage
        def session_entry():
            return {
                "session_id": str(uuid4()),
                "user_id": 1,
                "device_info": "Chrome 120 / Windows",
                "created_at": datetime.now(UTC).isoformat(),
                "last_active": datetime.now(UTC).isoformat(),
                "metadata": {"ip": "192.168.1.1", "user_agent": "Mozilla/5.0"},
            }

        result = self._benchmark("session_entry_creation", "cache_operations", session_entry)
        self._print_result(result)

    # =========================================================================
    # 3. Database Operations Memory
    # =========================================================================

    def benchmark_database_operations(self) -> None:
        """Benchmark memory usage during database operations."""
        print("\n[CATEGORY 3] Database Operations Memory")
        print("-" * 50)

        # Simulate user model creation
        def create_user_model():
            return {
                "id": 1,
                "username": "testuser",
                "email": "test@example.com",
                "name": "Test User",
                "is_active": True,
                "tier_id": 1,
                "profile_image_url": "https://example.com/avatar.jpg",
                "created_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
            }

        result = self._benchmark("user_model_creation", "database_operations", create_user_model)
        self._print_result(result)

        # Simulate query result set
        def create_query_result():
            return [
                {
                    "id": i,
                    "username": f"user{i}",
                    "email": f"user{i}@example.com",
                    "is_active": True,
                }
                for i in range(50)
            ]

        result = self._benchmark("query_result_50_rows", "database_operations", create_query_result)
        self._print_result(result)

    # =========================================================================
    # 4. JSON Serialization Memory
    # =========================================================================

    def benchmark_json_serialization(self) -> None:
        """Benchmark memory allocation during JSON operations."""
        print("\n[CATEGORY 4] JSON Serialization Memory")
        print("-" * 50)

        import json as stdlib_json

        # Small payload
        small_payload = {"id": 1, "name": "test"}

        def json_dumps_small():
            return stdlib_json.dumps(small_payload)

        result = self._benchmark("json_dumps_small", "json_serialization", json_dumps_small)
        self._print_result(result)

        # Medium payload
        medium_payload = {
            "id": 1,
            "name": "test user",
            "email": "test@example.com",
            "profile": {"avatar": "url", "bio": "text" * 50},
            "settings": {f"key_{i}": f"value_{i}" for i in range(20)},
        }

        def json_dumps_medium():
            return stdlib_json.dumps(medium_payload)

        result = self._benchmark("json_dumps_medium", "json_serialization", json_dumps_medium)
        self._print_result(result)

        # Large payload
        large_payload = {
            "users": [
                {
                    "id": i,
                    "name": f"User {i}",
                    "email": f"user{i}@example.com",
                    "data": {"field": "value" * 10},
                }
                for i in range(100)
            ]
        }

        def json_dumps_large():
            return stdlib_json.dumps(large_payload)

        result = self._benchmark("json_dumps_large", "json_serialization", json_dumps_large)
        self._print_result(result)

    # =========================================================================
    # 5. Session Management Memory
    # =========================================================================

    def benchmark_session_management(self) -> None:
        """Benchmark memory per session object."""
        print("\n[CATEGORY 5] Session Management Memory")
        print("-" * 50)

        # Single session
        def create_single_session():
            return {
                "session_id": str(uuid4()),
                "user_id": 1,
                "tenant_id": 1,
                "created_at": datetime.now(UTC).isoformat(),
                "last_active": datetime.now(UTC).isoformat(),
                "device_info": "Chrome 120 / Windows 10",
                "ip_address": "192.168.1.100",
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "is_active": True,
            }

        result = self._benchmark("single_session_creation", "session_management", create_single_session)
        self._print_result(result)

        # Multiple sessions (simulating user with multiple devices)
        def create_multiple_sessions():
            return [
                {
                    "session_id": str(uuid4()),
                    "user_id": 1,
                    "device_info": f"Device {i}",
                    "created_at": datetime.now(UTC).isoformat(),
                    "is_active": True,
                }
                for i in range(5)
            ]

        result = self._benchmark("multiple_sessions_5", "session_management", create_multiple_sessions)
        self._print_result(result)

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def _print_result(self, result: MemoryBenchmarkResult) -> None:
        stats = result.to_dict()
        if "error" in stats:
            print(f"  [ERR] {result.name}: {stats['error']}")
            return

        mem = stats["memory_mb"]["current"]
        print(
            f"  [OK] {result.name}: "
            f"mean={mem['mean']:.4f}MB, "
            f"p95={mem['p95']:.4f}MB, "
            f"peak={stats['memory_mb']['peak']['max']:.4f}MB"
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
                summary[result.category] = {"benchmarks": [], "avg_memory_mb": 0}

            summary[result.category]["benchmarks"].append(result.name)
            summary[result.category]["avg_memory_mb"] += result.mean_current_mb

        for cat in summary.values():
            if cat["benchmarks"]:
                cat["avg_memory_mb"] /= len(cat["benchmarks"])

        return summary

    def run_all(self) -> None:
        print("=" * 60)
        print("[*] MEMORY PROFILING BASELINE")
        print(f"    Iterations: {self.iterations}")
        print("=" * 60)

        self.benchmark_request_handling()
        self.benchmark_cache_operations()
        self.benchmark_database_operations()
        self.benchmark_json_serialization()
        self.benchmark_session_management()

        tracemalloc.stop()

        print("\n" + "=" * 60)
        print("[+] MEMORY BASELINE COLLECTION COMPLETE")
        print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Collect memory profiling baselines")
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
        default="tests/benchmarks/baselines/memory_baseline.json",
        help="Output file path (default: tests/benchmarks/baselines/memory_baseline.json)",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress console output",
    )

    args = parser.parse_args()

    profiler = MemoryProfiler(iterations=args.iterations)
    profiler.run_all()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(profiler.to_json(), f, indent=2)

    print(f"\n[FILE] Results saved to: {output_path}")

    summary = profiler._generate_summary()
    print("\n[STATS] SUMMARY BY CATEGORY:")
    for category, data in summary.items():
        print(f"  {category}: {len(data['benchmarks'])} benchmarks, avg={data['avg_memory_mb']:.4f}MB")


if __name__ == "__main__":
    main()
