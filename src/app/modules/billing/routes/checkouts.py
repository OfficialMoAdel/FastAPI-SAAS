"""Checkouts router - API compatibility layer for subscription management.

Provides /api/v1/checkouts endpoints for frontend compatibility with old backend.
Delegates to billing module's CheckoutService and UserSubscriptionService.
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import get_current_user
from src.app.core.db.database import async_get_db
from src.app.core.logger import get_logger
from src.app.modules.billing.schemas import (
    CheckoutCreate,
    UserSubscriptionRead,
)
from src.app.modules.billing.schemas import (
    CheckoutSession as CheckoutSessionSchema,
)
from src.app.modules.billing.service import (
    get_checkout_service,
    get_user_subscription_service,
)

router = APIRouter(prefix="/checkouts", tags=["checkouts"])
logger = get_logger(__name__)


@router.post("", response_model=CheckoutSessionSchema)
async def create_checkout(
    data: CheckoutCreate,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> CheckoutSessionSchema:
    """Create a Stripe checkout session for subscription.

    Args:
        data: Checkout session creation data
        current_user: Authenticated user
        db: Database session

    Returns:
        Checkout session with URL to redirect user to Stripe
    """
    service = get_checkout_service(db)
    return await service.create_session(
        user_id=current_user["id"],
        user_email=current_user["email"],
        data=data,
    )


@router.get("/current", response_model=UserSubscriptionRead | None)
async def get_current_subscription(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict | None:
    """Get the current user's active subscription.

    Returns the most recent active subscription if one exists.

    Args:
        current_user: Authenticated user
        db: Database session

    Returns:
        Current subscription details or None
    """
    service = get_user_subscription_service(db)
    result = await service.get_user_subscriptions(current_user["id"])
    subscriptions = result.get("data", [])

    # Return the first active subscription (most recent)
    if subscriptions:
        # Filter for active subscriptions
        active_subs = [s for s in subscriptions if s.get("status") in {"active", "trialing", "past_due"}]
        if active_subs:
            return dict(active_subs[0])

    return None


@router.post("/cancel", response_model=UserSubscriptionRead)
async def cancel_current_subscription(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    subscription_id: Annotated[int | None, Body(embed=True)] = None,
) -> dict:
    """Cancel the user's subscription at period end.

    Old frontend compatibility: Accepts subscription_id in request body.
    If not provided, cancels the current active subscription.

    Args:
        current_user: Authenticated user
        db: Database session
        subscription_id: Optional subscription ID (from body for old frontend compat)

    Returns:
        Updated subscription details

    Raises:
        HTTPException: If no active subscription found or user not authorized
    """
    service = get_user_subscription_service(db)

    # If subscription_id not provided, find the current active subscription
    if subscription_id is None:
        result = await service.get_user_subscriptions(current_user["id"])
        subscriptions = result.get("data", [])
        active_subs = [s for s in subscriptions if s.get("status") in {"active", "trialing", "past_due"}]

        if not active_subs:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No active subscription found",
            )

        subscription_id = active_subs[0]["id"]

    # Verify the subscription belongs to the current user
    subscription = await service.get_subscription(subscription_id)

    if (
        subscription["user_id"] != current_user["id"]
        or str(subscription.get("tenant_id")) != str(current_user.get("tenant_id"))
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to cancel this subscription",
        )

    return await service.cancel_subscription(subscription_id)
