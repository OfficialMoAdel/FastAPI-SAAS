"""Performance benchmarks for CI pipeline and optimization tracking.

This module provides comprehensive performance benchmarks for critical code paths
in the FastAPI SaaS application. Results are used to:
1. Establish baselines before optimization work (PERF-ADV rounds)
2. Validate performance regressions in CI/CD
3. Compare before/after metrics for PRs

Targets (p95 latency from conftest.PERF_THRESHOLDS_MS):
    - /users/me: < 15ms (auth + cache + serialization)
    - /auth/refresh: < 10ms (JWT decode + blacklist + encode)
    - AI chat DB operations: < 100ms (complex queries)
    - Cache operations: < 5ms (Redis round-trip)
    - Serialization: 10k/sec (< 0.1ms each Pydantic op)

Usage:
    # Run all benchmarks with verbose output
    pytest tests/performance/test_benchmarks.py --benchmark-only -v

    # Generate baseline JSON for comparison
    pytest tests/performance/test_benchmarks.py --benchmark-json=baselines/baseline_r1.json

    # Compare against baseline
    pytest tests/performance/test_benchmarks.py --benchmark-compare=baselines/baseline_r1.json

    # Run specific benchmark group
    pytest tests/performance/test_benchmarks.py -k "auth" --benchmark-only

Benchmark Groups:
    - auth: JWT creation/verification, password hashing
    - cache: Key generation, serialization/deserialization
    - serialization: Pydantic validation and model_dump operations
    - rate_limit: Key extraction and sliding window calculations
    - async_ops: Async patterns with mocked I/O
    - aggregate: Full endpoint flow simulations

See Also:
    - tests/performance/conftest.py: Threshold definitions and fixtures
    - tests/performance/baselines/: Stored baseline metrics
    - scripts/capture_baseline.py: Standalone baseline capture tool
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Final
from unittest.mock import AsyncMock

import pytest

if TYPE_CHECKING:
    from pytest_benchmark.fixture import BenchmarkFixture


# =============================================================================
# CONSTANTS
# =============================================================================

#: Sample user data matching UserRead schema structure
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


def _get_token_payload() -> dict[str, Any]:
    """Generate token payload with valid future expiration."""
    now = int(time.time())
    return {
        "sub": "1",
        "exp": now + 3600,  # 1 hour from now
        "iat": now,
        "type": "access",
        "tenant_id": 1,
    }


#: Test secret key (NOT for production use)
_BENCHMARK_SECRET: Final[str] = "test-secret-key-for-benchmarking-purposes-only"


# =============================================================================
# AUTH BENCHMARKS
# =============================================================================


@pytest.mark.benchmark(group="auth", min_rounds=100, warmup=True)
def test_bench_jwt_token_creation(benchmark: BenchmarkFixture) -> None:
    """Benchmark JWT token creation.

    This measures the raw jwt.encode() performance, which is a component of
    both access token creation and refresh token creation flows.

    Target: < 1ms (component of 10ms total auth flow)
    Critical path: /auth/login, /auth/refresh, /auth/token
    """
    import jwt

    payload = _get_token_payload()

    def create_token() -> str:
        return jwt.encode(payload, _BENCHMARK_SECRET, algorithm="HS256")

    result = benchmark(create_token)
    benchmark.extra_info["target_ms"] = 1.0
    benchmark.extra_info["operation"] = "jwt_encode"
    benchmark.extra_info["algorithm"] = "HS256"

    assert isinstance(result, str)
    assert len(result) > 50  # JWT tokens are fairly long


@pytest.mark.benchmark(group="auth", min_rounds=100, warmup=True)
def test_bench_jwt_token_verification(benchmark: BenchmarkFixture) -> None:
    """Benchmark JWT token verification (decode + signature validation).

    This is called on EVERY authenticated request via the auth dependency.
    Must be extremely fast as it's in the critical path.

    Target: < 1ms
    Critical path: ALL authenticated endpoints
    """
    import jwt

    payload = _get_token_payload()
    token = jwt.encode(payload, _BENCHMARK_SECRET, algorithm="HS256")

    def verify_token() -> dict[str, Any]:
        return jwt.decode(token, _BENCHMARK_SECRET, algorithms=["HS256"])

    result = benchmark(verify_token)
    benchmark.extra_info["target_ms"] = 1.0
    benchmark.extra_info["operation"] = "jwt_decode"
    benchmark.extra_info["algorithm"] = "HS256"

    assert result["sub"] == "1"


@pytest.mark.benchmark(group="auth", min_rounds=50, warmup=True)
def test_bench_password_hashing(benchmark: BenchmarkFixture) -> None:
    """Benchmark bcrypt password hashing.

    bcrypt is intentionally slow to resist brute-force attacks.
    Using rounds=4 for benchmarks (production uses 12).

    Target: < 500ms (intentionally slow, not optimizable)
    Critical path: /auth/register, /users/password-reset
    Note: This is only called on registration/password change, not on every request.
    """
    import bcrypt

    password = b"SecurePassword123!"

    def hash_password() -> bytes:
        # Use lower rounds for benchmark (default 12 is very slow)
        return bcrypt.hashpw(password, bcrypt.gensalt(rounds=4))

    result = benchmark(hash_password)
    benchmark.extra_info["target_ms"] = 500.0  # bcrypt is intentionally slow
    benchmark.extra_info["operation"] = "bcrypt_hash"
    benchmark.extra_info["rounds"] = 4

    assert result.startswith(b"$2b$")


@pytest.mark.benchmark(group="auth", min_rounds=100, warmup=True)
def test_bench_password_verification(benchmark: BenchmarkFixture) -> None:
    """Benchmark bcrypt password verification.

    Called on every login attempt. Using rounds=4 for benchmarks.

    Target: < 100ms
    Critical path: /auth/login
    """
    import bcrypt

    password = b"SecurePassword123!"
    hashed = bcrypt.hashpw(password, bcrypt.gensalt(rounds=4))

    def verify_password() -> bool:
        return bcrypt.checkpw(password, hashed)

    result = benchmark(verify_password)
    benchmark.extra_info["target_ms"] = 100.0
    benchmark.extra_info["operation"] = "bcrypt_verify"

    assert result is True


# =============================================================================
# CACHE BENCHMARKS
# =============================================================================


@pytest.mark.benchmark(group="cache", min_rounds=1000, warmup=True)
def test_bench_cache_key_generation(benchmark: BenchmarkFixture) -> None:
    """Benchmark cache key generation with SHA256 hashing.

    Cache keys must be unique and deterministic. We use SHA256 prefix
    for consistent key lengths in Redis.

    Target: < 0.1ms
    Critical path: ALL cached operations
    """
    import hashlib

    def generate_cache_key() -> str:
        parts = ["user", "1", "tenant", "1", "profile"]
        key = ":".join(parts)
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    result = benchmark(generate_cache_key)
    benchmark.extra_info["target_ms"] = 0.1
    benchmark.extra_info["operation"] = "cache_key_gen"

    assert len(result) == 16


@pytest.mark.benchmark(group="cache", min_rounds=500, warmup=True)
def test_bench_cache_serialization(benchmark: BenchmarkFixture) -> None:
    """Benchmark JSON serialization for cache storage using orjson.

    orjson is significantly faster than stdlib json for serialization.

    Target: < 5ms
    Critical path: Cache SET operations
    """
    import orjson

    data = {
        "user": SAMPLE_USER,
        "permissions": ["read", "write", "delete"],
        "metadata": {"last_login": "2026-01-01T00:00:00Z"},
    }

    def serialize() -> bytes:
        return orjson.dumps(data)

    result = benchmark(serialize)
    benchmark.extra_info["target_ms"] = 5.0
    benchmark.extra_info["operation"] = "orjson_dumps"

    assert len(result) > 50


@pytest.mark.benchmark(group="cache", min_rounds=500, warmup=True)
def test_bench_cache_deserialization(benchmark: BenchmarkFixture) -> None:
    """Benchmark JSON deserialization from cache using orjson.

    Target: < 5ms
    Critical path: Cache GET operations (cache hits)
    """
    import orjson

    data = orjson.dumps(
        {
            "user": SAMPLE_USER,
            "permissions": ["read", "write", "delete"],
            "metadata": {"last_login": "2026-01-01T00:00:00Z"},
        }
    )

    def deserialize() -> dict[str, Any]:
        return orjson.loads(data)

    result = benchmark(deserialize)
    benchmark.extra_info["target_ms"] = 5.0
    benchmark.extra_info["operation"] = "orjson_loads"

    assert "user" in result


# =============================================================================
# SERIALIZATION BENCHMARKS (Pydantic)
# =============================================================================


@pytest.mark.benchmark(group="serialization", min_rounds=1000, warmup=True)
def test_bench_pydantic_validation(benchmark: BenchmarkFixture) -> None:
    """Benchmark Pydantic model validation (dict -> model).

    Pydantic v2 uses Rust-based validation which is very fast.
    This is called on every API request to validate input.

    Target: < 0.1ms (10k/sec throughput)
    Critical path: ALL API endpoints with request bodies
    """
    from pydantic import BaseModel, ConfigDict

    class UserRead(BaseModel):
        model_config = ConfigDict(from_attributes=True)

        id: int
        email: str
        is_active: bool
        tenant_id: int

    def validate_user() -> UserRead:
        return UserRead.model_validate(SAMPLE_USER)

    result = benchmark(validate_user)
    benchmark.extra_info["target_ms"] = 0.1  # 10k/sec = 0.1ms each
    benchmark.extra_info["operation"] = "pydantic_validate"

    assert result.id == 1
    assert result.email == "test@example.com"


@pytest.mark.benchmark(group="serialization", min_rounds=1000, warmup=True)
def test_bench_pydantic_serialization(benchmark: BenchmarkFixture) -> None:
    """Benchmark Pydantic model serialization (model -> dict).

    Target: < 0.1ms (10k/sec throughput)
    Critical path: ALL API responses
    """
    from pydantic import BaseModel, ConfigDict

    class UserRead(BaseModel):
        model_config = ConfigDict(from_attributes=True)

        id: int
        email: str
        is_active: bool
        tenant_id: int

    user = UserRead.model_validate(SAMPLE_USER)

    def serialize_user() -> dict[str, Any]:
        return user.model_dump()

    result = benchmark(serialize_user)
    benchmark.extra_info["target_ms"] = 0.1
    benchmark.extra_info["operation"] = "pydantic_dump"

    assert result["id"] == 1


@pytest.mark.benchmark(group="serialization", min_rounds=1000, warmup=True)
def test_bench_pydantic_json_serialization(benchmark: BenchmarkFixture) -> None:
    """Benchmark Pydantic JSON serialization (model -> JSON string).

    Uses orjson internally for speed. Faster than model_dump() + json.dumps().

    Target: < 0.1ms (10k/sec throughput)
    Critical path: ALL API JSON responses
    """
    from pydantic import BaseModel, ConfigDict

    class UserRead(BaseModel):
        model_config = ConfigDict(from_attributes=True)

        id: int
        email: str
        is_active: bool
        tenant_id: int

    user = UserRead.model_validate(SAMPLE_USER)

    def serialize_json() -> str:
        return user.model_dump_json()

    result = benchmark(serialize_json)
    benchmark.extra_info["target_ms"] = 0.1
    benchmark.extra_info["operation"] = "pydantic_dump_json"

    assert "test@example.com" in result


# =============================================================================
# RATE LIMITING BENCHMARKS
# =============================================================================


@pytest.mark.benchmark(group="rate_limit", min_rounds=500, warmup=True)
def test_bench_rate_limit_key_extraction(benchmark: BenchmarkFixture) -> None:
    """Benchmark rate limit key extraction from request context.

    Composite key includes user ID, endpoint, and client IP.
    IP validation adds minimal overhead.

    Target: < 1ms
    Critical path: RateLimitMiddleware (every request)
    """
    import ipaddress

    def extract_rate_limit_key() -> str:
        client_ip = "192.168.1.100"
        user_id = 123
        endpoint = "/api/v1/users/me"

        # Validate IP
        ipaddress.ip_address(client_ip)

        # Generate composite key
        return f"rate_limit:{user_id}:{endpoint}:{client_ip}"

    result = benchmark(extract_rate_limit_key)
    benchmark.extra_info["target_ms"] = 1.0
    benchmark.extra_info["operation"] = "rate_limit_key"

    assert "rate_limit:123" in result


@pytest.mark.benchmark(group="rate_limit", min_rounds=500, warmup=True)
def test_bench_sliding_window_calculation(benchmark: BenchmarkFixture) -> None:
    """Benchmark sliding window rate limit calculation.

    Uses weighted average of current and previous window counts
    for smoother rate limiting without Redis Lua scripts.

    Target: < 5ms
    Critical path: RateLimitMiddleware (every request)
    """

    def calculate_sliding_window() -> tuple[int, int]:
        current_time = time.time()
        window_size = 60  # 1 minute
        max_requests = 100

        _ = int(current_time) - window_size  # window_start (used in real impl)
        current_window = int(current_time / window_size)
        _ = current_window - 1  # previous_window (used in real impl)

        # Simulate weighted calculation
        current_count = 45
        previous_count = 30
        elapsed = current_time % window_size
        weight = (window_size - elapsed) / window_size

        weighted_count = int(previous_count * weight) + current_count
        remaining = max(0, max_requests - weighted_count)

        return weighted_count, remaining

    result = benchmark(calculate_sliding_window)
    benchmark.extra_info["target_ms"] = 5.0
    benchmark.extra_info["operation"] = "sliding_window_calc"

    assert isinstance(result[0], int)
    assert isinstance(result[1], int)


# =============================================================================
# ASYNC OPERATION BENCHMARKS (using sync wrapper)
# =============================================================================


def _run_async(coro: Any) -> Any:
    """Run async coroutine in sync context for pytest-benchmark compatibility.

    Note: Creates a new event loop per call. This adds ~0.5ms overhead.
    Real async performance will be better in production.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.mark.benchmark(group="async_ops", min_rounds=100, warmup=True)
def test_bench_async_db_query_mock(benchmark: BenchmarkFixture, mock_redis: AsyncMock) -> None:
    """Benchmark mocked async DB query pattern.

    Simulates SQLAlchemy async query overhead without actual DB connection.
    Real DB queries will have network latency added.

    Target: < 100ms (includes simulated 1ms latency)
    Critical path: ALL database operations
    """

    async def mock_db_query() -> dict[str, Any]:
        # Simulate DB query overhead
        await asyncio.sleep(0.001)  # 1ms simulated latency
        return SAMPLE_USER.copy()

    def run_query() -> dict[str, Any]:
        return _run_async(mock_db_query())

    result = benchmark(run_query)
    benchmark.extra_info["target_ms"] = 100.0
    benchmark.extra_info["operation"] = "async_db_query"

    assert result["id"] == 1


@pytest.mark.benchmark(group="async_ops", min_rounds=100, warmup=True)
def test_bench_async_cache_get_mock(benchmark: BenchmarkFixture, mock_redis: AsyncMock) -> None:
    """Benchmark mocked async cache GET operation.

    Uses mock_redis fixture to simulate Redis response without network.

    Target: < 5ms
    Critical path: ALL cached lookups
    """

    async def mock_cache_get() -> bytes | None:
        return await mock_redis.get("user:1:profile")

    def run_cache_get() -> bytes | None:
        return _run_async(mock_cache_get())

    result = benchmark(run_cache_get)
    benchmark.extra_info["target_ms"] = 5.0
    benchmark.extra_info["operation"] = "async_cache_get"

    assert result is not None


# =============================================================================
# AGGREGATE PERFORMANCE TESTS
# =============================================================================


@pytest.mark.benchmark(group="aggregate", min_rounds=50, warmup=True)
def test_bench_users_me_simulation(benchmark: BenchmarkFixture) -> None:
    """Simulate full /users/me endpoint flow.

    This is the most critical benchmark as it represents the complete
    authenticated request path that every logged-in user hits frequently.

    Flow: JWT decode -> cache check -> user fetch -> Pydantic serialize
    Target: < 15ms p95
    Critical path: /users/me (highest traffic authenticated endpoint)

    Optimization opportunities:
    - JWT caching in request state (avoid double-decode)
    - RBAC permission caching with Redis Sets
    - Response caching for stable user data
    """
    import jwt
    import orjson
    from pydantic import BaseModel, ConfigDict

    class UserRead(BaseModel):
        model_config = ConfigDict(from_attributes=True)
        id: int
        email: str
        is_active: bool
        tenant_id: int

    secret = "test-secret"
    payload = _get_token_payload()
    token = jwt.encode(payload, secret, algorithm="HS256")
    cached_user = orjson.dumps(SAMPLE_USER)

    def users_me_flow() -> str:
        # 1. Decode JWT (typically done in auth dependency)
        _ = jwt.decode(token, secret, algorithms=["HS256"])

        # 2. Check cache (simulate hit - best case)
        user_data = orjson.loads(cached_user)

        # 3. Validate with Pydantic (response model)
        user = UserRead.model_validate(user_data)

        # 4. Serialize response to JSON
        return user.model_dump_json()

    result = benchmark(users_me_flow)
    benchmark.extra_info["target_ms"] = 15.0
    benchmark.extra_info["operation"] = "users_me_full"
    benchmark.extra_info["endpoint"] = "/users/me"

    assert "test@example.com" in result


@pytest.mark.benchmark(group="aggregate", min_rounds=50, warmup=True)
def test_bench_auth_refresh_simulation(benchmark: BenchmarkFixture) -> None:
    """Simulate /auth/refresh endpoint flow.

    Called periodically by frontend to refresh access tokens before expiry.
    Must be fast to avoid user-perceived latency during token refresh.

    Flow: JWT decode -> blacklist check (O(1) set lookup) -> new token creation
    Target: < 10ms p95
    Critical path: /auth/refresh

    Optimization opportunities:
    - Token blacklist in Redis Sets for O(1) lookup
    - Cached JWT payload extraction
    """
    import jwt

    secret = "test-secret"
    base_payload = _get_token_payload()
    refresh_payload = {**base_payload, "type": "refresh"}
    refresh_token = jwt.encode(refresh_payload, secret, algorithm="HS256")
    blacklist: set[str] = set()  # Simulate empty blacklist

    def auth_refresh_flow() -> str:
        # 1. Decode refresh token
        payload = jwt.decode(refresh_token, secret, algorithms=["HS256"])

        # 2. Check blacklist (O(1) set lookup)
        jti = payload.get("jti", "default")
        if jti in blacklist:
            raise ValueError("Token blacklisted")

        # 3. Create new access token
        new_payload = {
            "sub": payload["sub"],
            "exp": payload["exp"] + 3600,
            "iat": payload["iat"],
            "type": "access",
            "tenant_id": payload["tenant_id"],
        }
        return jwt.encode(new_payload, secret, algorithm="HS256")

    result = benchmark(auth_refresh_flow)
    benchmark.extra_info["target_ms"] = 10.0
    benchmark.extra_info["operation"] = "auth_refresh_full"
    benchmark.extra_info["endpoint"] = "/auth/refresh"

    assert isinstance(result, str)
    assert len(result) > 50
