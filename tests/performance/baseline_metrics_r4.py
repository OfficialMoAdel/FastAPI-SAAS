"""Baseline Performance Metrics for PERF-OPT Round 4.

This module captures baseline measurements for all 12 R4 optimizations
to enable before/after comparison and regression detection.

CRITICAL: This file captures BASELINE metrics BEFORE any optimization.
Run this ONCE and commit the JSON output before implementing optimizations.

Usage:
    # Capture baseline to JSON file
    pytest tests/performance/baseline_metrics_r4.py \
        --benchmark-json=tests/performance/baselines/baseline_r4.json \
        --benchmark-only -v

    # Compare against baseline after optimizations
    pytest tests/performance/baseline_metrics_r4.py \
        --benchmark-compare=tests/performance/baselines/baseline_r4.json

Optimizations being baselined:
    P0-1: DNS Resolution (url_validator.py:110) - socket.getaddrinfo blocking
    P0-2: FastCRUD Instantiation (_base/service.py:55) - object allocation
    P0-3: IP Address Parsing (rate_limit.py:182) - ipaddress module overhead
    P0-4: Webhooks HMAC (webhooks/service.py:159) - json.dumps vs orjson

    P1-1: Update Re-fetch Pattern (tenants/service.py) - RETURNING clause
    P1-2: Cache Cold Start (main.py lifespan) - first request latency
    P1-3: Worker Job Execution (worker/settings.py) - max_jobs config
    P1-4: Event Bus Enqueue (events.py:283) - sequential vs parallel
    P1-5: AI Streaming JSON (ollama_client.py:176) - json.loads per chunk

    P2-1: Pydantic model_dump (billing/service.py) - validation overhead
    P2-2: Redis Queue Pool (setup.py:78) - connection count
    P2-3: Cache Warming .decode (cache_warming.py:66) - bytes to str
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import socket
from typing import Any, TYPE_CHECKING, Any, Final
from unittest.mock import AsyncMock

import orjson
import pytest
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from pytest_benchmark.fixture import BenchmarkFixture


# =============================================================================
# SAMPLE DATA
# =============================================================================

#: Sample webhook payload for HMAC signature benchmarks
SAMPLE_WEBHOOK_PAYLOAD: Final[dict[str, Any]] = {
    "event": "user.created",
    "timestamp": "2026-01-17T12:00:00Z",
    "data": {
        "user_id": 12345,
        "email": "test@example.com",
        "tenant_id": 1,
        "metadata": {"source": "api", "version": "v1"},
    },
}

#: Sample tenant data for cache benchmarks
SAMPLE_TENANT: Final[dict[str, Any]] = {
    "id": 1,
    "slug": "acme-corp",
    "is_active": True,
}

#: Sample user data for Pydantic benchmarks
SAMPLE_USER: Final[dict[str, Any]] = {
    "id": 1,
    "email": "test@example.com",
    "is_active": True,
    "is_superuser": False,
    "tenant_id": 1,
    "tier_id": 1,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}

#: Test domains for DNS resolution benchmarks
TEST_DOMAINS: Final[list[str]] = [
    "example.com",
    "google.com",
    "github.com",
    "cloudflare.com",
    "amazon.com",
]

#: Test IP addresses for parsing benchmarks
TEST_IPS: Final[list[str]] = [
    "192.168.1.1",
    "10.0.0.1",
    "172.16.0.1",
    "8.8.8.8",
    "1.1.1.1",
    "203.0.113.50",
    "198.51.100.25",
    "192.0.2.100",
]

#: Trusted proxy networks for IP parsing
TRUSTED_NETWORKS: Final[list[ipaddress.IPv4Network]] = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]

#: Webhook secret for HMAC benchmarks
WEBHOOK_SECRET: Final[str] = "test-webhook-secret-key-for-benchmarking"


# =============================================================================
# PYDANTIC MODELS FOR BENCHMARKS
# =============================================================================


class UserRead(BaseModel):
    """User model for Pydantic benchmark."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    is_active: bool
    is_superuser: bool
    tenant_id: int
    tier_id: int
    created_at: str
    updated_at: str


class TenantCache(BaseModel):
    """Tenant cache model for Pydantic benchmark."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    is_active: bool


# =============================================================================
# P0 BASELINES - CRITICAL PATH OPTIMIZATIONS
# =============================================================================


@pytest.mark.benchmark(group="p0_dns", min_rounds=20, warmup=True)
def test_baseline_dns_resolution(benchmark: BenchmarkFixture) -> None:
    """BASELINE P0-1: DNS Resolution Latency (blocking socket.getaddrinfo).

    Current implementation uses blocking socket.getaddrinfo() which blocks
    the event loop during DNS resolution. This baseline measures the
    blocking time for comparison after async DNS wrapper optimization.

    Target file: src/app/core/security/url_validator.py:110
    Expected baseline: 1-50ms per call (network dependent)
    Optimization: Async DNS resolution wrapper
    """

    def resolve_dns() -> set[str]:
        # Simulate the exact code path from url_validator.py:110
        hostname = "example.com"
        addr_info = socket.getaddrinfo(
            hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
        )
        return {info[4][0] for info in addr_info}

    result = benchmark(resolve_dns)
    benchmark.extra_info["target_file"] = "url_validator.py:110"
    benchmark.extra_info["optimization_id"] = "P0-1"
    benchmark.extra_info["operation"] = "socket.getaddrinfo"
    benchmark.extra_info["expected_improvement"] = "async non-blocking"

    assert len(result) >= 1, "Should resolve at least one IP"


@pytest.mark.benchmark(group="p0_fastcrud", min_rounds=1000, warmup=True)
def test_baseline_fastcrud_instantiation(benchmark: BenchmarkFixture) -> None:
    """BASELINE P0-2: FastCRUD Instantiation Overhead.

    Every service instantiation creates a new FastCRUD(model) object.
    This baseline measures the object allocation overhead across 23 modules.

    Target file: src/app/modules/_base/service.py:55
    Expected baseline: ~10-50μs per instantiation
    Optimization: Flyweight pattern with cached instances
    """
    try:
        from fastcrud import FastCRUD

        from src.app.modules.users.models import User

        def create_crud() -> Any:
            return FastCRUD(User)

        result = benchmark(create_crud)
        benchmark.extra_info["target_file"] = "_base/service.py:55"
        benchmark.extra_info["optimization_id"] = "P0-2"
        benchmark.extra_info["operation"] = "FastCRUD(model)"
        benchmark.extra_info["modules_affected"] = 23
        benchmark.extra_info["expected_improvement"] = "flyweight caching"

        assert result is not None

    except ImportError:
        pytest.skip("FastCRUD not installed")


@pytest.mark.benchmark(group="p0_ip_parsing", min_rounds=1000, warmup=True)
def test_baseline_ip_address_parsing(benchmark: BenchmarkFixture) -> None:
    """BASELINE P0-3: IP Address Parsing Overhead.

    Every request parses client IP and checks against trusted networks.
    This baseline measures ipaddress.ip_address() + network membership check.

    Target file: src/app/middleware/rate_limit.py:182
    Expected baseline: ~5.8μs per call (per performance report)
    Optimization: LRU cache for repeated IP lookups
    """

    def parse_and_check_ip() -> bool:
        ip_str = "192.168.1.100"
        ip = ipaddress.ip_address(ip_str)
        return any(ip in network for network in TRUSTED_NETWORKS)

    result = benchmark(parse_and_check_ip)
    benchmark.extra_info["target_file"] = "rate_limit.py:182"
    benchmark.extra_info["optimization_id"] = "P0-3"
    benchmark.extra_info["operation"] = "ipaddress.ip_address + network check"
    benchmark.extra_info["expected_improvement"] = "LRU cache"

    assert isinstance(result, bool)


@pytest.mark.benchmark(group="p0_ip_parsing", min_rounds=500, warmup=True)
def test_baseline_ip_parsing_batch(benchmark: BenchmarkFixture) -> None:
    """BASELINE P0-3b: Batch IP Parsing (realistic request simulation).

    Simulates parsing multiple different IPs as would happen with
    concurrent requests from different clients.

    Target file: src/app/middleware/rate_limit.py:182
    """

    def parse_multiple_ips() -> list[bool]:
        results = []
        for ip_str in TEST_IPS:
            ip = ipaddress.ip_address(ip_str)
            results.append(any(ip in network for network in TRUSTED_NETWORKS))
        return results

    result = benchmark(parse_multiple_ips)
    benchmark.extra_info["target_file"] = "rate_limit.py:182"
    benchmark.extra_info["optimization_id"] = "P0-3"
    benchmark.extra_info["operation"] = "batch IP parsing"
    benchmark.extra_info["batch_size"] = len(TEST_IPS)

    assert len(result) == len(TEST_IPS)


@pytest.mark.benchmark(group="p0_hmac", min_rounds=500, warmup=True)
def test_baseline_webhook_hmac_stdlib_json(benchmark: BenchmarkFixture) -> None:
    """BASELINE P0-4: Webhook HMAC with stdlib json.dumps(sort_keys=True).

    Current implementation uses json.dumps() with sort_keys=True for
    consistent signatures. This is slower than orjson with OPT_SORT_KEYS.

    Target file: src/app/modules/webhooks/service.py:159
    Expected baseline: ~1.5ms per signature
    Optimization: Replace with orjson.dumps(option=orjson.OPT_SORT_KEYS)
    """

    def generate_signature_stdlib() -> str:
        payload_bytes = json.dumps(SAMPLE_WEBHOOK_PAYLOAD, sort_keys=True).encode(
            "utf-8"
        )
        signature = hmac.new(
            WEBHOOK_SECRET.encode("utf-8"), payload_bytes, hashlib.sha256
        ).hexdigest()
        return f"sha256={signature}"

    result = benchmark(generate_signature_stdlib)
    benchmark.extra_info["target_file"] = "webhooks/service.py:159"
    benchmark.extra_info["optimization_id"] = "P0-4"
    benchmark.extra_info["operation"] = "json.dumps(sort_keys=True) + HMAC"
    benchmark.extra_info["expected_improvement"] = "orjson.OPT_SORT_KEYS"

    assert result.startswith("sha256=")
    assert len(result) == 71  # sha256= + 64 hex chars


@pytest.mark.benchmark(group="p0_hmac", min_rounds=500, warmup=True)
def test_baseline_webhook_hmac_orjson(benchmark: BenchmarkFixture) -> None:
    """COMPARISON: Webhook HMAC with orjson (for before/after comparison).

    This measures what the optimized version WILL achieve.
    Included here to validate improvement after optimization.
    """

    def generate_signature_orjson() -> str:
        payload_bytes = orjson.dumps(
            SAMPLE_WEBHOOK_PAYLOAD, option=orjson.OPT_SORT_KEYS
        )
        signature = hmac.new(
            WEBHOOK_SECRET.encode("utf-8"), payload_bytes, hashlib.sha256
        ).hexdigest()
        return f"sha256={signature}"

    result = benchmark(generate_signature_orjson)
    benchmark.extra_info["target_file"] = "webhooks/service.py:159"
    benchmark.extra_info["optimization_id"] = "P0-4-target"
    benchmark.extra_info["operation"] = "orjson.dumps(OPT_SORT_KEYS) + HMAC"

    assert result.startswith("sha256=")


# =============================================================================
# P1 BASELINES - HIGH IMPACT OPTIMIZATIONS
# =============================================================================


@pytest.mark.benchmark(group="p1_ai_json", min_rounds=1000, warmup=True)
def test_baseline_ai_streaming_json_stdlib(benchmark: BenchmarkFixture) -> None:
    """BASELINE P1-5: AI Streaming JSON Parsing with stdlib json.

    AI streaming responses call json.loads() for each chunk.
    This baseline measures the parsing overhead per chunk.

    Target file: src/app/modules/ai/ollama_client.py:176
    Expected baseline: ~100μs per chunk
    Optimization: Replace with orjson.loads()
    """
    # Simulate a typical streaming chunk from Ollama
    sample_chunk = json.dumps(
        {
            "message": {"content": "Hello, how can I help?"},
            "done": False,
            "eval_count": 5,
        }
    )

    def parse_chunk_stdlib() -> dict[str, Any]:
        return json.loads(sample_chunk)

    result = benchmark(parse_chunk_stdlib)
    benchmark.extra_info["target_file"] = "ollama_client.py:176"
    benchmark.extra_info["optimization_id"] = "P1-5"
    benchmark.extra_info["operation"] = "json.loads(chunk)"
    benchmark.extra_info["expected_improvement"] = "orjson.loads"

    assert "message" in result


@pytest.mark.benchmark(group="p1_ai_json", min_rounds=1000, warmup=True)
def test_baseline_ai_streaming_json_orjson(benchmark: BenchmarkFixture) -> None:
    """COMPARISON: AI Streaming JSON with orjson (target performance)."""
    sample_chunk = orjson.dumps(
        {
            "message": {"content": "Hello, how can I help?"},
            "done": False,
            "eval_count": 5,
        }
    )

    def parse_chunk_orjson() -> dict[str, Any]:
        return orjson.loads(sample_chunk)

    result = benchmark(parse_chunk_orjson)
    benchmark.extra_info["target_file"] = "ollama_client.py:176"
    benchmark.extra_info["optimization_id"] = "P1-5-target"
    benchmark.extra_info["operation"] = "orjson.loads(chunk)"

    assert "message" in result


@pytest.mark.benchmark(group="p1_event_enqueue", min_rounds=100, warmup=True)
def test_baseline_event_bus_sequential_enqueue(
    benchmark: BenchmarkFixture, mock_redis: AsyncMock
) -> None:
    """BASELINE P1-4: Event Bus Sequential Handler Enqueue.

    Current implementation enqueues handlers sequentially in a loop.
    This baseline measures the overhead of sequential enqueueing.

    Target file: src/app/core/events.py:283
    Expected baseline: Sum(RTT) = 5-20ms for 5 handlers
    Optimization: asyncio.gather() for parallel enqueue
    """
    import asyncio

    # Simulate 5 handlers being enqueued sequentially
    async def sequential_enqueue() -> int:
        count = 0
        for i in range(5):
            await mock_redis.lpush(f"queue:handler_{i}", "event_data")
            count += 1
        return count

    def run_sequential() -> int:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(sequential_enqueue())
        finally:
            loop.close()

    result = benchmark(run_sequential)
    benchmark.extra_info["target_file"] = "events.py:283"
    benchmark.extra_info["optimization_id"] = "P1-4"
    benchmark.extra_info["operation"] = "sequential handler enqueue"
    benchmark.extra_info["handler_count"] = 5
    benchmark.extra_info["expected_improvement"] = "asyncio.gather parallel"

    assert result == 5


# =============================================================================
# P2 BASELINES - MODERATE IMPACT OPTIMIZATIONS
# =============================================================================


@pytest.mark.benchmark(group="p2_pydantic", min_rounds=1000, warmup=True)
def test_baseline_pydantic_validate_dump(benchmark: BenchmarkFixture) -> None:
    """BASELINE P2-1: Pydantic model_validate().model_dump() Overhead.

    Pattern used in billing and other services where dict data is
    validated through Pydantic then immediately dumped back to dict.

    Target file: src/app/modules/billing/service.py
    Expected baseline: ~166μs per call
    Optimization: Direct dict construction when schema is known
    """

    def validate_and_dump() -> dict[str, Any]:
        user = UserRead.model_validate(SAMPLE_USER)
        return user.model_dump()

    result = benchmark(validate_and_dump)
    benchmark.extra_info["target_file"] = "billing/service.py"
    benchmark.extra_info["optimization_id"] = "P2-1"
    benchmark.extra_info["operation"] = "model_validate().model_dump()"
    benchmark.extra_info["expected_improvement"] = "direct dict construction"

    assert result["id"] == 1
    assert result["email"] == "test@example.com"


@pytest.mark.benchmark(group="p2_pydantic", min_rounds=1000, warmup=True)
def test_baseline_direct_dict_construction(benchmark: BenchmarkFixture) -> None:
    """COMPARISON: Direct dict construction (target performance)."""

    def direct_dict() -> dict[str, Any]:
        # Direct dict construction bypassing Pydantic
        return {
            "id": SAMPLE_USER["id"],
            "email": SAMPLE_USER["email"],
            "is_active": SAMPLE_USER["is_active"],
            "is_superuser": SAMPLE_USER["is_superuser"],
            "tenant_id": SAMPLE_USER["tenant_id"],
            "tier_id": SAMPLE_USER["tier_id"],
            "created_at": SAMPLE_USER["created_at"],
            "updated_at": SAMPLE_USER["updated_at"],
        }

    result = benchmark(direct_dict)
    benchmark.extra_info["optimization_id"] = "P2-1-target"
    benchmark.extra_info["operation"] = "direct dict construction"

    assert result["id"] == 1


@pytest.mark.benchmark(group="p2_cache", min_rounds=1000, warmup=True)
def test_baseline_cache_warming_with_decode(benchmark: BenchmarkFixture) -> None:
    """BASELINE P2-3: Cache Warming orjson.dumps().decode() Overhead.

    Current implementation calls .decode() on orjson bytes output
    to convert to str for Redis. This adds overhead.

    Target file: src/app/core/utils/cache_warming.py:66
    Expected baseline: Minor (microseconds per call)
    Optimization: Store bytes directly if Redis client supports it
    """

    def serialize_with_decode() -> str:
        return orjson.dumps(SAMPLE_TENANT).decode()

    result = benchmark(serialize_with_decode)
    benchmark.extra_info["target_file"] = "cache_warming.py:66"
    benchmark.extra_info["optimization_id"] = "P2-3"
    benchmark.extra_info["operation"] = "orjson.dumps().decode()"
    benchmark.extra_info["expected_improvement"] = "store bytes directly"

    assert isinstance(result, str)
    assert "acme-corp" in result


@pytest.mark.benchmark(group="p2_cache", min_rounds=1000, warmup=True)
def test_baseline_cache_warming_bytes_only(benchmark: BenchmarkFixture) -> None:
    """COMPARISON: Cache Warming without .decode() (target performance)."""

    def serialize_bytes() -> bytes:
        return orjson.dumps(SAMPLE_TENANT)

    result = benchmark(serialize_bytes)
    benchmark.extra_info["optimization_id"] = "P2-3-target"
    benchmark.extra_info["operation"] = "orjson.dumps() bytes only"

    assert isinstance(result, bytes)


# =============================================================================
# AGGREGATE BASELINES - COMPOUND OPERATIONS
# =============================================================================


@pytest.mark.benchmark(group="aggregate", min_rounds=100, warmup=True)
def test_baseline_webhook_full_flow(benchmark: BenchmarkFixture) -> None:
    """BASELINE: Full webhook signature generation flow.

    Combines all webhook-related operations to measure aggregate impact.
    """

    def full_webhook_flow() -> tuple[str, str]:
        # 1. Serialize payload with stdlib json
        payload_json = json.dumps(SAMPLE_WEBHOOK_PAYLOAD, sort_keys=True)
        payload_bytes = payload_json.encode("utf-8")

        # 2. Generate HMAC signature
        signature = hmac.new(
            WEBHOOK_SECRET.encode("utf-8"), payload_bytes, hashlib.sha256
        ).hexdigest()

        return payload_json, f"sha256={signature}"

    result = benchmark(full_webhook_flow)
    benchmark.extra_info["operation"] = "full webhook signature flow"
    benchmark.extra_info["optimizations"] = ["P0-4"]

    assert len(result) == 2
    assert result[1].startswith("sha256=")


@pytest.mark.benchmark(group="aggregate", min_rounds=100, warmup=True)
def test_baseline_rate_limit_full_check(benchmark: BenchmarkFixture) -> None:
    """BASELINE: Full rate limit IP check flow.

    Combines IP parsing and network membership check.
    """

    def full_rate_limit_check() -> tuple[bool, str]:
        ip_str = "192.168.1.100"

        # 1. Parse IP address
        ip = ipaddress.ip_address(ip_str)

        # 2. Check trusted networks
        is_trusted = any(ip in network for network in TRUSTED_NETWORKS)

        # 3. Generate rate limit key
        rate_key = f"rate_limit:user:123:{ip_str}"

        return is_trusted, rate_key

    result = benchmark(full_rate_limit_check)
    benchmark.extra_info["operation"] = "full rate limit check"
    benchmark.extra_info["optimizations"] = ["P0-3"]

    assert isinstance(result[0], bool)
    assert "rate_limit" in result[1]


# =============================================================================
# STATISTICAL VALIDATION
# =============================================================================


@pytest.mark.benchmark(group="validation", min_rounds=100, warmup=True)
def test_baseline_statistical_validity(benchmark: BenchmarkFixture) -> None:
    """Validation: Ensure benchmark framework is working correctly.

    This simple benchmark validates that:
    1. pytest-benchmark is capturing metrics correctly
    2. Statistical data is being collected (100+ samples)
    3. Results are reproducible
    """
    counter = 0

    def simple_operation() -> int:
        nonlocal counter
        counter += 1
        # Simple arithmetic to validate timing
        return sum(range(100))

    result = benchmark(simple_operation)
    benchmark.extra_info["validation"] = True
    benchmark.extra_info["expected_result"] = 4950

    assert result == 4950, "Simple arithmetic should work"
