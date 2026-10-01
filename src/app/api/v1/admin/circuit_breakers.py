"""Circuit breaker administration API endpoints.

Provides admin-level access to circuit breaker monitoring and control,
including health summaries, state inspection, and reset operations.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, status

from src.app.core.resilience.registry import get_registry
from src.app.schemas.responses import (
    CircuitBreakerResetAllResponse,
    CircuitBreakerResetResponse,
)

router = APIRouter(
    prefix="/circuit-breakers",
    tags=["admin", "circuit-breakers"],
)


@router.get("/", response_model=dict[str, Any])
async def get_health_summary() -> dict[str, Any]:
    """Get health summary for all circuit breakers.

    Returns dashboard metrics including total count, state distribution,
    health percentage, and list of all circuit breakers with their details.
    """
    registry = get_registry()
    health = registry.get_health_summary()

    # Include detailed list of all circuit breakers
    health["circuit_breakers"] = registry.get_all_stats()

    return health


@router.get("/{name}", response_model=dict[str, Any])
async def get_circuit_breaker(name: str) -> dict[str, Any]:
    """Get details for a specific circuit breaker.

    Parameters
    ----------
        name: Circuit breaker identifier

    Returns
    -------
        Circuit breaker details including state, failure count, thresholds, and service type

    Raises
    ------
        HTTPException: 404 if circuit breaker not found
    """
    registry = get_registry()
    breaker = registry.get(name)

    if not breaker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Circuit breaker '{name}' not found",
        )

    # Get spec for additional metadata
    specs = registry.get_all()
    spec = specs.get(name)

    return {
        "name": name,
        "state": breaker.state.value,
        "failure_count": breaker.failure_count,
        "failure_threshold": breaker.failure_threshold,
        "recovery_timeout": breaker.recovery_timeout,
        "success_threshold": breaker.success_threshold,
        "last_failure_time": breaker.last_failure_time,
        "service_type": spec.service_type.value if spec else "unknown",
        "description": spec.description if spec else "",
    }


@router.post("/{name}/reset", response_model=CircuitBreakerResetResponse)
async def reset_circuit_breaker(name: str) -> CircuitBreakerResetResponse:
    """Reset a specific circuit breaker to closed state.

    Parameters
    ----------
        name: Circuit breaker identifier

    Returns
    -------
        Status confirmation with circuit breaker name

    Raises
    ------
        HTTPException: 404 if circuit breaker not found
    """
    registry = get_registry()
    success = await registry.reset(name)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Circuit breaker '{name}' not found",
        )

    return CircuitBreakerResetResponse(status="reset", name=name)


@router.post("/reset-all", response_model=CircuitBreakerResetAllResponse)
async def reset_all_circuit_breakers() -> CircuitBreakerResetAllResponse:
    """Reset all circuit breakers to closed state.

    Useful for incident response and recovery operations.
    Resets every registered circuit breaker back to healthy state.

    Returns
    -------
        Status confirmation with count of reset circuit breakers
    """
    registry = get_registry()
    count = await registry.reset_all()

    return CircuitBreakerResetAllResponse(status="reset", count=count)
