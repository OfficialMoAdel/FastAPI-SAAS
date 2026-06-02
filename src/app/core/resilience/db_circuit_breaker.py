"""Database-specific circuit breaker with fallback strategies."""

import asyncio
import json
import random
from collections.abc import Callable
from typing import Any

from sqlalchemy import Executable, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.logger import get_logger
from src.app.core.resilience.registry import ServiceType, create_circuit_breaker
from src.app.core.utils import cache
from src.app.core.utils.circuit_breaker import CircuitState

logger = get_logger(__name__)

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
    query: str | Executable,
    params: dict | None = None,
    cache_key: str | None = None,
    cache_ttl: int = 300,
    fallback_data: list | None = None,
) -> list | None:
    """
    Execute query with circuit breaker protection.

    Args:
        db: Database session
        query: SQL query string or SQLAlchemy Executable
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
                    logger.info("Using cached data", cache_key=cache_key)
                    # Assume simple JSON serialization for fallback data
                    return list(json.loads(cached))
            except Exception as cache_error:
                logger.error("Cache retrieval failed:", cache_error=cache_error)

        # Use static fallback if provided
        if fallback_data is not None:
            # SECURITY WARNING: if query is Executable, slicing [:100] on str() is safe.
            query_str = str(query)[:100]
            logger.warning("Using fallback data for query", query_snippet=query_str)
            return fallback_data

        # No fallback available
        raise RuntimeError("Database circuit breaker is OPEN and no fallback available")

    # HALF_OPEN state: allow one test request through.
    # When the request completes, record_success() or record_failure()
    # will transition the circuit to CLOSED or OPEN respectively.
    # NOTE: No manual _state manipulation is needed here — the circuit
    # is already in HALF_OPEN and will self-transition on the next outcome.
    if current_state == CircuitState.HALF_OPEN:
        logger.debug("Database circuit HALF_OPEN — allowing test request")

    # Execute query
    try:
        compiled_query: Executable
        if isinstance(query, str):
            compiled_query = text(query)
        else:
            compiled_query = query

        result = await db.execute(compiled_query, params or {})

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
                logger.warning("Failed to cache result:", cache_error=cache_error)

        return rows

    except Exception as e:
        await db_circuit.record_failure(e)
        logger.error("Database query failed", error=str(e), circuit_state=db_circuit.state.value)

        # Try fallback on failure
        if cache_key and cache.client:
            try:
                cached = await cache.client.get(cache_key)
                if cached:
                    logger.info("Using cached data after failure", cache_key=cache_key)
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
    last_error: Exception | None = None

    while attempt <= max_retries:
        # Check circuit state
        current_state = db_circuit.state
        if current_state == CircuitState.OPEN:
            logger.error("Database circuit OPEN, aborting transaction after attempts", attempt=attempt)
            break

        # HALF_OPEN: allow the request through. record_success/failure will
        # handle the state transition — no manual _state update is needed.
        if current_state == CircuitState.HALF_OPEN:
            logger.debug("Database circuit HALF_OPEN — allowing test transaction")

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
                # Use proportional jitter (industry standard) rather than tiny fixed jitter
                wait_time = (2 ** attempt) * random.uniform(0.5, 1.5)
                logger.warning(
                    "Transaction failed, retrying",
                    wait_time=round(wait_time, 3),
                    attempt=attempt,
                    max_retries=max_retries,
                    error=str(e),
                )
                await asyncio.sleep(wait_time)

    # All retries exhausted
    logger.error("Transaction failed", max_attempts=max_retries, error=str(last_error))
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
