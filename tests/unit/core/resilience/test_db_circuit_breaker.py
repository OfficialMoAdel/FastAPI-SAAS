import time
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.resilience.db_circuit_breaker import (
    db_circuit,
    db_circuit_guard,
    execute_query_with_circuit_breaker,
    execute_transaction_with_circuit_breaker,
    get_read_only_fallback,
)
from src.app.core.utils.circuit_breaker import CircuitState


@pytest.fixture
async def mock_db_session():
    session = AsyncMock(spec=AsyncSession)
    # Mock result for execute
    mock_result = Mock()
    mock_result.fetchall.return_value = []
    session.execute.return_value = mock_result
    return session


@pytest.fixture
async def reset_circuit():
    """Reset circuit breaker before each test."""
    db_circuit.reset()
    yield
    db_circuit.reset()


@pytest.mark.asyncio
async def test_execute_query_success(mock_db_session, reset_circuit):
    """Test successful query execution."""
    # Setup mock return
    mock_row = Mock()
    mock_row._mapping = {"id": 1, "name": "test"}
    mock_result = Mock()
    mock_result.fetchall.return_value = [mock_row]
    mock_db_session.execute.return_value = mock_result

    result = await execute_query_with_circuit_breaker(mock_db_session, "SELECT * FROM users")

    assert len(result) == 1
    assert result[0]["id"] == 1
    assert db_circuit.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_execute_query_failure_record(mock_db_session, reset_circuit):
    """Test that infrastructure failures are recorded in the circuit breaker."""
    mock_db_session.execute.side_effect = ConnectionError("DB Connection Lost")

    with pytest.raises(ConnectionError):
        await execute_query_with_circuit_breaker(mock_db_session, "SELECT * FROM users")

    assert db_circuit._failure_count == 1


@pytest.mark.asyncio
async def test_execute_query_business_error_ignored(mock_db_session, reset_circuit):
    """Test that non-infrastructure errors do not trip the circuit breaker."""
    mock_db_session.execute.side_effect = Exception("SQL Syntax Error")

    with pytest.raises(Exception, match="SQL Syntax Error"):
        await execute_query_with_circuit_breaker(mock_db_session, "SELECT * FROM users")

    assert db_circuit._failure_count == 0


@pytest.mark.asyncio
async def test_circuit_open_fallback(mock_db_session, reset_circuit):
    """Test fallback when circuit is open."""
    # Force open circuit
    db_circuit._open()
    # Set last failure time to now so it doesn't immediately go to HALF_OPEN
    db_circuit._last_failure_time = time.time()

    fallback_data = [{"id": 0, "name": "fallback"}]

    result = await execute_query_with_circuit_breaker(
        mock_db_session, "SELECT * FROM users", fallback_data=fallback_data
    )

    assert result == fallback_data
    # Should not have called DB
    mock_db_session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_transaction_success(mock_db_session, reset_circuit):
    """Test successful transaction."""

    async def op1(db):
        return "result1"

    async def op2(db):
        return "result2"

    results = await execute_transaction_with_circuit_breaker(mock_db_session, [op1, op2])

    assert results == ["result1", "result2"]
    assert db_circuit.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_transaction_retry(mock_db_session, reset_circuit):
    """Test transaction retry logic on infrastructure errors."""
    # Fail once then succeed
    op = AsyncMock()
    op.side_effect = [ConnectionError("Fail Connection"), "Success"]

    results = await execute_transaction_with_circuit_breaker(mock_db_session, [op], max_retries=3)

    assert results == ["Success"]
    assert op.call_count == 2
    # W3: the final successful attempt resets _failure_count — a success
    # proves the dependency is healthy, so stale failures must not accumulate
    # toward the threshold across the breaker's lifetime.
    assert db_circuit._failure_count == 0


@pytest.mark.asyncio
async def test_transaction_business_error_no_retry(mock_db_session, reset_circuit):
    """Test transaction does not retry or record failure on non-infrastructure errors."""
    op = AsyncMock()
    op.side_effect = ValueError("Validation Error")

    with pytest.raises(ValueError, match="Validation Error"):
        await execute_transaction_with_circuit_breaker(mock_db_session, [op], max_retries=3)

    assert op.call_count == 1
    assert db_circuit._failure_count == 0


def test_get_read_only_fallback():
    """Test fallback response structure."""
    fallback = get_read_only_fallback()
    assert fallback["status"] == "degraded"
    assert "read-only" in fallback["mode"]
    assert "create" in fallback["blocked_operations"]


@pytest.mark.asyncio
async def test_transaction_retry_has_jitter(mock_db_session, reset_circuit, monkeypatch):
    """Test that retry backoff includes jitter (non-deterministic wait)."""
    import asyncio

    recorded_waits: list[float] = []

    async def spy_sleep(seconds):
        recorded_waits.append(seconds)
        return

    monkeypatch.setattr(asyncio, "sleep", spy_sleep)

    # Fail twice then succeed
    op = AsyncMock()
    op.side_effect = [ConnectionError("Fail1"), ConnectionError("Fail2"), "Success"]

    results = await execute_transaction_with_circuit_breaker(mock_db_session, [op], max_retries=3)

    assert results == ["Success"]
    assert len(recorded_waits) == 2  # Two retries before success

    # Attempt 1: base = 2^1 = 2, jitter = 0.5..1.5 → range [1.0, 3.0]
    assert 1.0 <= recorded_waits[0] <= 3.0, f"Wait 1 out of bounds: {recorded_waits[0]}"
    # Attempt 2: base = 2^2 = 4, jitter = 0.5..1.5 → range [2.0, 6.0]
    assert 2.0 <= recorded_waits[1] <= 6.0, f"Wait 2 out of bounds: {recorded_waits[1]}"


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
