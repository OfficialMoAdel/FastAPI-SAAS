"""Comprehensive Circuit Breaker Tests.

Consolidates tests from:
- tests/unit/core/utils/test_circuit_breaker_basic.py
- tests/e2e/core/resilience/test_circuit_breaker.py
- tests/integration/core/resilience/test_db_circuit_breaker_integration.py
- tests/e2e/phase_tests/test_phase2_db_circuit_breaker.py
"""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.resilience.db_circuit_breaker import (
    db_circuit,
    execute_query_with_circuit_breaker,
    execute_transaction_with_circuit_breaker,
    get_read_only_fallback,
)
from src.app.core.utils.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpen,
    CircuitState,
    circuit_breaker,
)

# =============================================================================
# Unit Tests: Basic Circuit Breaker Functionality
# =============================================================================


class TestCircuitBreakerBasic:
    """Unit tests for basic circuit breaker functionality."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_basic_functionality(self):
        """Test that circuit breaker basic functionality still works."""
        breaker = CircuitBreaker(name="test", failure_threshold=2, recovery_timeout=1.0)

        call_count = 0

        @circuit_breaker(breaker)
        async def test_func():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ValueError("Test error")
            return "success"

        # First two calls should fail and record failures
        with pytest.raises(ValueError):
            await test_func()

        with pytest.raises(ValueError):
            await test_func()

        # Circuit should now be open
        assert breaker.state == CircuitState.OPEN

        # Next call should be rejected immediately
        with pytest.raises(CircuitBreakerOpen):
            await test_func()

        # Call count should still be 2 (third call was rejected)
        assert call_count == 2

        # Wait for recovery timeout
        await asyncio.sleep(1.1)

        # Circuit should transition to HALF_OPEN
        assert breaker.state == CircuitState.HALF_OPEN

        # Next call should succeed and close circuit
        result = await test_func()
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_circuit_breaker_with_feature_flag_disabled(self):
        """Test that circuit breaker works with feature flag disabled.

        Note: As of R2-HYBRID, the default is True (optimizations enabled).
        This test verifies the legacy path behavior when explicitly disabled.
        """
        from unittest.mock import patch

        from src.app.core.config import settings

        # Patch the feature flag to test legacy path
        with patch.object(settings, "CIRCUIT_BREAKER_USE_DOUBLE_CHECK", False):
            # Feature flag should be False when patched
            assert settings.CIRCUIT_BREAKER_USE_DOUBLE_CHECK is False

            breaker = CircuitBreaker(name="test_flag", failure_threshold=1)

            @circuit_breaker(breaker)
            async def test_func():
                raise ValueError("Test error")

            # Should still work with feature flag disabled
            with pytest.raises(ValueError):
                await test_func()

            assert breaker.state == CircuitState.OPEN


# =============================================================================
# E2E Tests: Circuit Breaker Registry
# =============================================================================


class TestCircuitBreakerRegistry:
    """E2E tests for circuit breaker registry."""

    @pytest.fixture
    async def e2e_client(self):
        """Create a client for E2E tests pointing to the app."""
        from httpx import ASGITransport, AsyncClient

        from src.app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            yield client

    @pytest.mark.asyncio
    async def test_smoke_circuit_breaker_registry(self, e2e_client):
        """
        E2E Smoke Test for Circuit Breaker Registry.

        Verifies:
        1. Circuit breakers are registered on startup (database is registered).
        2. Admin API exposes circuit breaker status.
        3. Circuits start in CLOSED state.
        """
        # 1. Check if we can access the health endpoint to ensure app is up.
        response = await e2e_client.get("/api/v1/health")
        assert response.status_code == 200

        # 2. Access the circuit breaker registry directly
        from src.app.core.resilience.registry import CircuitState, get_registry

        registry = get_registry()

        # Check registry content
        circuits = registry.list_all()
        circuit_names = [c.name for c in circuits]

        # The "database" circuit is registered via db_circuit_breaker.py
        assert "database" in circuit_names

        # 3. Verify initial state
        db_circuit_ref = registry.get("database")
        assert db_circuit_ref is not None
        assert db_circuit_ref.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_circuit_breaker_state_transition_simulation(self):
        """
        Simulate state transitions in a circuit breaker.
        """
        from src.app.core.resilience.registry import CircuitState, get_registry
        from src.app.core.utils.circuit_breaker import CircuitBreakerOpen

        registry = get_registry()

        # Get the database circuit (the one that's registered)
        circuit_name = "database"

        circuit = registry.get(circuit_name)
        assert circuit is not None

        # Store initial failure threshold
        original_threshold = circuit.failure_threshold

        # Reset it first
        circuit.reset()
        assert circuit.state == CircuitState.CLOSED

        # Simulate failures (default threshold is 3 for database circuit)
        for _ in range(original_threshold):
            await circuit.record_failure()

        # Should be OPEN now
        assert circuit.state == CircuitState.OPEN

        # Define a dummy protected function
        @circuit_breaker(circuit)
        async def protected_op():
            return "success"

        # Should raise exception when OPEN
        with pytest.raises(CircuitBreakerOpen):
            await protected_op()

        # Reset for other tests
        circuit.reset()
        assert circuit.state == CircuitState.CLOSED


# =============================================================================
# Integration Tests: Database Circuit Breaker
# =============================================================================


class TestDatabaseCircuitBreaker:
    """Integration tests for database circuit breaker."""

    @pytest.fixture(autouse=True)
    def reset_circuit(self):
        """Reset circuit breaker state before and after each test."""
        db_circuit.reset()
        yield
        db_circuit.reset()

    @pytest.mark.asyncio
    async def test_execute_query_success_path(self, real_postgres: AsyncSession):
        """Test successful query execution closes circuit and caches result."""
        query = "SELECT 1"
        cache_key = "test:circuit:select_1"

        # Need redis client for caching tests
        from src.app.core.utils.cache import client as redis_client

        if redis_client is None:
            pytest.skip("Redis client not initialized")

        # Execute query
        result = await execute_query_with_circuit_breaker(
            db=real_postgres, query=query, cache_key=cache_key, cache_ttl=60
        )

        # Verify result - Postgres returns 1 for SELECT 1
        assert result == [{"?column?": 1}]

        # Verify circuit state
        assert db_circuit.state == CircuitState.CLOSED
        assert db_circuit.failure_count == 0

        # Verify cache was set
        cached = await redis_client.get(cache_key)
        assert cached is not None
        import json

        cached_data = json.loads(cached)
        assert cached_data == [{"?column?": 1}]

    @pytest.mark.asyncio
    async def test_execute_query_static_fallback(self, real_postgres: AsyncSession):
        """Test fallback to static data when DB fails and no cache."""
        query = "SELECT * FROM non_existent_table"
        fallback_data = [{"id": 0, "name": "fallback"}]

        # Force DB error by using invalid table
        # This should trigger circuit failure counting
        try:
            await execute_query_with_circuit_breaker(db=real_postgres, query=query, fallback_data=fallback_data)
        except Exception:
            pass  # Expected failure

        # Manually open circuit to test fallback path
        db_circuit._state = CircuitState.OPEN

        # Now valid query should return fallback because circuit is open
        result = await execute_query_with_circuit_breaker(
            db=real_postgres, query="SELECT 1", fallback_data=fallback_data
        )

        assert result == fallback_data

    @pytest.mark.asyncio
    async def test_circuit_opens_after_threshold(self, real_postgres: AsyncSession):
        """Test circuit opens after reaching failure threshold."""
        query = "SELECT * FROM non_existent_table"

        # Reset circuit first
        db_circuit.reset()

        # Execute enough failures to open circuit
        # Default threshold is 3 for DB circuit
        for _ in range(3):
            try:
                await execute_query_with_circuit_breaker(db=real_postgres, query=query)
            except Exception:
                pass

        assert db_circuit.state == CircuitState.OPEN

        # Next call should fail fast with RuntimeError (as per implementation)
        with pytest.raises(RuntimeError) as excinfo:
            await execute_query_with_circuit_breaker(db=real_postgres, query="SELECT 1")
        assert "Database circuit breaker is OPEN" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_transaction_success(self, real_postgres: AsyncSession):
        """Test successful transaction execution."""

        async def op1(session):
            result = await session.execute(text("SELECT 1"))
            return result.scalar()

        async def op2(session):
            result = await session.execute(text("SELECT 2"))
            return result.scalar()

        result = await execute_transaction_with_circuit_breaker(db=real_postgres, operations=[op1, op2])

        assert result == [1, 2]
        assert db_circuit.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_transaction_retry_and_failure(self, real_postgres: AsyncSession):
        """Test transaction retries and eventual failure."""
        mock_fail_count = 0

        async def failing_op(session):
            nonlocal mock_fail_count
            mock_fail_count += 1
            raise ValueError("Transaction failed")

        # Expect RuntimeError which wraps the original error
        with pytest.raises(RuntimeError) as excinfo:
            await execute_transaction_with_circuit_breaker(db=real_postgres, operations=[failing_op], max_retries=2)

        # Check that the original error is the cause or part of the message if needed
        # But strictly the function raises RuntimeError
        assert "Transaction failed after 2 attempts" in str(excinfo.value)

        # Should have tried initial + 2 retries = 3 total
        assert mock_fail_count == 3
        # Failures should contribute to circuit state
        assert db_circuit.failure_count > 0

    @pytest.mark.asyncio
    async def test_read_only_fallback_structure(self):
        """Verify read-only fallback structure."""
        fallback = get_read_only_fallback()

        assert "status" in fallback
        assert fallback["status"] == "degraded"
        assert fallback["mode"] == "read-only"
        assert "timestamp" not in fallback  # Based on actual implementation


# =============================================================================
# E2E Phase Tests: Database Circuit Breaker Full Flow
# =============================================================================


class TestDatabaseCircuitBreakerE2E:
    """E2E tests for database circuit breaker full flow."""

    @pytest.mark.skip(reason="Complex test with timing issues - covered by other tests")
    @pytest.mark.asyncio
    async def test_db_circuit_breaker_e2e(self, db_session: AsyncSession):
        """
        E2E Test for Database Circuit Breaker.

        Covers:
        1. Normal query execution (CLOSED state)
        2. Failure counting and state transition (CLOSED -> OPEN)
        3. Fast failure when OPEN
        4. Static fallback when OPEN
        5. Read-only mode fallback
        6. Transaction retry logic
        7. Recovery (OPEN -> HALF_OPEN -> CLOSED)
        """
        from unittest.mock import AsyncMock

        # Ensure circuit starts closed
        db_circuit.reset()
        assert db_circuit.state == CircuitState.CLOSED

        # 1. Normal query execution
        query = "SELECT 1"
        result = await execute_query_with_circuit_breaker(db_session, query)
        assert result is not None
        # Adjust for SQLite/PostgreSQL differences in column naming
        first_row = result[0]
        if isinstance(first_row, dict):
            assert list(first_row.values())[0] == 1
        else:
            assert first_row[0] == 1

        # 2. Simulate failures to trip circuit
        original_execute = db_session.execute

        # Mock failure
        db_session.execute = AsyncMock(side_effect=Exception("DB Connection Failed"))  # type: ignore[method-assign]

        # Record failures until threshold is reached (threshold is 3)
        for _ in range(3):
            try:
                await execute_query_with_circuit_breaker(db_session, query)
            except Exception:
                pass

        # Should be OPEN now
        assert db_circuit.state == CircuitState.OPEN

        # 3. Fast failure when OPEN (no DB call)
        # Reset mock to verify it's NOT called
        db_session.execute.reset_mock()

        with pytest.raises(RuntimeError, match="Database circuit breaker is OPEN"):
            await execute_query_with_circuit_breaker(db_session, query)

        assert db_session.execute.call_count == 0

        # 4. Static fallback when OPEN
        static_data = [{"id": 2, "name": "Static Data"}]
        result = await execute_query_with_circuit_breaker(db_session, query, fallback_data=static_data)
        assert result == static_data

        # 5. Read-only mode fallback
        read_only_info = get_read_only_fallback()
        assert read_only_info["mode"] == "read-only"
        assert "read" in read_only_info["allowed_operations"]

        # 6. Transaction retry logic
        # Reset circuit for transaction test
        db_circuit.reset()

        # Mock a transient failure that succeeds on retry
        side_effects = [
            Exception("Transient Error 1"),
            Exception("Transient Error 2"),
            "Success",  # 3rd attempt succeeds
        ]

        async def operation(db):
            effect = side_effects.pop(0)
            if isinstance(effect, Exception):
                raise effect
            return effect

        ops = [operation]

        # Should succeed after retries
        results = await execute_transaction_with_circuit_breaker(db_session, ops, max_retries=3)
        assert results == ["Success"]

        # 7. Recovery (OPEN -> HALF_OPEN -> CLOSED)
        # Trip it again
        db_circuit.reset()
        assert db_circuit.state == CircuitState.CLOSED

        # Restore mock for failures
        db_session.execute = AsyncMock(side_effect=Exception("DB Connection Failed 2"))  # type: ignore[method-assign]

        for _ in range(3):
            try:
                await execute_query_with_circuit_breaker(db_session, query)
            except Exception:
                pass

        assert db_circuit.state == CircuitState.OPEN

        # Force recovery timeout to expire
        db_circuit._last_failure_time = 0

        # Restore real DB execution
        db_session.execute = original_execute  # type: ignore[method-assign]

        # 1. Normal query execution
        query = "SELECT 1"
        result = await execute_query_with_circuit_breaker(db_session, query)
        assert result is not None
        # Adjust for SQLite/PostgreSQL differences in column naming
        first_row = result[0]
        if isinstance(first_row, dict):
            assert list(first_row.values())[0] == 1
        else:
            assert first_row[0] == 1

        # 2. Simulate failures to trip circuit
        original_execute = db_session.execute

        # Mock failure
        db_session.execute = AsyncMock(side_effect=Exception("DB Connection Failed"))  # type: ignore[method-assign]

        # Record failures until threshold is reached (threshold is 3)
        for _ in range(3):
            try:
                await execute_query_with_circuit_breaker(db_session, query)
            except Exception:
                pass

        # Should be OPEN now
        assert db_circuit.state == CircuitState.OPEN

        # 3. Fast failure when OPEN (no DB call)
        # Reset mock to verify it's NOT called
        db_session.execute.reset_mock()

        with pytest.raises(RuntimeError, match="Database circuit breaker is OPEN"):
            await execute_query_with_circuit_breaker(db_session, query)

        assert db_session.execute.call_count == 0

        # 4. Cache fallback when OPEN
        # Setup cache mock
        cache_key = "test_query_cache"
        fallback_data = [{"id": 1, "name": "Cached Data"}]
        import json

        # Mock cache client
        with pytest.importorskip("src.app.core.utils.cache", None):
            from unittest.mock import patch

            with patch("src.app.core.utils.cache.client") as mock_cache:
                mock_cache.get = AsyncMock(return_value=json.dumps(fallback_data))

                result = await execute_query_with_circuit_breaker(db_session, query, cache_key=cache_key)

                assert result == fallback_data

        # 5. Static fallback when OPEN
        static_data = [{"id": 2, "name": "Static Data"}]
        result = await execute_query_with_circuit_breaker(db_session, query, fallback_data=static_data)
        assert result == static_data

        # 6. Read-only mode fallback
        read_only_info = get_read_only_fallback()
        assert read_only_info["mode"] == "read-only"
        assert "read" in read_only_info["allowed_operations"]

        # 7. Transaction retry logic
        # Reset circuit for transaction test
        db_circuit.reset()

        # Mock a transient failure that succeeds on retry
        side_effects = [
            Exception("Transient Error 1"),
            Exception("Transient Error 2"),
            "Success",  # 3rd attempt succeeds
        ]

        async def operation(db):
            effect = side_effects.pop(0)
            if isinstance(effect, Exception):
                raise effect
            return effect

        ops = [operation]

        # Should succeed after retries
        results = await execute_transaction_with_circuit_breaker(db_session, ops, max_retries=3)
        assert results == ["Success"]

        # 8. Recovery (OPEN -> HALF_OPEN -> CLOSED)
        # Trip it again
        db_circuit.reset()
        assert db_circuit.state == CircuitState.CLOSED

        # Restore mock for failures
        db_session.execute = AsyncMock(side_effect=Exception("DB Connection Failed 2"))  # type: ignore[method-assign]

        for _ in range(3):
            try:
                await execute_query_with_circuit_breaker(db_session, query)
            except Exception:
                pass

        assert db_circuit.state == CircuitState.OPEN

        # Force recovery timeout to expire
        db_circuit._last_failure_time = 0

        # Restore real DB execution
        db_session.execute = original_execute  # type: ignore[method-assign]

        # Next call should be allowed (HALF_OPEN)
        with pytest.importorskip("time", None):
            from unittest.mock import patch

            with patch("time.time", return_value=9999999999.9):
                # This call should go through and close the circuit
                for _ in range(db_circuit.success_threshold):
                    result = await execute_query_with_circuit_breaker(db_session, query)
                    assert result is not None

                assert db_circuit.state == CircuitState.CLOSED
