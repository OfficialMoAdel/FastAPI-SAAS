#!/usr/bin/env python3
"""Baseline Performance Capture Script.

Captures comprehensive performance baselines for the PERF-ADV optimization rounds.
Outputs structured JSON with latency percentiles, throughput, and metadata.

Usage:
    python scripts/capture_baseline.py --round 1
    python scripts/capture_baseline.py --round 1 --output custom_path.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


@dataclass
class BenchmarkResult:
    """Single benchmark result with latency percentiles."""

    name: str
    samples: list[float] = field(default_factory=list)
    mean_ms: float = 0.0
    median_ms: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0
    ops_per_sec: float = 0.0

    def compute_stats(self) -> None:
        """Compute statistics from samples."""
        if not self.samples:
            return

        sorted_samples = sorted(self.samples)
        n = len(sorted_samples)

        self.mean_ms = statistics.mean(sorted_samples) * 1000
        self.median_ms = statistics.median(sorted_samples) * 1000
        self.min_ms = sorted_samples[0] * 1000
        self.max_ms = sorted_samples[-1] * 1000

        # Percentiles
        self.p50_ms = sorted_samples[int(n * 0.50)] * 1000
        self.p95_ms = sorted_samples[int(n * 0.95)] * 1000
        self.p99_ms = sorted_samples[min(int(n * 0.99), n - 1)] * 1000

        # Throughput
        if self.mean_ms > 0:
            self.ops_per_sec = 1000.0 / self.mean_ms

    def to_dict(self) -> dict:
        """Convert to dictionary without samples."""
        return {
            "name": self.name,
            "mean_ms": round(self.mean_ms, 4),
            "median_ms": round(self.median_ms, 4),
            "p50_ms": round(self.p50_ms, 4),
            "p95_ms": round(self.p95_ms, 4),
            "p99_ms": round(self.p99_ms, 4),
            "min_ms": round(self.min_ms, 4),
            "max_ms": round(self.max_ms, 4),
            "ops_per_sec": round(self.ops_per_sec, 2),
            "sample_count": len(self.samples),
        }


@dataclass
class BaselineReport:
    """Complete baseline performance report."""

    round_number: int
    captured_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    git_commit: str = ""
    git_branch: str = ""
    git_dirty: bool = False
    python_version: str = field(default_factory=lambda: platform.python_version())
    platform_info: str = field(default_factory=lambda: f"{platform.system()} {platform.release()}")
    cpu_info: str = ""
    benchmarks: dict[str, BenchmarkResult] = field(default_factory=dict)
    thresholds: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to serializable dictionary."""
        return {
            "metadata": {
                "round": self.round_number,
                "captured_at": self.captured_at,
                "git_commit": self.git_commit,
                "git_branch": self.git_branch,
                "git_dirty": self.git_dirty,
                "python_version": self.python_version,
                "platform": self.platform_info,
                "cpu": self.cpu_info,
            },
            "benchmarks": {name: result.to_dict() for name, result in self.benchmarks.items()},
            "thresholds": self.thresholds,
        }


def get_git_info() -> tuple[str, str, bool]:
    """Get current git commit, branch, and dirty status."""
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()

        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()

        dirty_check = subprocess.check_output(
            ["git", "status", "--porcelain"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        dirty = bool(dirty_check)

        return commit, branch, dirty
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown", "unknown", False


def get_cpu_info() -> str:
    """Get CPU information."""
    try:
        if platform.system() == "Windows":
            return platform.processor()
        elif platform.system() == "Linux":
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        return line.split(":")[1].strip()
        elif platform.system() == "Darwin":
            result = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            return result.strip()
    except Exception:
        pass
    return platform.processor() or "unknown"


def benchmark_function(
    func: Callable[[], None],
    name: str,
    warmup_rounds: int = 10,
    min_rounds: int = 1000,
    max_time_seconds: float = 5.0,
) -> BenchmarkResult:
    """Benchmark a synchronous function."""
    result = BenchmarkResult(name=name)

    # Warmup
    for _ in range(warmup_rounds):
        func()

    # Benchmark
    start_time = time.perf_counter()
    rounds = 0

    while rounds < min_rounds or (time.perf_counter() - start_time) < max_time_seconds:
        t0 = time.perf_counter()
        func()
        t1 = time.perf_counter()
        result.samples.append(t1 - t0)
        rounds += 1

        if rounds >= min_rounds and (time.perf_counter() - start_time) >= max_time_seconds:
            break

    result.compute_stats()
    return result


async def benchmark_async_function(
    func: Callable[[], object],
    name: str,
    warmup_rounds: int = 10,
    min_rounds: int = 1000,
    max_time_seconds: float = 5.0,
) -> BenchmarkResult:
    """Benchmark an async function."""
    result = BenchmarkResult(name=name)

    # Warmup
    for _ in range(warmup_rounds):
        await func()

    # Benchmark
    start_time = time.perf_counter()
    rounds = 0

    while rounds < min_rounds or (time.perf_counter() - start_time) < max_time_seconds:
        t0 = time.perf_counter()
        await func()
        t1 = time.perf_counter()
        result.samples.append(t1 - t0)
        rounds += 1

        if rounds >= min_rounds and (time.perf_counter() - start_time) >= max_time_seconds:
            break

    result.compute_stats()
    return result


async def run_jwt_benchmarks() -> dict[str, BenchmarkResult]:
    """Run JWT-related benchmarks against the real auth API.

    G-3: previously imported a non-existent ``app.core.security.jwt``
    module with sync call shapes — TypeError on every run. Now uses the
    real async ``jwt_service``/``jwt_utils`` API. Verification benchmarks
    the pure-crypto path (``decode_verified_token``): full ``verify_token``
    needs a DB session + Redis and is not meaningful in a standalone
    baseline script.
    """
    from app.core.security.jwt_service import create_access_token
    from app.core.security.jwt_utils import decode_verified_token

    results = {}

    # JWT creation benchmark
    async def create_jwt():
        await create_access_token(
            {"sub": "test-user-id", "tenant_id": "test-tenant", "token_version": 0},
        )

    results["jwt_token_creation"] = await benchmark_async_function(create_jwt, "jwt_token_creation")

    # JWT verification benchmark (signature + claims decode, no DB)
    token = await create_access_token(
        {"sub": "test-user-id", "tenant_id": "test-tenant", "token_version": 0},
    )

    async def verify_jwt():
        await decode_verified_token(token)

    results["jwt_token_verification"] = await benchmark_async_function(verify_jwt, "jwt_token_verification")

    return results


async def run_bcrypt_benchmarks() -> dict[str, BenchmarkResult]:
    """Run bcrypt benchmarks against the real password API.

    G-3: previously called a non-existent sync ``hash_password``; the
    real API is async ``get_password_hash`` / ``verify_password``.
    """
    from app.core.security.password import get_password_hash, verify_password

    results = {}

    # Hash password (expensive operation)
    async def hash_pw():
        await get_password_hash("test-password-123!")

    results["bcrypt_hash"] = await benchmark_async_function(
        hash_pw,
        "bcrypt_hash",
        warmup_rounds=2,
        min_rounds=10,
        max_time_seconds=30.0,
    )

    # Verify password
    hashed = await get_password_hash("test-password-123!")

    async def verify_pw():
        await verify_password("test-password-123!", hashed)

    results["bcrypt_verify"] = await benchmark_async_function(
        verify_pw,
        "bcrypt_verify",
        warmup_rounds=2,
        min_rounds=10,
        max_time_seconds=30.0,
    )

    return results


def run_serialization_benchmarks() -> dict[str, BenchmarkResult]:
    """Run Pydantic serialization benchmarks."""
    from pydantic import BaseModel

    class UserResponse(BaseModel):
        id: str
        email: str
        name: str
        is_active: bool
        created_at: str

    results = {}

    # Model creation/validation
    data = {
        "id": "user-123",
        "email": "test@example.com",
        "name": "Test User",
        "is_active": True,
        "created_at": "2026-01-13T00:00:00Z",
    }

    def create_model():
        UserResponse.model_validate(data)

    results["pydantic_validation"] = benchmark_function(create_model, "pydantic_validation")

    # Model serialization
    model = UserResponse.model_validate(data)

    def serialize_model():
        model.model_dump_json()

    results["pydantic_serialization"] = benchmark_function(serialize_model, "pydantic_serialization")

    return results


async def run_cache_benchmarks() -> dict[str, BenchmarkResult]:
    """Run cache-related benchmarks."""
    results = {}

    # Simulate cache key generation
    def generate_cache_key():
        parts = ["user", "tenant-123", "permissions", "v1"]
        return ":".join(parts)

    results["cache_key_generation"] = benchmark_function(generate_cache_key, "cache_key_generation")

    # JSON serialization for cache
    import json

    cache_data = {
        "user_id": "user-123",
        "permissions": ["read", "write", "admin"],
        "tenant_id": "tenant-456",
        "expires_at": 1736726400,
    }

    def serialize_cache():
        json.dumps(cache_data)

    results["cache_serialization"] = benchmark_function(serialize_cache, "cache_serialization")

    def deserialize_cache():
        json.loads('{"user_id":"user-123","permissions":["read","write","admin"]}')

    results["cache_deserialization"] = benchmark_function(deserialize_cache, "cache_deserialization")

    return results


async def run_all_benchmarks(report: BaselineReport) -> None:
    """Run all benchmark suites."""
    print("Running JWT benchmarks...")
    report.benchmarks.update(await run_jwt_benchmarks())

    print("Running bcrypt benchmarks (this takes ~60 seconds)...")
    report.benchmarks.update(await run_bcrypt_benchmarks())

    print("Running serialization benchmarks...")
    report.benchmarks.update(run_serialization_benchmarks())

    print("Running cache benchmarks...")
    cache_results = await run_cache_benchmarks()
    report.benchmarks.update(cache_results)


def print_report_summary(report: BaselineReport) -> None:
    """Print a summary of the benchmark results."""
    print("\n" + "=" * 70)
    print(f"BASELINE PERFORMANCE REPORT - Round {report.round_number}")
    print("=" * 70)
    print(f"Captured: {report.captured_at}")
    print(f"Git: {report.git_branch}@{report.git_commit} {'(dirty)' if report.git_dirty else ''}")
    print(f"Python: {report.python_version} on {report.platform_info}")
    print(f"CPU: {report.cpu_info}")
    print("-" * 70)
    print(f"{'Benchmark':<30} {'Mean':>10} {'P50':>10} {'P95':>10} {'P99':>10} {'Ops/s':>10}")
    print("-" * 70)

    for name, result in sorted(report.benchmarks.items()):
        print(
            f"{name:<30} {result.mean_ms:>9.3f}ms {result.p50_ms:>9.3f}ms "
            f"{result.p95_ms:>9.3f}ms {result.p99_ms:>9.3f}ms {result.ops_per_sec:>10.0f}"
        )

    print("=" * 70)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Capture baseline performance metrics")
    parser.add_argument(
        "--round",
        "-r",
        type=int,
        required=True,
        help="Optimization round number (e.g., 1 for PERF-ADV-R1)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Output file path (default: tests/performance/baselines/baseline_perf_r{N}.json)",
    )
    parser.add_argument(
        "--skip-bcrypt",
        action="store_true",
        help="Skip slow bcrypt benchmarks",
    )

    args = parser.parse_args()

    # Initialize report
    commit, branch, dirty = get_git_info()
    report = BaselineReport(
        round_number=args.round,
        git_commit=commit,
        git_branch=branch,
        git_dirty=dirty,
        cpu_info=get_cpu_info(),
        thresholds={
            "users_me_ms": 15.0,
            "auth_refresh_ms": 10.0,
            "ai_chat_db_ms": 100.0,
            "cache_ms": 5.0,
            "serialization_ms": 0.1,
        },
    )

    # Run benchmarks
    print(f"\nCapturing baseline metrics for Round {args.round}...")
    asyncio.run(run_all_benchmarks(report))

    # Print summary
    print_report_summary(report)

    # Save to file
    output_path = args.output or Path(f"tests/performance/baselines/baseline_perf_r{args.round}.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(report.to_dict(), f, indent=2)

    print(f"\nBaseline saved to: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
