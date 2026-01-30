import pytest
import asyncio
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from src.app.core.resilience.db_circuit_breaker import (
    db_circuit,
    execute_query_with_circuit_breaker,
    execute_transaction_with_circuit_breaker,
    get_read_only_fallback,
)
from src.app.core.utils.circuit_breaker import CircuitState, CircuitBreakerOpen
from src.app.core.utils import cache


@pytest.mark.asyncio
async def test_db_circuit_breaker_e2e(db_session: AsyncSession):
    """
    E2E Test for Database Circuit Breaker.

    Covers:
    1. Normal query execution (CLOSED state)
    2. Failure counting and state transition (CLOSED -> OPEN)
    3. Fast failure when OPEN
    4. Cache fallback when OPEN
    5. Static fallback when OPEN
    6. Read-only mode fallback
    7. Transaction retry logic
    8. Recovery (OPEN -> HALF_OPEN -> CLOSED)
    """

    # Ensure circuit starts closed
    db_circuit.reset()
    assert db_circuit.state == CircuitState.CLOSED

    # 1. Normal query execution
    query = "SELECT 1"
    result = await execute_query_with_circuit_breaker(db_session, query)
    assert result is not None
    # Adjust for SQLite/PostgreSQL differences in column naming
    # PostgreSQL via asyncpg returns Record objects that act like mappings
    # SQLite via aiosqlite returns Row objects
    # We'll just verify we got a result with the value 1
    first_row = result[0]
    # Check if dict (from our conversion in db_circuit_breaker) or other
    if isinstance(first_row, dict):
        assert list(first_row.values())[0] == 1
    else:
        # Fallback for direct row access if conversion logic changes
        assert first_row[0] == 1

    # 2. Simulate failures to trip circuit
    # We need to mock the db.execute to raise exception
    # Since we can't easily make the real DB fail on demand without breaking the connection for everyone,
    # we'll mock the session execute method for the failure simulation

    original_execute = db_session.execute

    # Mock failure
    db_session.execute = AsyncMock(side_effect=Exception("DB Connection Failed"))

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
    # We can't set state directly as it's a property.
    # We need to simulate failures again or use the internal _state if available,
    # but let's stick to public API or mocking.

    # Reset first
    db_circuit.reset()
    assert db_circuit.state == CircuitState.CLOSED

    # Restore mock for failures
    db_session.execute = AsyncMock(side_effect=Exception("DB Connection Failed 2"))

    for _ in range(3):
        try:
            await execute_query_with_circuit_breaker(db_session, query)
        except Exception:
            pass

    assert db_circuit.state == CircuitState.OPEN

    # Force recovery timeout to expire by modifying last_failure_time
    # This is accessing internal attribute but necessary for testing timing dependent logic deterministically
    db_circuit._last_failure_time = 0

    # Restore real DB execution
    db_session.execute = original_execute

    # Next call should be allowed (HALF_OPEN)
    # We need to simulate time passing or force check
    # The circuit breaker checks time.time() > last_failure + timeout

    with patch("time.time", return_value=9999999999.9):
        # This call should go through and close the circuit
        # We need to succeed enough times to close the circuit (success_threshold)
        for _ in range(db_circuit.success_threshold):
            result = await execute_query_with_circuit_breaker(db_session, query)
            assert result is not None

        # NOTE: db_circuit.state relies on checking time.time() inside the property.
        # However, _state isn't updated to CLOSED until a successful request COMPLETES
        # and calls record_success().
        # record_success updates internal _state.

        # Wait a tiny bit for the async lock update to propagate if needed, though record_success awaits it.
        # Check internal state too for debugging if this fails again.
        assert db_circuit.state == CircuitState.CLOSED
