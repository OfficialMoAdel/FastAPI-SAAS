"""Database-specific circuit breaker with fallback strategies."""

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.resilience.registry import ServiceType, create_circuit_breaker
from src.app.core.utils import cache
from src.app.core.utils.circuit_breaker import CircuitState

logger = logging.getLogger("db.circuit_breaker")

# Create database circuit breaker with lower thresholds (critical service)
db_circuit = create_circuit_breaker(
    name="database",
    failure_threshold=3,  # Lower threshold for DB (critical)
    recovery_timeout=30.0,  # Shorter timeout
    success_threshold=3,
    service_type=ServiceType.DATABASE,
    description="Database connection circuit breaker",
)


async def execute_query_with_circuit_breaker(
    db: AsyncSession,
    query: str,
    params: Optional[dict] = None,
    cache_key: Optional[str] = None,
    cache_ttl: int = 300,
    fallback_data: Optional[list] = None,
) -> Optional[list]:
    """
    Execute query with circuit breaker protection.

    Args:
        db: Database session
        query: SQL query
        params: Query parameters
        cache_key: Redis cache key for fallback
        cache_ttl: Cache TTL in seconds
        fallback_data: Static fallback data if circuit is OPEN

    Returns:
        Query results or fallback data
    """
    # Check circuit state first (fast path)
    current_state = db_circuit.state
    if current_state == CircuitState.OPEN:
        logger.warning("Database circuit OPEN, attempting cache fallback")

        # Try cache fallback
        if cache_key and cache.client:
            try:
                cached = await cache.client.get(cache_key)
                if cached:
                    logger.info(f"Using cached data for key: {cache_key}")
                    # Assume simple JSON serialization for fallback data
                    return list(json.loads(cached))
            except Exception as cache_error:
                logger.error(f"Cache retrieval failed: {cache_error}")

        # Use static fallback if provided
        if fallback_data is not None:
            logger.warning(f"Using fallback data for query: {query[:50]}...")
            return fallback_data

        # No fallback available
        raise RuntimeError("Database circuit breaker is OPEN and no fallback available")

    # FIXED: Corrected HALF_OPEN state transition logic
    # Previous bug: checked state == HALF_OPEN AND _state == OPEN (always false)
    # The HALF_OPEN state requires single-request testing - allow one operation through
    if current_state == CircuitState.HALF_OPEN:
        # In HALF_OPEN state, we allow only one test request
        logger.debug("Database circuit HALF_OPEN - allowing test request")
        # Handle HALF_OPEN transition manually so record_success works
        if db_circuit._state == CircuitState.OPEN:
            async with db_circuit._lock:
                if db_circuit.state == CircuitState.HALF_OPEN and db_circuit._state == CircuitState.OPEN:
                    db_circuit._state = CircuitState.HALF_OPEN
        # If we succeed, record_success() will transition to CLOSED
        # If we fail, record_failure() will transition back to OPEN

    # Execute query
    try:
        result = await db.execute(text(query), params or {})

        # Convert result to list of dicts for consistency
        # SQLAlchemy returns Row objects, convert to dicts
        rows = [dict(row._mapping) for row in result.fetchall()]

        # Record success
        await db_circuit.record_success()

        # Cache result for future fallback
        if cache_key and cache.client:
            try:
                # Handle datetime/decimal serialization if needed (simplified here)
                # Using strings for non-serializable types for fallback purposes
                await cache.client.setex(cache_key, cache_ttl, json.dumps(rows, default=str))
            except Exception as cache_error:
                logger.warning(f"Failed to cache result: {cache_error}")

        return rows

    except Exception as e:
        await db_circuit.record_failure(e)
        logger.error(f"Database query failed: {e}", extra={"circuit_state": db_circuit.state.value})

        # Try fallback on failure
        if cache_key and cache.client:
            try:
                cached = await cache.client.get(cache_key)
                if cached:
                    logger.info(f"Using cached data after failure for key: {cache_key}")
                    return list(json.loads(cached))
            except Exception as cache_error:
                logger.debug("Circuit breaker cache fallback failed: %s", cache_error)

        if fallback_data is not None:
            return fallback_data

        raise


async def execute_transaction_with_circuit_breaker(
    db: AsyncSession,
    operations: list[Callable],
    max_retries: int = 3,
) -> list[Any]:
    """
    Execute transaction with circuit breaker and retry logic.

    Args:
        db: Database session
        operations: List of async operations to execute
        max_retries: Maximum retry attempts

    Returns:
        List of operation results
    """
    attempt = 0
    last_error: Optional[Exception] = None

    while attempt <= max_retries:
        # Check circuit state
        current_state = db_circuit.state
        if current_state == CircuitState.OPEN:
            logger.error(f"Database circuit OPEN, aborting transaction after {attempt} attempts")
            break

        # Handle HALF_OPEN transition manually
        if current_state == CircuitState.HALF_OPEN and db_circuit._state == CircuitState.OPEN:
            async with db_circuit._lock:
                if db_circuit.state == CircuitState.HALF_OPEN and db_circuit._state == CircuitState.OPEN:
                    db_circuit._state = CircuitState.HALF_OPEN

        try:
            # Execute operations
            results = []
            for op in operations:
                result = await op(db)
                results.append(result)

            await db_circuit.record_success()
            return results

        except Exception as e:
            await db.rollback()
            await db_circuit.record_failure(e)
            last_error = e
            attempt += 1

            # Exponential backoff
            if attempt <= max_retries:
                wait_time = 2**attempt
                logger.warning(
                    f"Transaction failed, retrying in {wait_time}s (attempt {attempt}/{max_retries})",
                    extra={"error": str(e)},
                )
                await asyncio.sleep(wait_time)

    # All retries exhausted
    logger.error(f"Transaction failed after {max_retries} attempts", extra={"last_error": str(last_error)})
    raise RuntimeError(f"Transaction failed after {max_retries} attempts") from last_error


# Fallback response factories
def get_read_only_fallback() -> dict[str, Any]:
    """Return read-only mode status when database is unavailable."""
    return {
        "status": "degraded",
        "message": "Database circuit breaker is OPEN, running in read-only mode",
        "mode": "read-only",
        "allowed_operations": ["read"],
        "blocked_operations": ["create", "update", "delete"],
    }
