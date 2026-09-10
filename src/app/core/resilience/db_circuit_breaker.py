"""Database-specific circuit breaker with fallback strategies."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import HTTPException, status
from sqlalchemy.exc import (
    DisconnectionError,
    OperationalError,
)
from sqlalchemy.exc import (
    TimeoutError as SQLAlchemyTimeoutError,
)

from src.app.core.logger import get_logger
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
