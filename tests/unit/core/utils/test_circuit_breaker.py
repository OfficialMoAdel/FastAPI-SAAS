"""Comprehensive Circuit Breaker Tests.

Consolidates tests from:
- tests/unit/core/utils/test_circuit_breaker_basic.py
- tests/e2e/core/resilience/test_circuit_breaker.py
- tests/integration/core/resilience/test_db_circuit_breaker_integration.py
- tests/e2e/phase_tests/test_phase2_db_circuit_breaker.py
"""

import asyncio

import pytest

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
        with patch.object(settings.r1_unified, "CIRCUIT_BREAKER_USE_DOUBLE_CHECK", False):
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
# E2E Phase Tests: Database Circuit Breaker Full Flow
# =============================================================================
