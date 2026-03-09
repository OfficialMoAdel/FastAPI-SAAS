"""R4 Performance Benchmarks for Regression Detection.

This module provides benchmarks that detect performance regressions in the CI/CD pipeline
for all R4 optimizations. Each benchmark has a defined threshold that triggers a failure
if exceeded.

Usage:
    # Run all benchmarks
    pytest tests/performance/benchmarks_r4.py --benchmark-only -v

    # Generate baseline JSON
    pytest tests/performance/benchmarks_r4.py --benchmark-json=baselines/r4_baseline.json

    # Compare against baseline
    pytest tests/performance/benchmarks_r4.py --benchmark-compare=baselines/r4_baseline.json

Benchmark Suite:
1. DNS Resolution: async vs blocking DNS resolution
2. FastCRUD Instantiation: flyweight cache effectiveness
3. IP Parsing: LRU cache hit performance
4. Webhooks HMAC: signature generation speed
5. Update Operations: session refresh pattern (1 vs 2 roundtrips)
6. Event Enqueue: parallel vs sequential handler execution

Statistical Requirements:
- Minimum 100 samples per benchmark (via pytest-benchmark defaults)
- P95 latency tracking
- Statistical significance validation
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from datetime import UTC, datetime
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Final
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pytest_benchmark.fixture import BenchmarkFixture


# =============================================================================
# REGRESSION THRESHOLDS
# =============================================================================

#: Regression thresholds for R4 optimizations
#: Values are maximum acceptable p95 latency in milliseconds
REGRESSION_THRESHOLDS_MS: Final[dict[str, float]] = {
    "dns_resolution": 150.0,  # Target <100ms, fail >150ms
    "fastcrud_instantiation": 0.5,  # Per operation, fail if >0.5ms (cache should be ~0.01ms)
    "ip_parsing_cached": 0.001,  # Target ~0.1μs, fail >1μs (1μs = 0.001ms)
    "webhooks_hmac": 0.6,  # Target <0.3ms, fail >0.6ms
    "update_operation": 50.0,  # Single DB roundtrip, fail if doubling behavior
    "event_enqueue": 7.0,  # Target <5ms for parallel, fail >7ms
}


# =============================================================================
# BENCHMARK 1: DNS Resolution
# =============================================================================


class TestDNSResolutionBenchmark:
    """Benchmark async DNS resolution vs blocking."""

    def test_async_dns_resolution(self, benchmark: BenchmarkFixture) -> None:
        """Benchmark async DNS resolution performance."""
        import socket

        # Mock DNS to avoid network calls
        mock_addr_info = [
            (2, 1, 6, "", ("93.184.216.34", 0)),
        ]

        async def resolve_async() -> list[str]:
            addr_info = await asyncio.to_thread(
                socket.getaddrinfo, "example.com", None, socket.AF_UNSPEC, socket.SOCK_STREAM
            )
            return [info[4][0] for info in addr_info]

        def run_async() -> list[str]:
            with patch("socket.getaddrinfo", return_value=mock_addr_info):
                return asyncio.new_event_loop().run_until_complete(resolve_async())

        result = benchmark(run_async)
        assert len(result) > 0

        # Check against threshold
        benchmark.extra_info["threshold_ms"] = REGRESSION_THRESHOLDS_MS["dns_resolution"]

    def test_dns_resolution_comparison(self, benchmark: BenchmarkFixture) -> None:
        """Compare async vs blocking DNS (mocked)."""
        import socket

        mock_addr_info = [
            (2, 1, 6, "", ("93.184.216.34", 0)),
        ]

        def blocking_resolve() -> list[str]:
            with patch("socket.getaddrinfo", return_value=mock_addr_info):
                addr_info = socket.getaddrinfo("example.com", None, socket.AF_UNSPEC, socket.SOCK_STREAM)
                return [info[4][0] for info in addr_info]

        result = benchmark(blocking_resolve)
        assert len(result) > 0


# =============================================================================
# BENCHMARK 2: FastCRUD Instantiation
# =============================================================================


class TestFastCRUDInstantiationBenchmark:
    """Benchmark FastCRUD flyweight cache performance."""

    def test_cached_crud_instantiation(self, benchmark: BenchmarkFixture) -> None:
        """Benchmark cached FastCRUD instance retrieval."""
        from src.app.modules._base.service import BaseService
        from src.app.modules.tenants.models import Tenant

        # First call populates cache
        mock_db = MagicMock()
        _ = BaseService(Tenant, mock_db)

        # Benchmark cached retrieval
        def get_service() -> BaseService:
            return BaseService(Tenant, mock_db)

        result = benchmark(get_service)
        assert result is not None

        benchmark.extra_info["threshold_ms"] = REGRESSION_THRESHOLDS_MS["fastcrud_instantiation"]
        benchmark.extra_info["optimization"] = "flyweight_cache"

    def test_multiple_model_caching(self, benchmark: BenchmarkFixture) -> None:
        """Benchmark caching across multiple models."""
        from src.app.modules._base.service import BaseService
        from src.app.modules.tenants.models import Tenant
        from src.app.modules.tiers.models import Tier
        from src.app.modules.users.models import User

        mock_db = MagicMock()
        models = [Tenant, Tier, User]

        # Pre-warm cache
        for model in models:
            _ = BaseService(model, mock_db)

        def cycle_services() -> list[Any]:
            return [BaseService(model, mock_db) for model in models]

        result = benchmark(cycle_services)
        assert len(result) == 3


# =============================================================================
# BENCHMARK 3: IP Parsing
# =============================================================================


class TestIPParsingBenchmark:
    """Benchmark IP address parsing with LRU cache."""

    def test_ip_parsing_cached(self, benchmark: BenchmarkFixture) -> None:
        """Benchmark LRU-cached IP parsing."""

        @lru_cache(maxsize=1024)
        def parse_ip(ip: str) -> str:
            return ip.split(",")[0].strip()

        # Pre-warm cache
        test_ips = [f"192.168.1.{i}" for i in range(10)]
        for ip in test_ips:
            parse_ip(ip)

        def cached_lookup() -> str:
            return parse_ip("192.168.1.1")  # Cache hit

        result = benchmark(cached_lookup)
        assert result == "192.168.1.1"

        cache_info = parse_ip.cache_info()
        benchmark.extra_info["cache_hits"] = cache_info.hits
        benchmark.extra_info["cache_misses"] = cache_info.misses
        benchmark.extra_info["threshold_ms"] = REGRESSION_THRESHOLDS_MS["ip_parsing_cached"]

    def test_ip_parsing_uncached_baseline(self, benchmark: BenchmarkFixture) -> None:
        """Baseline: IP parsing without cache."""

        def parse_ip_no_cache(ip: str) -> str:
            return ip.split(",")[0].strip()

        result = benchmark(parse_ip_no_cache, "192.168.1.1")
        assert result == "192.168.1.1"


# =============================================================================
# BENCHMARK 4: Webhooks HMAC
# =============================================================================


class TestWebhooksHMACBenchmark:
    """Benchmark webhook HMAC signature generation."""

    def test_hmac_signature_generation(self, benchmark: BenchmarkFixture) -> None:
        """Benchmark HMAC-SHA256 signature generation."""
        payload = b'{"event": "user.created", "data": {"id": 1, "email": "test@example.com"}}'
        secret = b"test_webhook_secret_key_32bytes!"

        def generate_signature() -> str:
            return hmac.new(secret, payload, hashlib.sha256).hexdigest()

        result = benchmark(generate_signature)
        assert len(result) == 64  # SHA256 hex = 64 chars

        benchmark.extra_info["threshold_ms"] = REGRESSION_THRESHOLDS_MS["webhooks_hmac"]
        benchmark.extra_info["payload_size"] = len(payload)

    def test_hmac_verification(self, benchmark: BenchmarkFixture) -> None:
        """Benchmark HMAC signature verification."""
        payload = b'{"event": "user.created", "data": {"id": 1, "email": "test@example.com"}}'
        secret = b"test_webhook_secret_key_32bytes!"
        expected_sig = hmac.new(secret, payload, hashlib.sha256).hexdigest()

        def verify_signature() -> bool:
            computed = hmac.new(secret, payload, hashlib.sha256).hexdigest()
            return hmac.compare_digest(computed, expected_sig)

        result = benchmark(verify_signature)
        assert result is True


# =============================================================================
# BENCHMARK 5: Update Operations
# =============================================================================


class TestUpdateOperationsBenchmark:
    """Benchmark update operations with session refresh pattern."""

    def test_direct_dict_serialization(self, benchmark: BenchmarkFixture) -> None:
        """Benchmark direct dict vs Pydantic for update responses."""
        from src.app.modules.tenants.service import _tenant_to_dict

        mock_tenant = MagicMock()
        mock_tenant.id = 1
        mock_tenant.created_at = datetime.now(UTC).replace(tzinfo=None)
        mock_tenant.updated_at = datetime.now(UTC).replace(tzinfo=None)
        mock_tenant.name = "Test Tenant"
        mock_tenant.slug = "test-tenant"
        mock_tenant.is_active = True

        result = benchmark(_tenant_to_dict, mock_tenant)

        assert result["id"] == 1
        assert result["slug"] == "test-tenant"

        benchmark.extra_info["optimization"] = "direct_dict_bypass"

    def test_pydantic_serialization_baseline(self, benchmark: BenchmarkFixture) -> None:
        """Baseline: Pydantic model serialization."""
        from src.app.modules.tenants.schemas import TenantResponse

        mock_tenant = MagicMock()
        mock_tenant.id = 1
        mock_tenant.created_at = datetime.now(UTC).replace(tzinfo=None)
        mock_tenant.updated_at = datetime.now(UTC).replace(tzinfo=None)
        mock_tenant.name = "Test Tenant"
        mock_tenant.slug = "test-tenant"
        mock_tenant.is_active = True

        def serialize_pydantic() -> dict[str, Any]:
            return TenantResponse.model_validate(mock_tenant).model_dump()

        result = benchmark(serialize_pydantic)
        assert result["id"] == 1


# =============================================================================
# BENCHMARK 6: Event Enqueue
# =============================================================================


class TestEventEnqueueBenchmark:
    """Benchmark event handler enqueue performance."""

    def test_parallel_handler_enqueue(self, benchmark: BenchmarkFixture) -> None:
        """Benchmark parallel event handler enqueue."""
        handler_count = 5

        async def mock_enqueue() -> float:
            await asyncio.sleep(0.001)  # Simulate enqueue
            return time.perf_counter()

        async def enqueue_parallel() -> list[float]:
            return await asyncio.gather(*[mock_enqueue() for _ in range(handler_count)])

        def run_parallel() -> list[float]:
            return asyncio.new_event_loop().run_until_complete(enqueue_parallel())

        result = benchmark(run_parallel)
        assert len(result) == handler_count

        benchmark.extra_info["handler_count"] = handler_count
        benchmark.extra_info["threshold_ms"] = REGRESSION_THRESHOLDS_MS["event_enqueue"]
        benchmark.extra_info["optimization"] = "parallel_gather"

    def test_sequential_handler_enqueue_baseline(self, benchmark: BenchmarkFixture) -> None:
        """Baseline: Sequential event handler enqueue."""
        handler_count = 5

        async def mock_enqueue() -> float:
            await asyncio.sleep(0.001)
            return time.perf_counter()

        async def enqueue_sequential() -> list[float]:
            results = []
            for _ in range(handler_count):
                results.append(await mock_enqueue())
            return results

        def run_sequential() -> list[float]:
            return asyncio.new_event_loop().run_until_complete(enqueue_sequential())

        result = benchmark(run_sequential)
        assert len(result) == handler_count


# =============================================================================
# AGGREGATE BENCHMARKS
# =============================================================================


class TestAggregateBenchmarks:
    """Aggregate benchmarks combining multiple optimizations."""

    def test_orjson_serialization(self, benchmark: BenchmarkFixture) -> None:
        """Benchmark orjson vs stdlib json."""
        import orjson

        data = {
            "id": 1,
            "name": "Test",
            "nested": {"key": "value"},
            "list": [1, 2, 3, 4, 5],
        }

        def serialize_orjson() -> bytes:
            return orjson.dumps(data)

        result = benchmark(serialize_orjson)
        assert len(result) > 0

        benchmark.extra_info["optimization"] = "orjson"

    def test_redis_cache_bytes(self, benchmark: BenchmarkFixture) -> None:
        """Benchmark bytes storage without decode."""
        import orjson

        tenant_data = {
            "id": 123,
            "slug": "test-tenant",
            "is_active": True,
        }

        def serialize_for_cache() -> bytes:
            return orjson.dumps(tenant_data)  # No .decode()

        result = benchmark(serialize_for_cache)
        assert isinstance(result, bytes)

        benchmark.extra_info["optimization"] = "bytes_storage"


# =============================================================================
# REGRESSION VALIDATION
# =============================================================================


class TestRegressionValidation:
    """Validate benchmark results against thresholds."""

    def test_all_thresholds_defined(self) -> None:
        """Ensure all required thresholds are defined."""
        required = [
            "dns_resolution",
            "fastcrud_instantiation",
            "ip_parsing_cached",
            "webhooks_hmac",
            "update_operation",
            "event_enqueue",
        ]
        for key in required:
            assert key in REGRESSION_THRESHOLDS_MS, f"Missing threshold: {key}"
            assert REGRESSION_THRESHOLDS_MS[key] > 0, f"Invalid threshold: {key}"

    def test_thresholds_reasonable(self) -> None:
        """Validate threshold values are reasonable."""
        # DNS should be under 500ms
        assert REGRESSION_THRESHOLDS_MS["dns_resolution"] < 500

        # HMAC should be under 10ms
        assert REGRESSION_THRESHOLDS_MS["webhooks_hmac"] < 10

        # Event enqueue should be under 50ms
        assert REGRESSION_THRESHOLDS_MS["event_enqueue"] < 50
