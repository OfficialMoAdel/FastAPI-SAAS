import time
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.resilience.db_circuit_breaker import (
    db_circuit,
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
    # _success_count is not incremented in CLOSED state


@pytest.mark.asyncio
async def test_execute_query_failure_record(mock_db_session, reset_circuit):
    """Test that failures are recorded in the circuit breaker."""
    mock_db_session.execute.side_effect = Exception("DB Error")

    with pytest.raises(Exception):
        await execute_query_with_circuit_breaker(mock_db_session, "SELECT * FROM users")

    assert db_circuit._failure_count == 1


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
    """Test transaction retry logic."""
    # Fail once then succeed
    op = AsyncMock()
    op.side_effect = [Exception("Fail"), "Success"]

    results = await execute_transaction_with_circuit_breaker(mock_db_session, [op], max_retries=3)

    assert results == ["Success"]
    assert op.call_count == 2
    assert db_circuit._failure_count == 1
    # Success count only increments in HALF_OPEN


def test_get_read_only_fallback():
    """Test fallback response structure."""
    fallback = get_read_only_fallback()
    assert fallback["status"] == "degraded"
    assert "read-only" in fallback["mode"]
    assert "create" in fallback["blocked_operations"]
