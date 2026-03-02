"""Middleware performance profiler for investigating latency bottlenecks.

This script profiles each middleware component individually to identify
where time is spent in the request lifecycle. It measures:

1. Path exemption checks (TENANT_EXEMPT_PATHS, etc.)
2. Tenant resolution (cache hit vs DB query)
3. Rate limit checks (Redis Lua script)
4. JWT decoding and caching
5. Security header addition
6. Request ID generation

Output: tests/benchmarks/baselines/middleware_profile.json
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import platform
import statistics
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


@dataclass
class ProfileResult:
    """Result of profiling a middleware operation."""

    name: str
    category: str
    iterations: int = 100
    times_us: list[float] = field(default_factory=list)  # microseconds

    @property
    def mean(self) -> float:
        return statistics.mean(self.times_us) if self.times_us else 0.0

    @property
    def p50(self) -> float:
        return statistics.median(self.times_us) if self.times_us else 0.0

    @property
    def p95(self) -> float:
        if not self.times_us:
            return 0.0
        sorted_times = sorted(self.times_us)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    @property
    def p99(self) -> float:
        if not self.times_us:
            return 0.0
        sorted_times = sorted(self.times_us)
        idx = int(len(sorted_times) * 0.99)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    @property
    def min_time(self) -> float:
        return min(self.times_us) if self.times_us else 0.0

    @property
    def max_time(self) -> float:
        return max(self.times_us) if self.times_us else 0.0

    @property
    def stdev(self) -> float:
        return statistics.stdev(self.times_us) if len(self.times_us) > 1 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "iterations": self.iterations,
            "mean_us": round(self.mean, 4),
            "p50_us": round(self.p50, 4),
            "p95_us": round(self.p95, 4),
            "p99_us": round(self.p99, 4),
            "min_us": round(self.min_time, 4),
            "max_us": round(self.max_time, 4),
            "stdev_us": round(self.stdev, 4),
        }


class MiddlewareProfiler:
    """Profiler for measuring middleware operation latencies."""

    def __init__(self, iterations: int = 100, warmup: int = 10) -> None:
        self.iterations = iterations
        self.warmup = warmup
        self.results: list[ProfileResult] = []

    def _profile_sync(self, name: str, category: str, func: Any, *args: Any, **kwargs: Any) -> ProfileResult:
        """Profile a synchronous operation."""
        result = ProfileResult(name=name, category=category, iterations=self.iterations)

        # Warmup
        for _ in range(self.warmup):
            func(*args, **kwargs)

        # Profile
        for _ in range(self.iterations):
            start = time.perf_counter_ns()
            func(*args, **kwargs)
            end = time.perf_counter_ns()
            result.times_us.append((end - start) / 1000)  # Convert to microseconds

        self.results.append(result)
        return result

    async def _profile_async(
        self, name: str, category: str, func: Any, *args: Any, **kwargs: Any
    ) -> ProfileResult:
        """Profile an asynchronous operation."""
        result = ProfileResult(name=name, category=category, iterations=self.iterations)

        # Warmup
        for _ in range(self.warmup):
            await func(*args, **kwargs)

        # Profile
        for _ in range(self.iterations):
            start = time.perf_counter_ns()
            await func(*args, **kwargs)
            end = time.perf_counter_ns()
            result.times_us.append((end - start) / 1000)  # Convert to microseconds

        self.results.append(result)
        return result

    def profile_path_exemption_checks(self) -> None:
        """Profile path exemption check operations."""
        print("\n[CATEGORY] 1: Path Exemption Checks")
        print("-" * 50)

        from src.app.middleware.tenant import TENANT_EXEMPT_PATHS, TENANT_EXEMPT_PREFIXES, is_path_exempt

        # Test paths
        exempt_exact = "/api/v1/health"
        exempt_prefix = "/api/v1/tenants/create"
        non_exempt = "/api/v1/users/me"

        # Profile frozenset membership (O(1))
        def frozenset_check() -> bool:
            return exempt_exact in TENANT_EXEMPT_PATHS

        result = self._profile_sync("frozenset_membership", "path_exemption", frozenset_check)
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

        # Profile prefix iteration
        def prefix_check() -> bool:
            for prefix in TENANT_EXEMPT_PREFIXES:
                if exempt_prefix.startswith(prefix):
                    return True
            return False

        result = self._profile_sync("prefix_iteration", "path_exemption", prefix_check)
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

        # Profile full is_path_exempt function
        result = self._profile_sync("is_path_exempt_match", "path_exemption", is_path_exempt, exempt_exact)
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

        result = self._profile_sync("is_path_exempt_prefix", "path_exemption", is_path_exempt, exempt_prefix)
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

        result = self._profile_sync("is_path_exempt_no_match", "path_exemption", is_path_exempt, non_exempt)
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

    def profile_ip_parsing(self) -> None:
        """Profile IP address parsing and trusted proxy checks."""
        print("\n[CATEGORY] 2: IP Parsing & Trusted Proxy")
        print("-" * 50)

        # Simulate RateLimitMiddleware IP parsing logic
        TRUSTED_PROXY_IPS: set[str] = {"127.0.0.1", "::1"}
        TRUSTED_PROXY_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
        )

        def is_trusted_proxy(ip_str: str) -> bool:
            if ip_str in TRUSTED_PROXY_IPS:
                return True
            try:
                ip = ipaddress.ip_address(ip_str)
                return any(ip in network for network in TRUSTED_PROXY_NETWORKS)
            except ValueError:
                return False

        # Profile set membership (fast path)
        result = self._profile_sync("trusted_proxy_set_hit", "ip_parsing", is_trusted_proxy, "127.0.0.1")
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

        # Profile network containment check
        result = self._profile_sync("trusted_proxy_network_check", "ip_parsing", is_trusted_proxy, "10.5.2.100")
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

        # Profile external IP (full check, no match)
        result = self._profile_sync("trusted_proxy_external", "ip_parsing", is_trusted_proxy, "8.8.8.8")
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

        # Profile IP address parsing alone
        def parse_ip(ip_str: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
            return ipaddress.ip_address(ip_str)

        result = self._profile_sync("ip_address_parse", "ip_parsing", parse_ip, "192.168.1.50")
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

    def profile_jwt_operations(self) -> None:
        """Profile JWT token operations."""
        print("\n[CATEGORY] 3: JWT Operations")
        print("-" * 50)

        from src.app.core.security.jwt_utils import decode_jwt_token, extract_token_from_header

        # Test data - shortened JWT for line length
        auth_header = (
            "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIiwiYXVkIjoiZmFzdGFwaS1zYWFzLWFwaSIsImV4cCI6OTk5OTk5OTk5OX0."
            "placeholder"
        )
        invalid_header = "Basic dXNlcjpwYXNz"

        # Profile header extraction
        result = self._profile_sync("extract_bearer_token", "jwt", extract_token_from_header, auth_header)
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

        result = self._profile_sync("extract_invalid_header", "jwt", extract_token_from_header, invalid_header)
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

        # Profile JWT decode (this will fail signature but still measures decode overhead)
        token = extract_token_from_header(auth_header)
        if token:
            result = self._profile_sync("jwt_decode_invalid_sig", "jwt", decode_jwt_token, token)
            print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

    def profile_uuid_generation(self) -> None:
        """Profile UUID generation for request IDs."""
        print("\n[CATEGORY] 4: Request ID Generation")
        print("-" * 50)

        import uuid

        result = self._profile_sync("uuid4_generation", "request_id", uuid.uuid4)
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

        # Profile string conversion
        def uuid_to_str() -> str:
            return str(uuid.uuid4())

        result = self._profile_sync("uuid4_to_string", "request_id", uuid_to_str)
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

    def profile_security_headers(self) -> None:
        """Profile security header string operations."""
        print("\n[CATEGORY] 5: Security Headers")
        print("-" * 50)

        # Simulate header dict operations (what the middleware does)
        DEFAULT_CSP = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "font-src 'self' data:; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )

        def add_security_headers() -> dict[str, str]:
            h = {}
            h["X-Content-Type-Options"] = "nosniff"
            h["X-Frame-Options"] = "DENY"
            h["X-XSS-Protection"] = "1; mode=block"
            h["Referrer-Policy"] = "strict-origin-when-cross-origin"
            h["Permissions-Policy"] = (
                "accelerometer=(), camera=(), geolocation=(), "
                "gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
            )
            h["Content-Security-Policy"] = DEFAULT_CSP
            return h

        result = self._profile_sync("add_security_headers", "security_headers", add_security_headers)
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

    def profile_cache_key_generation(self) -> None:
        """Profile cache key generation patterns."""
        print("\n[CATEGORY] 6: Cache Key Generation")
        print("-" * 50)

        # Tenant cache key
        def tenant_cache_key(slug: str) -> str:
            return f"tenant:slug:{slug}"

        result = self._profile_sync("tenant_cache_key", "cache_keys", tenant_cache_key, "acme-corp")
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

        # Rate limit key
        def rate_limit_key(user_id: int, window_start: int) -> str:
            return f"ratelimit:user:{user_id}:{window_start}"

        result = self._profile_sync("rate_limit_key", "cache_keys", rate_limit_key, 12345, 1705000000)
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

        # Session key
        def session_key(user_id: int) -> str:
            return f"user_sessions:{user_id}"

        result = self._profile_sync("session_key", "cache_keys", session_key, 12345)
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

    def profile_json_cache_operations(self) -> None:
        """Profile JSON serialization for cache data."""
        print("\n[CATEGORY] 7: JSON Cache Serialization")
        print("-" * 50)

        import json as stdlib_json

        # Tenant cache data (what TenantMiddleware caches)
        tenant_data = {"id": 1, "slug": "acme-corp", "is_active": True}

        # Session data (what SessionManager caches)
        session_data = {
            "session_id": "abc123-def456-ghi789",
            "device_info": "Chrome 120 / Windows",
            "created_at": datetime.now(UTC).isoformat(),
        }

        # Session list (multiple sessions)
        session_list = [session_data] * 3

        # Profile tenant data serialization
        result = self._profile_sync("tenant_json_dumps", "json_cache", stdlib_json.dumps, tenant_data)
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

        tenant_json = stdlib_json.dumps(tenant_data)
        result = self._profile_sync("tenant_json_loads", "json_cache", stdlib_json.loads, tenant_json)
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

        # Profile session data serialization
        result = self._profile_sync("session_json_dumps", "json_cache", stdlib_json.dumps, session_data)
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

        # Profile session list serialization
        result = self._profile_sync("session_list_json_dumps", "json_cache", stdlib_json.dumps, session_list)
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

        session_list_json = stdlib_json.dumps(session_list)
        result = self._profile_sync("session_list_json_loads", "json_cache", stdlib_json.loads, session_list_json)
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

    def profile_timestamp_operations(self) -> None:
        """Profile timestamp operations used in rate limiting."""
        print("\n[CATEGORY] 8: Timestamp Operations")
        print("-" * 50)

        # Rate limiter window calculation
        def get_window_start(period: int = 3600) -> int:
            current_timestamp = int(datetime.now(UTC).timestamp())
            return current_timestamp - (current_timestamp % period)

        result = self._profile_sync("rate_limit_window_calc", "timestamp", get_window_start)
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

        # ISO timestamp for session creation
        def iso_timestamp() -> str:
            return datetime.now(UTC).isoformat()

        result = self._profile_sync("iso_timestamp", "timestamp", iso_timestamp)
        print(f"  [OK] {result.name}: mean={result.mean:.4f}us, p95={result.p95:.4f}us")

    async def run_all_profiles(self) -> None:
        """Run all profiling benchmarks."""
        print("=" * 60)
        print("[*] MIDDLEWARE PERFORMANCE PROFILER")
        print("=" * 60)
        print(f"Iterations: {self.iterations} | Warmup: {self.warmup}")

        # Run sync profiles
        self.profile_path_exemption_checks()
        self.profile_ip_parsing()
        self.profile_jwt_operations()
        self.profile_uuid_generation()
        self.profile_security_headers()
        self.profile_cache_key_generation()
        self.profile_json_cache_operations()
        self.profile_timestamp_operations()

        # Save results
        self._save_results()

    def _save_results(self) -> None:
        """Save profiling results to JSON file."""
        output_dir = Path(__file__).parent / "baselines"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "middleware_profile.json"

        report = {
            "metadata": {
                "generated_at": datetime.now(UTC).isoformat(),
                "python_version": platform.python_version(),
                "platform": platform.system(),
                "machine": platform.machine(),
                "iterations": self.iterations,
                "warmup": self.warmup,
            },
            "results": [r.to_dict() for r in self.results],
            "summary": self._generate_summary(),
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        print("\n" + "=" * 60)
        print("[+] PROFILING COMPLETE")
        print("=" * 60)
        print(f"\n[FILE] Results saved to: {output_path}")

        # Print summary
        print("\n[STATS] SUMMARY BY CATEGORY:")
        for category, stats in report["summary"]["by_category"].items():
            print(f"  {category}: {stats['count']} ops, mean={stats['total_mean_us']:.2f}us")

    def _generate_summary(self) -> dict[str, Any]:
        """Generate summary statistics."""
        by_category: dict[str, dict[str, Any]] = {}

        for result in self.results:
            cat = result.category
            if cat not in by_category:
                by_category[cat] = {"count": 0, "total_mean_us": 0.0, "max_p99_us": 0.0}

            by_category[cat]["count"] += 1
            by_category[cat]["total_mean_us"] += result.mean
            by_category[cat]["max_p99_us"] = max(by_category[cat]["max_p99_us"], result.p99)

        # Calculate overall stats
        all_means = [r.mean for r in self.results]
        all_p99s = [r.p99 for r in self.results]

        return {
            "by_category": by_category,
            "overall": {
                "total_operations": len(self.results),
                "avg_mean_us": round(statistics.mean(all_means), 4) if all_means else 0.0,
                "max_p99_us": round(max(all_p99s), 4) if all_p99s else 0.0,
            },
        }


async def main() -> None:
    """Run the middleware profiler."""
    profiler = MiddlewareProfiler(iterations=100, warmup=10)
    await profiler.run_all_profiles()


if __name__ == "__main__":
    asyncio.run(main())
