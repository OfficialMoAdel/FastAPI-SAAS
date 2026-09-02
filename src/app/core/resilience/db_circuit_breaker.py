"""Database-specific circuit breaker with fallback strategies."""

import asyncio
import random
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Executable, text
from sqlalchemy.exc import (
    DisconnectionError,
    OperationalError,
)
from sqlalchemy.exc import (
    TimeoutError as SQLAlchemyTimeoutError,
)
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.redis_provider import get_redis
from src.app.core.resilience.registry import ServiceType, create_circuit_breaker
from src.app.core.utils.circuit_breaker import CircuitState

logger = get_logger(__name__)

# Infrastructure errors that indicate DB outage/unavailability.
# DBAPIError is intentionally excluded: it is the base for IntegrityError /
# DataError / ProgrammingError which are business-logic errors and must NOT
# trip the circuit breaker (otherwise duplicate-key / validation errors would
# open the circuit under load).
DB_INFRASTRUCTURE_ERRORS = (
    OperationalError,
    SQLAlchemyTimeoutError,
    DisconnectionError,
    asyncio.TimeoutError,
    ConnectionError,
    OSError,
)

# Create database circuit breaker with tuned thresholds for critical service stability
db_circuit = create_circuit_breaker(
    name="database",
    failure_threshold=5,  # Tuned for production stability against transient spikes
    recovery_timeout=30.0,  # Shorter timeout for DB recovery
    success_threshold=2,
    service_type=ServiceType.DATABASE,
    description="Database connection circuit breaker",
)


@asynccontextmanager
async def db_circuit_guard() -> AsyncIterator[None]:
    """Async context manager that protects database operations with the circuit breaker.

    Checks if the database circuit breaker is OPEN before executing the wrapped code.
    If OPEN, raises HTTPException(503 Service Unavailable) to fail fast without
    overloading the struggling database.

    Only records failures for actual database infrastructure/connection errors
    (timeouts, operational errors, disconnections), ignoring business logic
    errors (IntegrityError, ModelNotFoundError, validation errors).
    """
    current_state = db_circuit.state
    if current_state == CircuitState.OPEN:
        logger.warning("Database circuit OPEN, blocking database operation")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service temporarily degraded due to high failure rate. Please retry later.",
        )

    if current_state == CircuitState.HALF_OPEN:
        async with db_circuit._get_lock():
            if db_circuit.state == CircuitState.HALF_OPEN:
                if not db_circuit.try_acquire_probe():
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Database service temporarily degraded. Probing recovery.",
                    )
            elif db_circuit.state == CircuitState.OPEN:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Database service temporarily degraded due to high failure rate. Please retry later.",
                )
        logger.debug("Database circuit HALF_OPEN — allowing test request")

    try:
        yield
        await db_circuit.record_success()
    except DB_INFRASTRUCTURE_ERRORS as e:
        await db_circuit.record_failure(e)
        logger.error(
            "Database infrastructure failure recorded on circuit breaker",
            error=str(e),
            error_type=type(e).__name__,
            circuit_state=db_circuit.state.value,
        )
        raise
    except Exception:
        # Business errors (IntegrityError, 404s, validation) do not trip
        # the DB circuit breaker, but MUST clear the HALF_OPEN probe flag
        # so the next recovery window can actually probe (otherwise wedge).
        await db_circuit.clear_probe_async()
        raise


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
    client = get_redis()

    # Check circuit state first (fast path)
    current_state = db_circuit.state
    if current_state == CircuitState.OPEN:
        logger.warning("Database circuit OPEN, attempting cache fallback")

        # Try cache fallback
        if cache_key and client:
            try:
                cached = await client.get(cache_key)
                if cached:
                    logger.info("Using cached data", cache_key=cache_key)
                    # Assume simple JSON serialization for fallback data
                    return list(json_loads(cached))
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
        async with db_circuit._get_lock():
            if db_circuit.state == CircuitState.HALF_OPEN:
                if not db_circuit.try_acquire_probe():
                    raise RuntimeError("Database circuit breaker is probing recovery and no fallback available")
            elif db_circuit.state == CircuitState.OPEN:
                raise RuntimeError("Database circuit breaker is OPEN and no fallback available")
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
        if cache_key and client:
            try:
                # Handle datetime/decimal serialization if needed (simplified here)
                # Using strings for non-serializable types for fallback purposes
                await client.setex(cache_key, cache_ttl, json_dumps(rows, default=str, as_str=True))
            except Exception as cache_error:
                logger.warning("Failed to cache result:", cache_error=cache_error)

        return rows

    except DB_INFRASTRUCTURE_ERRORS as e:
        await db_circuit.record_failure(e)
        logger.error(
            "Database query failed with infrastructure error",
            error=str(e),
            circuit_state=db_circuit.state.value,
        )

        # Try fallback on failure
        if cache_key and client:
            try:
                cached = await client.get(cache_key)
                if cached:
                    logger.info("Using cached data after failure", cache_key=cache_key)
                    return list(json_loads(cached))
            except Exception as cache_error:
                logger.debug("Circuit breaker cache fallback failed: %s", cache_error)

        if fallback_data is not None:
            return fallback_data

        raise
    except Exception as e:
        # Non-infrastructure error (e.g. SQL syntax, validation); do not trip circuit breaker
        logger.error("Database query failed with non-infrastructure error", error=str(e))
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

        except DB_INFRASTRUCTURE_ERRORS as e:
            await db.rollback()
            await db_circuit.record_failure(e)
            last_error = e
            attempt += 1

            # Exponential backoff
            if attempt <= max_retries:
                # Use proportional jitter (industry standard) rather than tiny fixed jitter
                wait_time = (2**attempt) * random.uniform(0.5, 1.5)
                logger.warning(
                    "Transaction failed, retrying",
                    wait_time=round(wait_time, 3),
                    attempt=attempt,
                    max_retries=max_retries,
                    error=str(e),
                )
                await asyncio.sleep(wait_time)
        except Exception as e:
            await db.rollback()
            # Non-infrastructure error; do not retry or trip circuit breaker
            logger.error("Transaction failed with non-infrastructure error", error=str(e))
            raise

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
