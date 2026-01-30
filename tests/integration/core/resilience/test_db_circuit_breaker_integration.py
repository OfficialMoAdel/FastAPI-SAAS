"""Integration tests for Database Circuit Breaker.

Tests real database interactions, cache fallback, and circuit breaker behavior
with actual Postgres and Redis instances.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.resilience.db_circuit_breaker import (
    db_circuit,
    execute_query_with_circuit_breaker,
    execute_transaction_with_circuit_breaker,
    get_read_only_fallback,
)
from src.app.core.utils.cache import client as redis_client
from src.app.core.utils.circuit_breaker import CircuitState


@pytest.fixture(autouse=True)
def reset_circuit():
    """Reset circuit breaker state before and after each test."""
    db_circuit.reset()
    yield
    db_circuit.reset()


@pytest.mark.asyncio
async def test_execute_query_success_path(real_postgres: AsyncSession):
    """Test successful query execution closes circuit and caches result."""
    query = "SELECT 1"
    cache_key = "test:circuit:select_1"

    # Need redis client for caching tests
    if redis_client is None:
        pytest.skip("Redis client not initialized")

    # Execute query
    result = await execute_query_with_circuit_breaker(db=real_postgres, query=query, cache_key=cache_key, cache_ttl=60)

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
async def test_execute_query_cache_fallback(real_postgres: AsyncSession):
    """Test fallback to cache when database fails."""
    query = "SELECT 1"
    cache_key = "test:circuit:fallback"
    expected_data = [{"id": 1, "name": "cached"}]

    if redis_client is None:
        pytest.skip("Redis client not initialized")

    # Pre-populate cache
    import json

    await redis_client.setex(cache_key, 60, json.dumps(expected_data))

    # Force circuit open
    db_circuit._state = CircuitState.OPEN
    db_circuit._failure_count = db_circuit.failure_threshold

    # Execute query expecting cache fallback
    result = await execute_query_with_circuit_breaker(db=real_postgres, query=query, cache_key=cache_key)

    assert result == expected_data


@pytest.mark.asyncio
async def test_execute_query_static_fallback(real_postgres: AsyncSession):
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
    result = await execute_query_with_circuit_breaker(db=real_postgres, query="SELECT 1", fallback_data=fallback_data)

    assert result == fallback_data


@pytest.mark.asyncio
async def test_circuit_opens_after_threshold(real_postgres: AsyncSession):
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
async def test_transaction_success(real_postgres: AsyncSession):
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
async def test_transaction_retry_and_failure(real_postgres: AsyncSession):
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
async def test_read_only_fallback_structure():
    """Verify read-only fallback structure."""
    fallback = get_read_only_fallback()

    assert "status" in fallback
    assert fallback["status"] == "degraded"
    assert fallback["mode"] == "read-only"
    assert "timestamp" not in fallback  # Based on actual implementation
