import time

import pytest
from fastapi import HTTPException

from src.app.core.resilience.db_circuit_breaker import (
    db_circuit,
    db_circuit_guard,
)
from src.app.core.utils.circuit_breaker import CircuitState


@pytest.fixture
async def reset_circuit():
    """Reset circuit breaker before each test."""
    db_circuit.reset()
    yield
    db_circuit.reset()


# --- Tests for db_circuit_guard ---


@pytest.mark.asyncio
async def test_db_circuit_guard_success(reset_circuit):
    """Test db_circuit_guard allows execution and records success."""
    async with db_circuit_guard():
        pass
    assert db_circuit.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_db_circuit_guard_infrastructure_failure(reset_circuit):
    """Test db_circuit_guard records failure on infrastructure error."""
    with pytest.raises(ConnectionError):
        async with db_circuit_guard():
            raise ConnectionError("DB Down")
    assert db_circuit._failure_count == 1


@pytest.mark.asyncio
async def test_db_circuit_guard_business_error_ignored(reset_circuit):
    """Test db_circuit_guard ignores non-infrastructure error."""
    with pytest.raises(ValueError):
        async with db_circuit_guard():
            raise ValueError("Invalid ID")
    assert db_circuit._failure_count == 0


@pytest.mark.asyncio
async def test_db_circuit_guard_open_blocks_request(reset_circuit):
    """Test db_circuit_guard raises 503 when circuit is OPEN."""
    db_circuit._open()
    db_circuit._last_failure_time = time.time()

    with pytest.raises(HTTPException) as exc_info:
        async with db_circuit_guard():
            pass

    assert exc_info.value.status_code == 503
    assert "temporarily degraded" in exc_info.value.detail
