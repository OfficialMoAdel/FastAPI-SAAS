"""
Performance benchmarks for CI pipeline.

Targets (p95 latency):
- /users/me: < 15ms
- /auth/refresh: < 10ms
- AI chat DB operations: < 100ms
- Cache operations: < 5ms
- Serialization: 10k/sec (< 0.1ms each)

Run locally:
    pytest tests/performance/ --benchmark-only -v

Run with comparison:
    pytest tests/performance/ --benchmark-compare=baselines/main.json

Generate baseline:
    pytest tests/performance/ --benchmark-json=baselines/main.json
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

if TYPE_CHECKING:
    from pytest_benchmark.fixture import BenchmarkFixture

# Sample data for benchmarks
SAMPLE_USER = {
    "id": 1,
    "email": "test@example.com",
    "is_active": True,
    "is_superuser": False,
    "tenant_id": 1,
    "tier_id": 1,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}

SAMPLE_TOKEN_PAYLOAD = {
    "sub": "1",
    "exp": 1736000000,
    "iat": 1735900000,
    "type": "access",
    "tenant_id": 1,
}


# =============================================================================
# AUTH BENCHMARKS
# =============================================================================


@pytest.mark.benchmark(group="auth", min_rounds=100, warmup=True)
def test_bench_jwt_token_creation(benchmark: BenchmarkFixture) -> None:
    """Benchmark JWT token creation - target: component of < 10ms total."""
    import jwt

    secret = "test-secret-key-for-benchmarking-purposes-only"

    def create_token() -> str:
        return jwt.encode(SAMPLE_TOKEN_PAYLOAD, secret, algorithm="HS256")

    result = benchmark(create_token)
    benchmark.extra_info["target_ms"] = 10.0
    benchmark.extra_info["operation"] = "jwt_encode"

    assert isinstance(result, str)
    assert len(result) > 50  # JWT tokens are fairly long


@pytest.mark.benchmark(group="auth", min_rounds=100, warmup=True)
def test_bench_jwt_token_verification(benchmark: BenchmarkFixture) -> None:
    """Benchmark JWT token verification - target: < 10ms."""
    import jwt

    secret = "test-secret-key-for-benchmarking-purposes-only"
    token = jwt.encode(SAMPLE_TOKEN_PAYLOAD, secret, algorithm="HS256")

    def verify_token() -> dict[str, Any]:
        return jwt.decode(token, secret, algorithms=["HS256"])

    result = benchmark(verify_token)
    benchmark.extra_info["target_ms"] = 10.0
    benchmark.extra_info["operation"] = "jwt_decode"

    assert result["sub"] == "1"


@pytest.mark.benchmark(group="auth", min_rounds=50, warmup=True)
def test_bench_password_hashing(benchmark: BenchmarkFixture) -> None:
    """Benchmark bcrypt password hashing - expected to be slow (~100-500ms)."""
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
    """Benchmark bcrypt password verification."""
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
    """Benchmark cache key generation - target: < 0.1ms."""
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
    """Benchmark JSON serialization for cache - target: < 5ms."""
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
    """Benchmark JSON deserialization from cache - target: < 5ms."""
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
    """Benchmark Pydantic model validation - target: 10k/sec."""
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
    """Benchmark Pydantic model serialization - target: 10k/sec."""
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
    """Benchmark Pydantic JSON serialization - target: 10k/sec."""
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
    """Benchmark rate limit key extraction from request - target: < 1ms."""
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
    """Benchmark sliding window rate limit calculation - target: < 5ms."""

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
    """Helper to run async code in sync benchmark."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.mark.benchmark(group="async_ops", min_rounds=100, warmup=True)
def test_bench_async_db_query_mock(benchmark: BenchmarkFixture, mock_redis: AsyncMock) -> None:
    """Benchmark mocked async DB query pattern - target: < 100ms."""

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
    """Benchmark mocked async cache get - target: < 5ms."""

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
    """
    Simulate full /users/me endpoint flow - target: < 15ms p95.

    Flow: JWT decode -> cache check -> user fetch -> serialize
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
    token = jwt.encode(SAMPLE_TOKEN_PAYLOAD, secret, algorithm="HS256")
    cached_user = orjson.dumps(SAMPLE_USER)

    def users_me_flow() -> str:
        # 1. Decode JWT
        _ = jwt.decode(token, secret, algorithms=["HS256"])  # payload used in real impl

        # 2. Check cache (simulate hit)
        user_data = orjson.loads(cached_user)

        # 3. Validate with Pydantic
        user = UserRead.model_validate(user_data)

        # 4. Serialize response
        return user.model_dump_json()

    result = benchmark(users_me_flow)
    benchmark.extra_info["target_ms"] = 15.0
    benchmark.extra_info["operation"] = "users_me_full"
    benchmark.extra_info["endpoint"] = "/users/me"

    assert "test@example.com" in result


@pytest.mark.benchmark(group="aggregate", min_rounds=50, warmup=True)
def test_bench_auth_refresh_simulation(benchmark: BenchmarkFixture) -> None:
    """
    Simulate /auth/refresh endpoint flow - target: < 10ms p95.

    Flow: JWT decode -> blacklist check -> new token creation
    """
    import jwt

    secret = "test-secret"
    refresh_payload = {**SAMPLE_TOKEN_PAYLOAD, "type": "refresh"}
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
