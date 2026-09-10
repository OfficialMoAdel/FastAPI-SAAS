"""
Database Circuit Breaker Usage Examples.

This script provides practical examples of how to use the database circuit breaker
(`db_circuit_guard`) in your services and API endpoints.

Usage:
    These examples are designed to be copied into your service layer or
    API route handlers.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import async_get_db

# Import the circuit breaker utilities
from src.app.core.resilience import db_circuit, db_circuit_guard
from src.app.core.utils.circuit_breaker import CircuitState

# Mock router for examples
router = APIRouter()


# -----------------------------------------------------------------------------
# Example 1: Protect a Read with the Guard
# -----------------------------------------------------------------------------
async def get_user_dashboard_stats(user_id: str, db: AsyncSession) -> dict[str, Any]:
    """
    Fetch dashboard stats guarded by the database circuit breaker.

    If the circuit is OPEN, the guard raises HTTPException(503) immediately
    (fail fast) instead of piling queries onto a struggling database.
    Only infrastructure errors (timeouts, disconnects) trip the circuit —
    business-logic errors pass through without affecting it.
    """
    query = text(
        """
        SELECT total_views, active_projects, storage_used
        FROM user_stats
        WHERE user_id = :user_id
        """
    )

    try:
        async with db_circuit_guard():
            result = await db.execute(query, {"user_id": user_id})
            rows = [dict(row._mapping) for row in result.fetchall()]
    except HTTPException:
        # Circuit OPEN — fail fast; serve cached/stale data here if you have it
        raise HTTPException(status_code=503, detail="Service unavailable")
    except OSError as e:
        # Infrastructure error recorded on the circuit; propagate as 503
        raise HTTPException(status_code=503, detail="Service unavailable") from e

    if not rows:
        return {"total_views": 0, "active_projects": 0, "storage_used": 0}

    return rows[0]


# -----------------------------------------------------------------------------
# Example 2: Protect a Write
# -----------------------------------------------------------------------------
async def transfer_credits(from_user: str, to_user: str, amount: int, db: AsyncSession) -> bool:
    """
    Execute a critical write guarded by the circuit breaker.

    Business-logic errors (e.g. insufficient funds, constraint violations)
    do NOT trip the circuit — only infrastructure failures do.
    """

    try:
        async with db_circuit_guard():
            await db.execute(
                text("UPDATE users SET credits = credits - :amt WHERE id = :uid"),
                {"amt": amount, "uid": from_user},
            )
            await db.execute(
                text("UPDATE users SET credits = credits + :amt WHERE id = :uid"),
                {"amt": amount, "uid": to_user},
            )
            await db.commit()
        return True

    except HTTPException as e:
        # Circuit OPEN — fail fast without touching the DB
        print(f"Transfer rejected, circuit degraded: {e.detail}")
        return False
    except OSError as e:
        await db.rollback()
        print(f"Transfer failed with infrastructure error: {e}")
        return False


# -----------------------------------------------------------------------------
# Example 3: Read-Only Mode Degradation
# -----------------------------------------------------------------------------
@router.post("/posts")
async def create_post(title: str, db: Annotated[AsyncSession, Depends(async_get_db)]) -> dict[str, Any]:
    """
    API endpoint that gracefully handles DB outages by checking circuit state.
    """

    # Check if we are in a degraded state explicitly
    if db_circuit.state == CircuitState.OPEN:
        # Return a standardized degraded response instead of attempting
        # writes that will fail
        return {
            "status": "degraded",
            "message": "Database temporarily degraded, running in read-only mode",
            "mode": "read-only",
        }

    # Normal write operation, still guarded so infra failures trip the circuit
    try:
        async with db_circuit_guard():
            # ... perform write ...
            return {"status": "created", "id": "123"}
    except HTTPException:
        raise HTTPException(status_code=503, detail="Service unavailable")
