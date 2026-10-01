import time

import pytest

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
    """P1-10: an OPEN circuit raises the DOMAIN exception, not HTTPException.

    An HTTP response object below the transport escapes into the ARQ worker,
    where create_error_response never runs and the failure becomes a silent
    DLQ entry instead of a visible error. The wire status is unchanged: the
    central handler maps ServiceUnavailableError to 503.
    """
    from fastapi import HTTPException as FastAPIHTTPException

    from src.app.shared.exceptions import ServiceUnavailableError

    db_circuit._open()
    db_circuit._last_failure_time = time.time()

    with pytest.raises(ServiceUnavailableError) as exc_info:
        async with db_circuit_guard():
            pass

    assert not isinstance(exc_info.value, FastAPIHTTPException)
    assert exc_info.value.http_status == 503
    assert "temporarily degraded" in str(exc_info.value.detail)
