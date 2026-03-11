"""Basic test to verify circuit breaker double-check pattern works."""

import asyncio

import pytest

from src.app.core.utils.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpen,
    CircuitState,
    circuit_breaker,
)


@pytest.mark.asyncio
async def test_circuit_breaker_basic_functionality():
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
async def test_circuit_breaker_with_feature_flag_disabled():
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
