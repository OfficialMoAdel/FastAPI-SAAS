"""Subscription webhook event handlers."""

from __future__ import annotations

from datetime import UTC, datetime

import stripe

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
from src.app.modules.billing.webhook.locks import (
    find_subscription_by_stripe_id,
    lock_subscription_by_id,
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
    # P2-#12: the lock dance lives in webhook.locks — the with_for_update
    # invariant is impossible to skip.
    subscription_ref = await find_subscription_by_stripe_id(stripe_subscription_id)
    tenant_id = subscription_ref.tenant_id
    subscription_id = subscription_ref.subscription_id
    user_id = subscription_ref.user_id

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
        # P2-#12: re-lock lives in webhook.locks.
        db_subscription_locked = await lock_subscription_by_id(db, subscription_id)

        if not db_subscription_locked:
            _pkg.logger.warning(
                "Subscription deleted in race condition gap, skipping update",
                stripe_subscription_id=stripe_subscription_id,
            )
            return

        # S2-02 FIX: Validate lifecycle transition to prevent resurrecting canceled/expired subscriptions.
        # R2-1: early-return on invalid transition (mirrors handle_subscription_deleted).
        # The out-of-order event's period timestamps are stale too, so nothing
        # is written — not just the status. Downgrade below keys off the
        # event's new_status only when the transition was accepted.
        current_status = getattr(db_subscription_locked, "status", None)
        if (
            isinstance(current_status, str)
            and current_status != new_status
            and not is_valid_transition(current_status, new_status)
        ):
            _pkg.logger.info(
                "Skipping invalid subscription transition (out-of-order webhook)",
                stripe_subscription_id=stripe_subscription_id,
                current_status=current_status,
                target_status=str(new_status),
            )
            return

        await _pkg.crud_user_subscriptions.update(
            db=db,
            object=update_data,
            id=subscription_id,
        )

        # M-04: Explicitly downgrade user to free tier if canceled/expired.
        # R2-1: safe to key off new_status — rejected transitions returned
        # above, so reaching here means the transition was accepted.
        if new_status in ("canceled", "expired"):
            from src.app.modules.users import UserService

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

    # Find subscription by stripe_subscription_id using bypass context.
    # P2-#12: the lock dance lives in webhook.locks (user_id is captured
    # inside the bypass session there — reading it after exit risked
    # DetachedInstanceError -> 500 -> Stripe retry storm).
    subscription_ref = await find_subscription_by_stripe_id(stripe_subscription_id)
    tenant_id = subscription_ref.tenant_id
    subscription_id = subscription_ref.subscription_id
    user_id = subscription_ref.user_id

    canceled_at_ts = subscription.get("canceled_at")
    # Update subscription to canceled/expired
    update_data = UserSubscriptionUpdate(
        status="canceled",
        canceled_at=datetime.fromtimestamp(canceled_at_ts, UTC) if canceled_at_ts else datetime.now(UTC),
    )

    async with _pkg.job_tenant_context(tenant_id) as db:
        # ATOMIC LOCK: Re-verify and lock record within tenant context to prevent TOCTOU
        # P2-#12: re-lock lives in webhook.locks.
        db_subscription_locked = await lock_subscription_by_id(db, subscription_id)

        if not db_subscription_locked:
            _pkg.logger.warning(
                "Subscription deleted in race condition gap, skipping update",
                stripe_subscription_id=stripe_subscription_id,
            )
            return

        # P2-3: guard out-of-order delivery (mirrors the S2-02 guard in
        # handle_subscription_updated) — a delayed subscription.deleted must
        # not rewrite a terminal EXPIRED subscription back to canceled, nor
        # re-downgrade the user.
        current_status = getattr(db_subscription_locked, "status", None)
        if (
            isinstance(current_status, str)
            and current_status != "canceled"
            and not is_valid_transition(current_status, "canceled")
        ):
            _pkg.logger.info(
                "Skipping invalid subscription transition (out-of-order webhook)",
                stripe_subscription_id=stripe_subscription_id,
                current_status=current_status,
                target_status="canceled",
            )
            return

        await _pkg.crud_user_subscriptions.update(
            db=db,
            object=update_data,
            id=subscription_id,
        )

        # M-04: Explicitly downgrade user to free tier
        from src.app.modules.users import UserService

        await UserService(db).downgrade_to_free_tier(user_id)

        await db.commit()

    _pkg.logger.info("Marked subscription as canceled", stripe_subscription_id=stripe_subscription_id)


__all__ = ["handle_subscription_deleted", "handle_subscription_updated"]
