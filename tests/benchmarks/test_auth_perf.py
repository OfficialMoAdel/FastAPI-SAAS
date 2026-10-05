"""Performance benchmarks for authentication operations.

Timing goes through ``src/benchmarks.protocol``, the single H-11 estimator.
Each ceiling here is an ABSOLUTE FIXED CEILING chosen in advance, never a
comparison against a previous run: ``new < old`` passes on noise, and the
measured noise floor on this project is already 7.1x run-to-run on identical
code (perf C11). The protocol refuses a relative criterion outright, so it
cannot be reintroduced here by accident.

The values are ``Ceiling`` objects rather than bare numbers, and they are
expressed in microseconds because that is the protocol's unit. They are not
derived from any earlier measurement of these operations; see the module-level
note in ``src/benchmarks/protocol.py`` on why a ceiling computed from a
previous run is the same mistake wearing a different hat.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from src.benchmarks import protocol

# Absolute fixed ceilings, in microseconds. Not measured, not tuned, not
# relative to anything.
THRESHOLDS = {
    "token_blacklist_check": protocol.Ceiling(5_000.0, "is_token_blacklisted"),
    "token_blacklist_add": protocol.Ceiling(5_000.0, "add_token_to_blacklist"),
    "jwt_creation": protocol.Ceiling(10_000.0, "create_access_token"),
    # bcrypt dominates this one, so the ceiling is deliberately loose: it is a
    # guard against a pathological regression (a re-hash per call, an
    # accidental round bump), not a performance claim.
    "password_verify": protocol.Ceiling(3_000_000.0, "verify_password"),
}


class TestTokenBlacklistPerformance:
    """Benchmark token blacklist Redis cache operations."""

    @pytest.mark.asyncio
    async def test_token_blacklist_check_performance(self, mock_redis) -> None:
        """Token blacklist check should complete under threshold."""
        from src.app.core.security.token_blacklist_cache import is_token_blacklisted

        result: bool | None = None

        async def probe() -> None:
            nonlocal result
            result = await is_token_blacklisted("test-token-12345")

        with patch("src.app.core.utils.cache.client", mock_redis):
            measured = await protocol.measure_async(probe, name="token_blacklist_check")

        assert result is False
        protocol.verdict(measured, THRESHOLDS["token_blacklist_check"]).assert_passed()

    @pytest.mark.asyncio
    async def test_token_blacklist_add_performance(self, mock_redis) -> None:
        """Adding token to blacklist should be fast."""
        from src.app.core.security.token_blacklist_cache import add_token_to_blacklist

        mock_redis.zadd = AsyncMock(return_value=1)
        expires = datetime.now(UTC) + timedelta(hours=1)
        result: bool | None = None

        async def probe() -> None:
            nonlocal result
            result = await add_token_to_blacklist("test-token", expires)

        with patch("src.app.core.utils.cache.client", mock_redis):
            measured = await protocol.measure_async(probe, name="token_blacklist_add")

        assert result is True
        protocol.verdict(measured, THRESHOLDS["token_blacklist_add"]).assert_passed()


class TestJWTPerformance:
    """Benchmark JWT token operations."""

    @pytest.mark.asyncio
    async def test_jwt_creation_performance(self) -> None:
        """JWT token creation should be fast."""
        from src.app.core.security import create_access_token

        token = ""

        async def probe() -> None:
            nonlocal token
            token = await create_access_token(
                data={"sub": "testuser@example.com"},
                expires_delta=timedelta(minutes=30),
            )

        measured = await protocol.measure_async(probe, name="jwt_creation")

        assert token is not None
        assert len(token) > 50
        protocol.verdict(measured, THRESHOLDS["jwt_creation"]).assert_passed()


class TestPasswordHashingPerformance:
    """Benchmark password hashing operations."""

    @pytest.mark.asyncio
    async def test_password_verify_performance(self) -> None:
        """Password verification should complete in reasonable time."""
        from src.app.core.security import get_password_hash, verify_password

        hashed = await get_password_hash("TestPassword123!")
        result: bool | None = None

        async def probe() -> None:
            nonlocal result
            result = await verify_password("TestPassword123!", hashed)

        # iterations=1 because a single bcrypt verification IS the unit under
        # test; there is no inner loop to amortise over. The protocol still
        # takes the best of five repetitions, which is the part that matters
        # here - the previous version read ONE arbitrary sample, so a single
        # scheduler preemption on a shared runner could read as 30x.
        measured = await protocol.measure_async(probe, name="password_verify", iterations=1, warmup=1)

        assert result is True
        protocol.verdict(measured, THRESHOLDS["password_verify"]).assert_passed()
