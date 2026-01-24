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
    from unittest.mock import AsyncMock, MagicMock

    # Create pipeline mock (sync call, async execute)
    mock_pipe = MagicMock()
    mock_pipe.zadd = MagicMock()
    mock_pipe.delete = MagicMock()
    mock_pipe.setex = MagicMock()
    mock_pipe.execute = AsyncMock(return_value=[1, 1])

    redis = AsyncMock()
    redis.zscore = AsyncMock(return_value=None)  # Not blacklisted
    redis.incr = AsyncMock(return_value=1)
    redis.ttl = AsyncMock(return_value=60)
    redis.evalsha = AsyncMock(return_value=0)  # 0 = not blacklisted (single int)
    redis.script_load = AsyncMock(return_value="test_sha")
    redis.pipeline = MagicMock(return_value=mock_pipe)  # Pipeline support for OPT-5
    return redis


@pytest.fixture
def mock_db_session():
    """Create mock async database session."""
    from unittest.mock import AsyncMock

    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    return session
