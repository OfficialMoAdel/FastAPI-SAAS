"""Unit tests for P4-1: Circuit Breaker TOCTOU Double-Check Pattern."""
import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.app.core.utils.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpen,
    CircuitState,
    circuit_breaker,
)


@pytest.mark.asyncio
async def test_open_state_checked_under_lock():
    """Test that OPEN state is double-checked under lock when optimization enabled."""
    breaker = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=60.0)

    # Force circuit to OPEN state
    await breaker.record_failure(ValueError("Test error"))
    assert breaker.state == CircuitState.OPEN

    call_count = 0

    @circuit_breaker(breaker)
    async def test_func():
        nonlocal call_count
        call_count += 1
        return "success"

    # Mock settings with feature flag enabled
    mock_settings = MagicMock()
    mock_settings.CIRCUIT_BREAKER_USE_DOUBLE_CHECK = True

    with patch("src.app.core.config.settings", mock_settings):
        # Should raise CircuitBreakerOpen
        with pytest.raises(CircuitBreakerOpen):
            await test_func()

    # Function should not have been called
    assert call_count == 0


@pytest.mark.asyncio
async def test_state_transition_during_check_handled():
    """Test that state transitions during check are handled correctly."""
    breaker = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.1)

    # Force circuit to OPEN state
    await breaker.record_failure(ValueError("Test error"))
    assert breaker.state == CircuitState.OPEN

    # Wait for recovery timeout
    await asyncio.sleep(0.15)

    # State should now be HALF_OPEN
    assert breaker.state == CircuitState.HALF_OPEN

    @circuit_breaker(breaker)
    async def test_func():
        return "success"

    mock_settings = MagicMock()
    mock_settings.CIRCUIT_BREAKER_USE_DOUBLE_CHECK = True

    with patch("src.app.core.config.settings", mock_settings):
        # Should allow probe request in HALF_OPEN
        result = await test_func()

    assert result == "success"


@pytest.mark.asyncio
async def test_closed_state_no_lock_overhead():
    """Test that CLOSED state has no lock overhead."""
    breaker = CircuitBreaker(name="test", failure_threshold=5)

    call_count = 0

    @circuit_breaker(breaker)
    async def test_func():
        nonlocal call_count
        call_count += 1
        return "success"

    mock_settings = MagicMock()
    mock_settings.CIRCUIT_BREAKER_USE_DOUBLE_CHECK = True

    with patch("src.app.core.config.settings", mock_settings):
        # Multiple calls should succeed without lock overhead
        for _ in range(10):
            result = await test_func()
            assert result == "success"

    assert call_count == 10


@pytest.mark.asyncio
async def test_half_open_allows_probe_request():
    """Test that HALF_OPEN state allows probe request."""
    breaker = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.1, success_threshold=1)

    # Force circuit to OPEN
    await breaker.record_failure(ValueError("Test error"))
    assert breaker.state == CircuitState.OPEN

    # Wait for recovery
    await asyncio.sleep(0.15)
    assert breaker.state == CircuitState.HALF_OPEN

    @circuit_breaker(breaker)
    async def test_func():
        return "success"

    mock_settings = MagicMock()
    mock_settings.CIRCUIT_BREAKER_USE_DOUBLE_CHECK = True

    with patch("src.app.core.config.settings", mock_settings):
        result = await test_func()

    assert result == "success"
    # Circuit should close after successful probe
    assert breaker.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_concurrent_state_checks_safe():
    """Test that concurrent state checks are safe with double-check pattern."""
    breaker = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=60.0)

    # Force circuit to OPEN
    await breaker.record_failure(ValueError("Test error"))

    @circuit_breaker(breaker)
    async def test_func():
        return "success"

    mock_settings = MagicMock()
    mock_settings.CIRCUIT_BREAKER_USE_DOUBLE_CHECK = True

    # Run 100 concurrent checks
    async def check():
        try:
            await test_func()
            return "success"
        except CircuitBreakerOpen:
            return "rejected"

    with patch("src.app.core.config.settings", mock_settings):
        results = await asyncio.gather(*[check() for _ in range(100)])

    # All should be rejected
    assert all(r == "rejected" for r in results)


@pytest.mark.asyncio
async def test_half_open_race_condition_prevented():
    """Test that HALF_OPEN prevents multiple probe requests."""
    breaker = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.1, success_threshold=1)

    # Force circuit to OPEN
    await breaker.record_failure(ValueError("Test error"))

    # Wait for recovery
    await asyncio.sleep(0.15)
    assert breaker.state == CircuitState.HALF_OPEN

    probe_count = 0

    @circuit_breaker(breaker)
    async def test_func():
        nonlocal probe_count
        probe_count += 1
        await asyncio.sleep(0.01)  # Simulate work
        return "success"

    mock_settings = MagicMock()
    mock_settings.CIRCUIT_BREAKER_USE_DOUBLE_CHECK = True

    # Try to execute multiple probes concurrently
    async def try_probe():
        try:
            return await test_func()
        except CircuitBreakerOpen:
            return "rejected"

    with patch("src.app.core.config.settings", mock_settings):
        results = await asyncio.gather(*[try_probe() for _ in range(10)])

    # At least one should succeed (the probe)
    success_count = sum(1 for r in results if r == "success")
    assert success_count >= 1

    # Circuit should be closed after successful probe
    assert breaker.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_feature_flag_toggles_behavior():
    """Test that feature flag controls double-check behavior."""
    breaker = CircuitBreaker(name="test", failure_threshold=1)

    # Force circuit to OPEN
    await breaker.record_failure(ValueError("Test error"))

    @circuit_breaker(breaker)
    async def test_func():
        return "success"

    # Test with feature flag disabled (default behavior)
    mock_settings_disabled = MagicMock()
    mock_settings_disabled.CIRCUIT_BREAKER_USE_DOUBLE_CHECK = False

    with patch("src.app.core.config.settings", mock_settings_disabled):
        with pytest.raises(CircuitBreakerOpen):
            await test_func()

    # Test with feature flag enabled (optimized behavior)
    mock_settings_enabled = MagicMock()
    mock_settings_enabled.CIRCUIT_BREAKER_USE_DOUBLE_CHECK = True

    with patch("src.app.core.config.settings", mock_settings_enabled):
        with pytest.raises(CircuitBreakerOpen):
            await test_func()

    # Both should reject, but optimized path uses double-check


@pytest.mark.asyncio
async def test_fallback_path_works():
    """Test that fallback path (feature flag disabled) works correctly."""
    breaker = CircuitBreaker(name="test", failure_threshold=2)

    call_count = 0

    @circuit_breaker(breaker)
    async def test_func():
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise ValueError("Test error")
        return "success"

    mock_settings = MagicMock()
    mock_settings.CIRCUIT_BREAKER_USE_DOUBLE_CHECK = False

    with patch("src.app.core.config.settings", mock_settings):
        # First two calls fail
        with pytest.raises(ValueError):
            await test_func()
        with pytest.raises(ValueError):
            await test_func()

        # Circuit should be open
        assert breaker.state == CircuitState.OPEN

        # Next call should be rejected
        with pytest.raises(CircuitBreakerOpen):
            await test_func()

    # Call count should be 2 (third was rejected)
    assert call_count == 2


@pytest.mark.asyncio
async def test_monitoring_metrics_recorded():
    """Test that monitoring metrics are recorded for state transitions."""
    breaker = CircuitBreaker(name="test", failure_threshold=1, success_threshold=1, recovery_timeout=0.1)

    @circuit_breaker(breaker)
    async def test_func():
        return "success"

    mock_settings = MagicMock()
    mock_settings.CIRCUIT_BREAKER_USE_DOUBLE_CHECK = True

    with patch("src.app.core.config.settings", mock_settings):
        with patch("src.app.core.monitoring_metrics.record_circuit_breaker_transition") as mock_record:
            # Cause failure to open circuit
            breaker._failure_count = breaker.failure_threshold
            await breaker.record_failure(ValueError("Test error"))

            # Verify transition to OPEN was recorded
            mock_record.assert_called_with("open")

            # Wait for recovery
            await asyncio.sleep(0.15)

            # Successful probe should close circuit
            await test_func()

            # Verify transition to CLOSED was recorded
            assert any(call[0][0] == "closed" for call in mock_record.call_args_list)
