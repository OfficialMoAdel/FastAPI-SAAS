"""Baseline Performance Metrics - FOUNDATION.

CRITICAL: This module captures performance baselines BEFORE any optimization.
Run this FIRST to establish before/after comparison data.

Captures 6 metric categories:
1. JSON Serialization: stdlib vs orjson latency
2. Auth Flow Latency: login, refresh, /users/me breakdown
3. Cache Operations: Redis GET/SET, tenant invalidation
4. Middleware Overhead: tenant resolution, rate limit, auth timing
5. Password Validation: regex compilation overhead
6. Pydantic Conversion: model_validate + model_dump time

Usage:
    uv run python -m tests.benchmarks.baseline_metrics
    uv run python -m tests.benchmarks.baseline_metrics --output results.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.benchmarks.percentiles import nearest_rank  # noqa: E402


@dataclass
class BenchmarkResult:
    """Result from a single benchmark."""

    name: str
    category: str
    iterations: int
    times_ns: list[int] = field(default_factory=list)

    @property
    def times_us(self) -> list[float]:
        """Times in microseconds."""
        return [t / 1000 for t in self.times_ns]

    @property
    def times_ms(self) -> list[float]:
        """Times in milliseconds."""
        return [t / 1_000_000 for t in self.times_ns]

    def percentile(self, p: float) -> float:
        """Nearest-rank percentile in milliseconds. ``p`` is 0-100.

        ESTIMATOR CHANGE - READ BEFORE COMPARING ARTEFACTS. This used to
        interpolate linearly between the two bracketing samples, which returns
        values that are frequently not in the sample at all, so a p99 could be
        quoted for a cost no iteration actually paid. It now delegates to
        ``src.benchmarks.percentiles.nearest_rank``, which always returns an
        OBSERVED sample.

        Consequences, stated rather than absorbed:

        - The returned percentile is now always a measured value, so it may be
          LARGER than the old figure whenever the cost rose monotonically
          (interpolation reads below the upper neighbour) and SMALLER when the
          cost fell. Neither direction is a regression on its own.
        - ``p50`` at an even sample count changes meaning: interpolation
          returned the midpoint of the two central samples; nearest rank
          returns the lower of them.
        - A percentile of an empty result used to return ``0.0``. It now
          raises ``ValueError``, because a 0.0 ms percentile reads as a
          measurement when nothing was measured. ``to_dict`` still emits the
          ``"No timing data collected"`` form for that case.

        Artefacts written before this change are therefore NOT comparable with
        artefacts written after it on any percentile field. Treat the estimator
        name (or the harness commit SHA) as part of the measurement
        provenance, the same way a timing protocol already is.
        """
        return nearest_rank(self.times_ms, p / 100)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        if not self.times_ns:
            return {
                "name": self.name,
                "category": self.category,
                "iterations": self.iterations,
                "error": "No timing data collected",
            }

        return {
            "name": self.name,
            "category": self.category,
            "iterations": self.iterations,
            "stats_ms": {
                "min": min(self.times_ms),
                "max": max(self.times_ms),
                "mean": statistics.mean(self.times_ms),
                "median": statistics.median(self.times_ms),
                "stdev": statistics.stdev(self.times_ms) if len(self.times_ms) > 1 else 0,
                "p50": self.percentile(50),
                "p95": self.percentile(95),
                "p99": self.percentile(99),
            },
            "stats_us": {
                "min": min(self.times_us),
                "max": max(self.times_us),
                "mean": statistics.mean(self.times_us),
                "median": statistics.median(self.times_us),
            },
            "ops_per_sec": 1_000_000_000 / statistics.mean(self.times_ns) if self.times_ns else 0,
        }


class BaselineMetrics:
    """Baseline performance metrics collector."""

    def __init__(self, iterations: int = 1000, warmup: int = 100):
        self.iterations = iterations
        self.warmup = warmup
        self.results: list[BenchmarkResult] = []

    def _benchmark(self, name: str, category: str, func: Any, *args: Any, **kwargs: Any) -> BenchmarkResult:
        """Run a synchronous benchmark."""
        result = BenchmarkResult(name=name, category=category, iterations=self.iterations)

        # Warmup
        for _ in range(self.warmup):
            func(*args, **kwargs)

        # Actual benchmark
        for _ in range(self.iterations):
            start = time.perf_counter_ns()
            func(*args, **kwargs)
            end = time.perf_counter_ns()
            result.times_ns.append(end - start)

        self.results.append(result)
        return result

    async def _async_benchmark(
        self, name: str, category: str, func: Any, *args: Any, **kwargs: Any
    ) -> BenchmarkResult:
        """Run an async benchmark."""
        result = BenchmarkResult(name=name, category=category, iterations=self.iterations)

        # Warmup
        for _ in range(self.warmup):
            await func(*args, **kwargs)

        # Actual benchmark
        for _ in range(self.iterations):
            start = time.perf_counter_ns()
            await func(*args, **kwargs)
            end = time.perf_counter_ns()
            result.times_ns.append(end - start)

        self.results.append(result)
        return result

    # =========================================================================
    # 1. JSON Serialization Benchmarks
    # =========================================================================

    def benchmark_json_serialization(self) -> None:
        """Benchmark JSON serialization: stdlib vs orjson."""
        print("\n[CATEGORY 1] JSON Serialization")
        print("-" * 50)

        # Test payloads
        small_payload = {"id": 1, "name": "test", "active": True}
        medium_payload = {
            "id": 1,
            "name": "test user",
            "email": "test@example.com",
            "profile": {"avatar": "url", "bio": "text" * 50},
            "settings": {f"key_{i}": f"value_{i}" for i in range(20)},
        }
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

        # stdlib json
        import json as stdlib_json

        for name, payload in [
            ("small", small_payload),
            ("medium", medium_payload),
            ("large", large_payload),
        ]:
            result = self._benchmark(
                f"stdlib_json_dumps_{name}",
                "json_serialization",
                stdlib_json.dumps,
                payload,
            )
            self._print_result(result)

            result = self._benchmark(
                f"stdlib_json_loads_{name}",
                "json_serialization",
                stdlib_json.loads,
                stdlib_json.dumps(payload),
            )
            self._print_result(result)

        # orjson
        try:
            import orjson

            for name, payload in [
                ("small", small_payload),
                ("medium", medium_payload),
                ("large", large_payload),
            ]:
                result = self._benchmark(
                    f"orjson_dumps_{name}",
                    "json_serialization",
                    orjson.dumps,
                    payload,
                )
                self._print_result(result)

                result = self._benchmark(
                    f"orjson_loads_{name}",
                    "json_serialization",
                    orjson.loads,
                    orjson.dumps(payload),
                )
                self._print_result(result)
        except ImportError:
            print("  [WARN]  orjson not installed, skipping orjson benchmarks")

        # Project's json_utils (should use orjson)
        try:
            from src.app.core.json_utils import json_dumps, json_loads

            for name, payload in [
                ("small", small_payload),
                ("medium", medium_payload),
                ("large", large_payload),
            ]:
                result = self._benchmark(
                    f"json_utils_dumps_{name}",
                    "json_serialization",
                    json_dumps,
                    payload,
                )
                self._print_result(result)

                result = self._benchmark(
                    f"json_utils_loads_{name}",
                    "json_serialization",
                    json_loads,
                    json_dumps(payload),
                )
                self._print_result(result)
        except ImportError as e:
            print(f"  [WARN]  Could not import json_utils: {e}")

    # =========================================================================
    # 2. Auth Flow Latency Benchmarks
    # =========================================================================

    async def benchmark_auth_flow(self) -> None:
        """Benchmark auth flow components."""
        print("\n[CATEGORY] 2: Auth Flow Latency")
        print("-" * 50)

        try:
            from src.app.core.security import create_access_token, create_refresh_token

            # JWT token creation
            token_data = {"sub": "testuser", "tier_id": 1, "tier_name": "pro"}

            result = await self._async_benchmark(
                "jwt_access_token_create",
                "auth_flow",
                create_access_token,
                token_data,
            )
            self._print_result(result)

            result = await self._async_benchmark(
                "jwt_refresh_token_create",
                "auth_flow",
                create_refresh_token,
                token_data,
            )
            self._print_result(result)

            # JWT verification (without DB)
            import jwt

            from src.app.core.config import settings

            access_token = await create_access_token(token_data)

            def verify_jwt_sync():
                return jwt.decode(
                    access_token,
                    settings.SECRET_KEY.get_secret_value(),
                    algorithms=[settings.ALGORITHM],
                    audience="fastapi-saas-api",
                )

            result = self._benchmark(
                "jwt_decode_only",
                "auth_flow",
                verify_jwt_sync,
            )
            self._print_result(result)

        except ImportError as e:
            print(f"  [WARN]  Could not import auth modules: {e}")

        # Password hashing (expensive operation)
        try:
            import bcrypt

            password = "TestPassword123!"

            # Single hash operation (for reference)
            def hash_password():
                return bcrypt.hashpw(password.encode(), bcrypt.gensalt())

            # Reduce iterations for expensive bcrypt
            old_iterations = self.iterations
            self.iterations = 10  # bcrypt is ~250ms per op

            result = self._benchmark(
                "bcrypt_hash",
                "auth_flow",
                hash_password,
            )
            self._print_result(result)

            # Verify password
            hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt())

            def verify_password():
                return bcrypt.checkpw(password.encode(), hashed)

            result = self._benchmark(
                "bcrypt_verify",
                "auth_flow",
                verify_password,
            )
            self._print_result(result)

            self.iterations = old_iterations

        except ImportError as e:
            print(f"  [WARN]  Could not import bcrypt: {e}")

    # =========================================================================
    # 3. Cache Operations Benchmarks
    # =========================================================================

    async def benchmark_cache_operations(self) -> None:
        """Benchmark cache operations (mock Redis for isolated testing)."""
        print("\n[CATEGORY] 3: Cache Operations")
        print("-" * 50)

        # Mock Redis operations to measure serialization overhead
        try:
            from src.app.core.json_utils import json_dumps, json_loads

            # Simulate cache set/get with serialization
            cache_data = {
                "id": 1,
                "username": "testuser",
                "email": "test@example.com",
                "tier_id": 1,
                "is_active": True,
            }

            def cache_serialize():
                return json_dumps(cache_data)

            result = self._benchmark(
                "cache_serialize",
                "cache_operations",
                cache_serialize,
            )
            self._print_result(result)

            serialized = json_dumps(cache_data)

            def cache_deserialize():
                return json_loads(serialized)

            result = self._benchmark(
                "cache_deserialize",
                "cache_operations",
                cache_deserialize,
            )
            self._print_result(result)

            # Full round-trip
            def cache_roundtrip():
                s = json_dumps(cache_data)
                return json_loads(s)

            result = self._benchmark(
                "cache_roundtrip",
                "cache_operations",
                cache_roundtrip,
            )
            self._print_result(result)

        except ImportError as e:
            print(f"  [WARN]  Could not import cache modules: {e}")

        # Tenant cache key generation
        try:
            from src.app.core.utils.cache import tenant_cache_key, tenant_cache_pattern

            result = self._benchmark(
                "tenant_cache_key_gen",
                "cache_operations",
                tenant_cache_key,
                123,
                "user",
                456,
            )
            self._print_result(result)

            result = self._benchmark(
                "tenant_cache_pattern_gen",
                "cache_operations",
                tenant_cache_pattern,
                123,
                "user",
            )
            self._print_result(result)

        except ImportError as e:
            print(f"  [WARN]  Could not import tenant cache utils: {e}")

    # =========================================================================
    # 4. Middleware Overhead Benchmarks
    # =========================================================================

    def benchmark_middleware_overhead(self) -> None:
        """Benchmark middleware components."""
        print("\n[CATEGORY] 4: Middleware Overhead")
        print("-" * 50)

        # Tenant path exemption check
        try:
            from src.app.middleware.tenant import is_path_exempt

            test_paths = [
                "/api/v1/users/me",
                "/docs",
                "/api/v1/health",
                "/api/v1/tenants/create",
                "/api/v1/posts/123",
            ]

            for path in test_paths:
                result = self._benchmark(
                    f"path_exempt_check_{path.replace('/', '_')}",
                    "middleware",
                    is_path_exempt,
                    path,
                )
                self._print_result(result)

        except ImportError as e:
            print(f"  [WARN]  Could not import tenant middleware: {e}")

        # IP address parsing (rate limit)
        try:
            import ipaddress

            test_ips = ["192.168.1.1", "10.0.0.1", "8.8.8.8", "2001:db8::1"]

            for ip in test_ips:

                def parse_ip():
                    return ipaddress.ip_address(ip)

                result = self._benchmark(
                    f"ip_parse_{ip.replace('.', '_').replace(':', '_')}",
                    "middleware",
                    parse_ip,
                )
                self._print_result(result)

        except Exception as e:
            print(f"  [WARN]  IP parsing error: {e}")

    # =========================================================================
    # 5. Password Validation Benchmarks
    # =========================================================================

    def benchmark_password_validation(self) -> None:
        """Benchmark password validation regex."""
        print("\n[CATEGORY] 5: Password Validation")
        print("-" * 50)

        password = "Str1ngst!Password123"

        # Individual regex patterns (as used in UserCreate schema)
        patterns = [
            (r"[a-z]", "lowercase"),
            (r"[A-Z]", "uppercase"),
            (r"\d", "digit"),
            (r"[^a-zA-Z0-9]", "special"),
        ]

        for pattern, name in patterns:
            compiled = re.compile(pattern)

            def search_pattern():
                return compiled.search(password)

            result = self._benchmark(
                f"regex_{name}_compiled",
                "password_validation",
                search_pattern,
            )
            self._print_result(result)

            # Without pre-compilation
            def search_raw():
                return re.search(pattern, password)

            result = self._benchmark(
                f"regex_{name}_raw",
                "password_validation",
                search_raw,
            )
            self._print_result(result)

        # Full validation (all 4 checks)
        def full_validation():
            return (
                re.search(r"[a-z]", password)
                and re.search(r"[A-Z]", password)
                and re.search(r"\d", password)
                and re.search(r"[^a-zA-Z0-9]", password)
            )

        result = self._benchmark(
            "password_full_validation",
            "password_validation",
            full_validation,
        )
        self._print_result(result)

    # =========================================================================
    # 6. Pydantic Conversion Benchmarks
    # =========================================================================

    def benchmark_pydantic_conversion(self) -> None:
        """Benchmark Pydantic model validation and serialization."""
        print("\n[CATEGORY] 6: Pydantic Conversion")
        print("-" * 50)

        try:
            from pydantic import BaseModel, EmailStr

            # Simple model similar to UserRead
            class UserReadBench(BaseModel):
                id: int
                name: str
                username: str
                email: EmailStr
                profile_image_url: str
                tier_id: int | None

            user_data = {
                "id": 1,
                "name": "Test User",
                "username": "testuser",
                "email": "test@example.com",
                "profile_image_url": "https://example.com/avatar.jpg",
                "tier_id": 1,
            }

            # model_validate (dict -> model)
            def validate_user():
                return UserReadBench.model_validate(user_data)

            result = self._benchmark(
                "pydantic_model_validate",
                "pydantic",
                validate_user,
            )
            self._print_result(result)

            # model_dump (model -> dict)
            user_model = UserReadBench.model_validate(user_data)

            def dump_user():
                return user_model.model_dump()

            result = self._benchmark(
                "pydantic_model_dump",
                "pydantic",
                dump_user,
            )
            self._print_result(result)

            # model_dump_json (model -> JSON string)
            def dump_user_json():
                return user_model.model_dump_json()

            result = self._benchmark(
                "pydantic_model_dump_json",
                "pydantic",
                dump_user_json,
            )
            self._print_result(result)

            # Full round-trip
            def full_roundtrip():
                m = UserReadBench.model_validate(user_data)
                return m.model_dump()

            result = self._benchmark(
                "pydantic_full_roundtrip",
                "pydantic",
                full_roundtrip,
            )
            self._print_result(result)

            # Test with actual project schemas if available
            try:
                # UserRead requires created_at field from base schema
                from datetime import UTC, datetime

                from src.app.modules.users.schemas import UserRead
                real_user_data = {
                    **user_data,
                    "created_at": datetime.now(UTC),
                }

                def validate_real_user():
                    return UserRead.model_validate(real_user_data)

                result = self._benchmark(
                    "pydantic_real_userread_validate",
                    "pydantic",
                    validate_real_user,
                )
                self._print_result(result)

            except (ImportError, Exception) as e:
                print(f"  [WARN]  Could not benchmark real UserRead: {e}")

        except ImportError as e:
            print(f"  [WARN]  Could not import Pydantic: {e}")

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def _print_result(self, result: BenchmarkResult) -> None:
        """Print a single benchmark result."""
        stats = result.to_dict()
        if "error" in stats:
            print(f"  [ERR] {result.name}: {stats['error']}")
            return

        ms = stats["stats_ms"]
        print(
            f"  [OK] {result.name}: "
            f"mean={ms['mean']:.4f}ms, "
            f"p95={ms['p95']:.4f}ms, "
            f"p99={ms['p99']:.4f}ms, "
            f"ops={stats['ops_per_sec']:.0f}/s"
        )

    def get_machine_info(self) -> dict[str, str]:
        """Get machine information for reproducibility."""
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
        """Convert all results to JSON-serializable dict."""
        from datetime import UTC
        return {
            "metadata": {
                "created_at": datetime.now(UTC).isoformat(),
                "iterations": self.iterations,
                "warmup": self.warmup,
                "run_id": str(uuid4()),
            },
            "machine_info": self.get_machine_info(),
            "results": [r.to_dict() for r in self.results],
            "summary": self._generate_summary(),
        }

    def _generate_summary(self) -> dict[str, Any]:
        """Generate summary statistics by category."""
        summary: dict[str, dict] = {}

        for result in self.results:
            if result.category not in summary:
                summary[result.category] = {"benchmarks": [], "total_ops_per_sec": 0}

            stats = result.to_dict()
            summary[result.category]["benchmarks"].append(result.name)
            if "ops_per_sec" in stats:
                summary[result.category]["total_ops_per_sec"] += stats["ops_per_sec"]

        return summary

    async def run_all(self) -> None:
        """Run all benchmarks."""
        print("=" * 60)
        print("[*] BASELINE PERFORMANCE METRICS")
        print(f"    Iterations: {self.iterations}, Warmup: {self.warmup}")
        print("=" * 60)

        # Category 1: JSON Serialization
        self.benchmark_json_serialization()

        # Category 2: Auth Flow (async)
        await self.benchmark_auth_flow()

        # Category 3: Cache Operations (async)
        await self.benchmark_cache_operations()

        # Category 4: Middleware Overhead
        self.benchmark_middleware_overhead()

        # Category 5: Password Validation
        self.benchmark_password_validation()

        # Category 6: Pydantic Conversion
        self.benchmark_pydantic_conversion()

        print("\n" + "=" * 60)
        print("[+] BASELINE METRICS COLLECTION COMPLETE")
        print("=" * 60)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Collect baseline performance metrics")
    parser.add_argument(
        "--iterations",
        "-i",
        type=int,
        default=1000,
        help="Number of iterations per benchmark (default: 1000)",
    )
    parser.add_argument(
        "--warmup",
        "-w",
        type=int,
        default=100,
        help="Number of warmup iterations (default: 100)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="tests/benchmarks/baselines/baseline_metrics.json",
        help="Output file path (default: tests/benchmarks/baselines/baseline_metrics.json)",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress console output",
    )

    args = parser.parse_args()

    metrics = BaselineMetrics(iterations=args.iterations, warmup=args.warmup)

    # Run benchmarks
    asyncio.run(metrics.run_all())

    # Save results
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(metrics.to_json(), f, indent=2)

    print(f"\n[FILE] Results saved to: {output_path}")

    # Print summary
    summary = metrics._generate_summary()
    print("\n[STATS] SUMMARY BY CATEGORY:")
    for category, data in summary.items():
        print(f"  {category}: {len(data['benchmarks'])} benchmarks, {data['total_ops_per_sec']:.0f} total ops/sec")


if __name__ == "__main__":
    main()
