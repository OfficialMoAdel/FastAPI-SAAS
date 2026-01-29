"""
Database Circuit Breaker Usage Examples.

This script provides practical examples of how to use the database circuit breaker
patterns in your services and API endpoints.

Usage:
    These examples are designed to be copied into your service layer or
    API route handlers.
"""

import asyncio
from typing import Any, Annotated

from fastapi import Depends, APIRouter, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

# Import the circuit breaker utilities
from src.app.core.resilience import (
    execute_query_with_circuit_breaker,
    execute_transaction_with_circuit_breaker,
    get_read_only_fallback,
    db_circuit,
)
from src.app.core.db.database import async_get_db
from src.app.core.utils.circuit_breaker import CircuitState

# Mock router for examples
router = APIRouter()


# -----------------------------------------------------------------------------
# Example 1: Basic Query with Cache Fallback
# -----------------------------------------------------------------------------
async def get_user_dashboard_stats(user_id: str, db: AsyncSession) -> dict[str, Any]:
    """
    Fetch dashboard stats with automatic cache fallback.

    If the database is down or slow, this will return cached data if available.
    The cache key is specific to the user and query.
    """
    query = """
        SELECT total_views, active_projects, storage_used
        FROM user_stats
        WHERE user_id = :user_id
    """

    # Usage:
    # 1. Provide the query and params
    # 2. Provide a cache_key (required for cache fallback)
    # 3. Provide cache_ttl (default is 300s)
    try:
        results = await execute_query_with_circuit_breaker(
            db=db,
            query=query,
            params={"user_id": user_id},
            cache_key=f"dashboard:stats:{user_id}",
            cache_ttl=600,  # Cache for 10 minutes
        )

        if not results:
            return {"total_views": 0, "active_projects": 0, "storage_used": 0}

        return results[0]  # type: ignore

    except Exception as e:
        # If both DB and Cache fail, re-raise or handle gracefully
        # RuntimeError is raised if Circuit is OPEN and no fallback available
        raise HTTPException(status_code=503, detail="Service unavailable") from e


# -----------------------------------------------------------------------------
# Example 2: Query with Static Fallback Data
# -----------------------------------------------------------------------------
async def get_system_status(db: AsyncSession) -> dict[str, Any]:
    """
    Fetch system status with static fallback.

    If the database is unavailable, return a default "degraded" status object
    instead of failing.
    """
    query = "SELECT status, maintenance_mode FROM system_config LIMIT 1"

    # Default data to return if DB is down and not cached
    static_fallback = [{"status": "operational", "maintenance_mode": False}]

    results = await execute_query_with_circuit_breaker(
        db=db,
        query=query,
        # No cache_key needed if we only want static fallback
        # But we can use both: Cache first, then Static Fallback
        fallback_data=static_fallback,
    )

    if results and len(results) > 0:
        return dict(results[0])

    return static_fallback[0]


# -----------------------------------------------------------------------------
# Example 3: Transaction with Retry


# -----------------------------------------------------------------------------
async def transfer_credits(from_user: str, to_user: str, amount: int, db: AsyncSession) -> bool:
    """
    Execute a critical transaction with automatic retries.

    Uses exponential backoff for retries (1s, 2s, 4s...) if the transaction fails
    due to transient errors (deadlocks, connection blips).
    """

    # Define atomic operations
    async def deduct_sender(session: AsyncSession) -> None:
        await session.execute(
            text("UPDATE users SET credits = credits - :amt WHERE id = :uid"), {"amt": amount, "uid": from_user}
        )

    async def credit_receiver(session: AsyncSession) -> None:
        await session.execute(
            text("UPDATE users SET credits = credits + :amt WHERE id = :uid"), {"amt": amount, "uid": to_user}
        )

    try:
        # Execute strictly as a transaction
        # If circuit is OPEN, fails immediately
        # If execution fails, retries up to 3 times (default)
        await execute_transaction_with_circuit_breaker(
            db=db, operations=[deduct_sender, credit_receiver], max_retries=3
        )
        return True

    except RuntimeError as e:
        # Transaction failed after all retries or Circuit was OPEN
        print(f"Transfer failed: {e}")
        return False


# -----------------------------------------------------------------------------
# Example 4: Read-Only Mode Degradation
# -----------------------------------------------------------------------------
@router.post("/posts")
async def create_post(title: str, db: Annotated[AsyncSession, Depends(async_get_db)]) -> dict[str, Any]:
    """
    API endpoint that gracefully handles DB outages by checking circuit state.
    """

    # Check if we are in a degraded state explicitly
    if db_circuit.state == CircuitState.OPEN:
        # Return a standardized read-only response
        # This prevents users from attempting writes that will fail
        return get_read_only_fallback()

    # Normal write operation
    try:
        # ... perform write ...
        return {"status": "created", "id": "123"}
    except Exception:
        raise HTTPException(status_code=500, detail="Write failed")


# -----------------------------------------------------------------------------
# Example 5: Integration with FastAPI Dependency
# -----------------------------------------------------------------------------
# You can create a reusable dependency for resilience
from typing import Callable, Any


async def resilient_query_runner(db: Annotated[AsyncSession, Depends(async_get_db)]) -> Callable[..., Any]:
    """Dependency that returns a configured query runner."""

    async def runner(query: str, **kwargs: Any) -> Any:
        return await execute_query_with_circuit_breaker(db, query, **kwargs)

    return runner


@router.get("/reports")
async def get_reports(run_query: Annotated[Callable[..., Any], Depends(resilient_query_runner)]) -> dict[str, Any]:
    """Route utilizing the resilient query runner dependency."""

    data = await run_query(
        "SELECT * FROM reports ORDER BY created_at DESC LIMIT 10", cache_key="recent_reports", cache_ttl=300
    )
    return {"data": data}
