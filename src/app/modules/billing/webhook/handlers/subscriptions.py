"""Subscription webhook event handlers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import stripe
from sqlalchemy import select

from src.app.modules.billing import webhook as _pkg
from src.app.modules.billing.lifecycle import (
    extract_period_timestamps,
    is_valid_transition,
    map_stripe_status,
)
from src.app.modules.billing.schemas import (
    SubscriptionStatus,
    UserSubscriptionUpdate,
)


async def handle_subscription_updated(event: stripe.Event) -> None:
    """Handle customer.subscription.updated event.

    Updates subscription status and period dates.
    T078: Subscription status update logic
    """
    subscription = event["data"]["object"]
    stripe_subscription_id = subscription["id"]
    _pkg.logger.info("Processing subscription.updated:", stripe_subscription_id=stripe_subscription_id)

    # Find subscription by stripe_subscription_id using bypass context.
    # P0-1 FIX: the bypass context is only used for the raw row lookup (RLS
    # bypass, no tenant ContextVar). Tenant-scoped CRUD must run inside
    # job_tenant_context — running TenantAwareCRUD on the bypass session
    # raised TenantContextError on every event (the webhook route is
    # tenant-exempt, so no tenant context exists), meaning this handler's
    # sync never applied and Stripe retried for ~3 days.
    tenant_id = None
    subscription_id = None
    user_id = None

    async with _pkg.job_bypass_context("Webhook subscription lookup") as db:
        result = await db.execute(
            select(_pkg.crud_user_subscriptions.model)
            .where(_pkg.crud_user_subscriptions.model.stripe_subscription_id == stripe_subscription_id)
            .with_for_update()
        )
        db_subscription = result.scalar_one_or_none()

        if not db_subscription:
            _pkg.logger.warning(
                f"Subscription not found for stripe_subscription_id: {stripe_subscription_id}. "
                f"Raising error to trigger Stripe retry."
            )
            raise ValueError(f"Subscription not found for {stripe_subscription_id}")

        tenant_id = db_subscription.tenant_id
        subscription_id = db_subscription.id
        user_id = db_subscription.user_id

    # Map Stripe status to our status
    new_status: SubscriptionStatus = map_stripe_status(subscription["status"])  # type: ignore

    # P1-4 FIX (event payload): current_period_* moved to SubscriptionItem in
    # Stripe API 2025-03-31.basil+; read from items.data[0] with top-level fallback.
    period_start, period_end = extract_period_timestamps(subscription)
    if period_start is None or period_end is None:
        raise ValueError(f"Missing critical timestamps in Stripe subscription {stripe_subscription_id}")

    # Update subscription
    update_data = UserSubscriptionUpdate(
        status=new_status,
        current_period_start=datetime.fromtimestamp(period_start, UTC),
        current_period_end=datetime.fromtimestamp(period_end, UTC),
        cancel_at_period_end=subscription.get("cancel_at_period_end", False),
    )
    if subscription.get("canceled_at"):
        update_data.canceled_at = datetime.fromtimestamp(subscription["canceled_at"], UTC)

    async with _pkg.job_tenant_context(tenant_id) as db:
        # ATOMIC LOCK: Re-verify and lock record within tenant context to prevent TOCTOU
        lock_result = await db.execute(
            select(_pkg.crud_user_subscriptions.model)
            .where(_pkg.crud_user_subscriptions.model.id == subscription_id)
            .with_for_update()
        )
        db_subscription_locked = lock_result.scalar_one_or_none()

        if not db_subscription_locked:
            _pkg.logger.warning(
                "Subscription deleted in race condition gap, skipping update",
                stripe_subscription_id=stripe_subscription_id,
            )
            return

        # S2-02 FIX: Validate lifecycle transition to prevent resurrecting canceled/expired subscriptions
        effective_status = new_status
        current_status = getattr(db_subscription_locked, "status", None)
        if isinstance(current_status, str):
            if current_status != new_status and not is_valid_transition(current_status, new_status):
                _pkg.logger.info(
                    "Skipping invalid subscription transition (out-of-order webhook)",
                    stripe_subscription_id=stripe_subscription_id,
                    current_status=current_status,
                    target_status=str(new_status),
                )
                effective_status = cast(SubscriptionStatus, current_status)
                update_data.status = effective_status

        await _pkg.crud_user_subscriptions.update(
            db=db,
            object=update_data,
            id=subscription_id,
        )

        # M-04: Explicitly downgrade user to free tier if canceled/expired
        if new_status in ("canceled", "expired"):
            from src.app.modules.users.service import UserService

            await UserService(db).downgrade_to_free_tier(user_id)
            _pkg.logger.info(
                "Downgraded user to free tier due to subscription cancellation",
                user_id=user_id,
                tenant_id=tenant_id,
                reason=new_status,
            )
        await db.commit()

    _pkg.logger.info(
        "Updated subscription to status", stripe_subscription_id=stripe_subscription_id, new_status=new_status
    )


async def handle_subscription_deleted(event: stripe.Event) -> None:
    """Handle customer.subscription.deleted event.

    Marks subscription as canceled/expired.
    """
    subscription = event["data"]["object"]
    stripe_subscription_id = subscription["id"]
    _pkg.logger.info("Processing subscription.deleted:", stripe_subscription_id=stripe_subscription_id)

    # Find subscription by stripe_subscription_id using bypass context
    tenant_id = None
    subscription_id = None

    async with _pkg.job_bypass_context("Webhook subscription lookup") as db:
        result = await db.execute(
            select(_pkg.crud_user_subscriptions.model)
            .where(_pkg.crud_user_subscriptions.model.stripe_subscription_id == stripe_subscription_id)
            .with_for_update()
        )
        db_subscription = result.scalar_one_or_none()

        if not db_subscription:
            _pkg.logger.warning(
                f"Subscription not found for stripe_subscription_id: {stripe_subscription_id}. "
                f"Raising error to trigger Stripe retry."
            )
            raise ValueError(f"Subscription not found for {stripe_subscription_id}")

        tenant_id = db_subscription.tenant_id
        subscription_id = db_subscription.id

    canceled_at_ts = subscription.get("canceled_at")
    # Update subscription to canceled/expired
    update_data = UserSubscriptionUpdate(
        status="canceled",
        canceled_at=datetime.fromtimestamp(canceled_at_ts, UTC) if canceled_at_ts else datetime.now(UTC),
    )

    async with _pkg.job_tenant_context(tenant_id) as db:
        # ATOMIC LOCK: Re-verify and lock record within tenant context to prevent TOCTOU
        lock_result = await db.execute(
            select(_pkg.crud_user_subscriptions.model)
            .where(_pkg.crud_user_subscriptions.model.id == subscription_id)
            .with_for_update()
        )
        db_subscription_locked = lock_result.scalar_one_or_none()

        if not db_subscription_locked:
            _pkg.logger.warning(
                "Subscription deleted in race condition gap, skipping update",
                stripe_subscription_id=stripe_subscription_id,
            )
            return

        await _pkg.crud_user_subscriptions.update(
            db=db,
            object=update_data,
            id=subscription_id,
        )

        # M-04: Explicitly downgrade user to free tier
        from src.app.modules.users.service import UserService

        await UserService(db).downgrade_to_free_tier(db_subscription.user_id)

        await db.commit()

    _pkg.logger.info("Marked subscription as canceled", stripe_subscription_id=stripe_subscription_id)


__all__ = ["handle_subscription_deleted", "handle_subscription_updated"]
