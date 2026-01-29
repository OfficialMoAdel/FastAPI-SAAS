"""Performance benchmarks for authentication operations.

Tests verify auth operations meet P95 latency requirements.
Uses simple timing assertions (no pytest-benchmark dependency).
"""

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

# Performance thresholds in seconds
THRESHOLDS = {
    "token_blacklist_check": 0.005,  # 5ms
    "token_blacklist_add": 0.005,  # 5ms
    "jwt_creation": 0.010,  # 10ms
    "password_verify": 0.700,  # 700ms (bcrypt is intentionally slow)
}


class TestTokenBlacklistPerformance:
    """Benchmark token blacklist Redis cache operations."""

    @pytest.mark.asyncio
    async def test_token_blacklist_check_performance(self, mock_redis) -> None:
        """Token blacklist check should complete under threshold."""
        from src.app.core.security.token_blacklist_cache import is_token_blacklisted

        with patch("src.app.core.security.token_blacklist_cache.redis_client", mock_redis):
            start = time.perf_counter()
            for _ in range(100):
                result = await is_token_blacklisted("test-token-12345")
            elapsed = (time.perf_counter() - start) / 100

        assert result is False
        assert elapsed < THRESHOLDS["token_blacklist_check"], f"Too slow: {elapsed * 1000:.2f}ms"

    @pytest.mark.asyncio
    async def test_token_blacklist_add_performance(self, mock_redis) -> None:
        """Adding token to blacklist should be fast."""
        from src.app.core.security.token_blacklist_cache import add_token_to_blacklist

        mock_redis.zadd = AsyncMock(return_value=1)
        expires = datetime.now(UTC) + timedelta(hours=1)

        with patch("src.app.core.security.token_blacklist_cache.redis_client", mock_redis):
            start = time.perf_counter()
            for _ in range(100):
                result = await add_token_to_blacklist("test-token", expires)
            elapsed = (time.perf_counter() - start) / 100

        assert result is True
        assert elapsed < THRESHOLDS["token_blacklist_add"], f"Too slow: {elapsed * 1000:.2f}ms"


class TestJWTPerformance:
    """Benchmark JWT token operations."""

    @pytest.mark.asyncio
    async def test_jwt_creation_performance(self) -> None:
        """JWT token creation should be fast."""
        from src.app.core.security import create_access_token

        start = time.perf_counter()
        for _ in range(100):
            token = await create_access_token(
                data={"sub": "testuser@example.com"},
                expires_delta=timedelta(minutes=30),
            )
        elapsed = (time.perf_counter() - start) / 100

        assert token is not None
        assert len(token) > 50
        assert elapsed < THRESHOLDS["jwt_creation"], f"Too slow: {elapsed * 1000:.2f}ms"


class TestPasswordHashingPerformance:
    """Benchmark password hashing operations."""

    @pytest.mark.asyncio
    async def test_password_verify_performance(self) -> None:
        """Password verification should complete in reasonable time."""
        from src.app.core.security import get_password_hash, verify_password

        hashed = await get_password_hash("TestPassword123!")

        start = time.perf_counter()
        result = await verify_password("TestPassword123!", hashed)
        elapsed = time.perf_counter() - start

        assert result is True
        assert elapsed < THRESHOLDS["password_verify"], f"Too slow: {elapsed * 1000:.2f}ms"
