"""Benchmark test configuration and fixtures.

Provides shared fixtures for performance testing with pytest-benchmark.
Thresholds are defined here for CI/CD regression gates.
"""

import pytest

# Performance thresholds (P95 in milliseconds)
PERF_THRESHOLDS = {
    "auth_token_verify": 50,  # AUTH_P95: 50ms
    "ai_rate_limit_check": 10,  # Rate limit should be very fast
    "user_fetch_with_tier": 30,  # USER_FETCH_P95: 30ms
    "token_blacklist_check": 5,  # Redis lookup should be <5ms
}


@pytest.fixture
def perf_thresholds():
    """Provide performance thresholds to tests."""
    return PERF_THRESHOLDS


@pytest.fixture
def mock_redis():
    """Create mock Redis client for benchmark tests."""
    from unittest.mock import AsyncMock

    redis = AsyncMock()
    redis.zscore = AsyncMock(return_value=None)  # Not blacklisted
    redis.incr = AsyncMock(return_value=1)
    redis.ttl = AsyncMock(return_value=60)
    redis.evalsha = AsyncMock(return_value=[1, 60])
    redis.script_load = AsyncMock(return_value="test_sha")
    return redis


@pytest.fixture
def mock_db_session():
    """Create mock async database session."""
    from unittest.mock import AsyncMock

    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    return session
