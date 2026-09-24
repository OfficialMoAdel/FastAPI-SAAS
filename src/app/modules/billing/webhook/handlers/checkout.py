"""Checkout webhook event handler."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

import stripe
from sqlalchemy.exc import IntegrityError

from src.app.modules.billing import webhook as _pkg
from src.app.modules.billing.lifecycle import extract_period_timestamps, map_stripe_status
from src.app.modules.billing.schemas import (
    SubscriptionStatus,
    UserSubscriptionCreateInternal,
)
from src.app.modules.billing.subscription_service import (
    SubscriptionPlanService,
    fetch_stripe_subscription,
    resolve_tenant_stripe_api_key,
)
from src.app.modules.users import get_user_tenant_id


async def handle_checkout_session_completed(event: stripe.Event) -> None:
    """Handle checkout.session.completed event.

    Creates a new UserSubscription record when checkout is completed.
    T074: checkout.session.completed webhook handler
    """
    session = event["data"]["object"]
    _pkg.logger.info("Processing checkout.session.completed:", val=session["id"])

    # Extract metadata
    metadata = session.get("metadata", {})
    tenant_id_str = metadata.get("tenant_id")
    user_id_str = metadata.get("user_id")
    plan_id_str = metadata.get("plan_id")

    # Parse tenant_id safely
    tenant_id: UUID | None = None
    if tenant_id_str:
        try:
            tenant_id = UUID(tenant_id_str)
        except ValueError:
            _pkg.logger.warning("Invalid UUID format for tenant_id:", tenant_id_str=tenant_id_str)

    user_id = int(user_id_str) if user_id_str and user_id_str.isdigit() else None
    plan_id = int(plan_id_str) if plan_id_str and plan_id_str.isdigit() else None

    if tenant_id is None or user_id is None or plan_id is None:
        _pkg.logger.error(
            "Missing or invalid metadata in checkout session",
            session_id=session["id"],
            tenant_id=str(tenant_id) if tenant_id else None,
            user_id=user_id,
            plan_id=plan_id,
        )
        # P1 FIX (code-review): deterministic failure — Stripe retry can
        # never succeed (metadata is immutable). Return success-with-log so
        # dispatch acks and does not retry-storm for days. Only transient
        # errors (subscription-not-found, Stripe transport) raise.
        return

    stripe_subscription_id = session.get("subscription")

    # Retrieve subscription details from Stripe via the gateway seam.
    # Key resolution needs a DB session but the Stripe HTTP call must NOT
    # hold one: resolve inside a short bypass context (the webhook path has
    # no tenant middleware context), then fetch outside it so a slow Stripe
    # never pins a pooled DB connection.
    if stripe_subscription_id:
        try:
            async with _pkg.job_bypass_context("Checkout tenant Stripe key resolve") as gw_db:
                api_key = await resolve_tenant_stripe_api_key(gw_db, tenant_id)
            stripe_sub = await fetch_stripe_subscription(api_key, stripe_subscription_id)
            # P1-4 FIX: current_period_* moved from Subscription to
            # SubscriptionItem in Stripe API 2025-03-31.basil (the pinned SDK
            # sends 2025-12-15.clover, where the top-level fields are absent).
            # Read from items.data[0] with top-level fallback for older versions.
            period_start, period_end = extract_period_timestamps(stripe_sub)
            if period_start is None or period_end is None:
                # P1 FIX: deterministic payload shape — retry cannot heal a
                # version-skewed payload; ack to avoid poison retry storm.
                _pkg.logger.error(
                    "Missing critical timestamps in Stripe subscription — acking",
                    stripe_subscription_id=stripe_subscription_id,
                )
                return

            current_period_start = datetime.fromtimestamp(period_start, UTC)
            current_period_end = datetime.fromtimestamp(period_end, UTC)

            # Determine billing interval from Stripe subscription
            interval = "month"
            if stripe_sub.get("items") and stripe_sub["items"].get("data"):
                price = stripe_sub["items"]["data"][0].get("price")
                if price and price.get("recurring") and price["recurring"].get("interval"):
                    interval = price["recurring"]["interval"]
            billing_interval: Literal["monthly", "yearly"] = "yearly" if interval == "year" else "monthly"

            # B6 residual FIX: validate the plan actually belongs to this
            # tenant before minting the subscription. Previously metadata
            # plan_id was trusted blindly — a forged checkout could attach
            # another tenant's plan id and mint arbitrary subscriptions.
            # I10: ownership lookup via SubscriptionPlanService, not raw crud.
            async with _pkg.job_bypass_context("Checkout plan ownership check") as plan_db:
                plan_tenant = await SubscriptionPlanService(plan_db).get_plan_tenant_id(plan_id)
            if plan_tenant is None or str(plan_tenant) != str(tenant_id):
                _pkg.logger.error(
                    "Checkout plan/tenant ownership mismatch — rejecting subscription creation",
                    session_id=session["id"],
                    plan_id=plan_id,
                    claimed_tenant_id=str(tenant_id),
                    plan_tenant_id=str(plan_tenant),
                )
                # P1 FIX: forged/cross-tenant metadata never heals on retry.
                return
            # B2-06 FIX: validate that the user belongs to this tenant before minting subscription
            # I10: membership check via the users facade, not users.models.
            async with _pkg.job_bypass_context("Checkout user/tenant membership check") as user_db:
                user_tenant = await get_user_tenant_id(user_db, user_id)
            if str(user_tenant) != str(tenant_id):
                _pkg.logger.error(
                    "Checkout user/tenant membership mismatch — rejecting subscription creation",
                    session_id=session["id"],
                    user_id=user_id,
                    claimed_tenant_id=str(tenant_id),
                    actual_user_tenant=str(user_tenant),
                )
                # P1 FIX: membership mismatch never heals on retry.
                return

            # Create user subscription with tenant_id in the object
            subscription_data = UserSubscriptionCreateInternal(
                tenant_id=tenant_id,
                user_id=user_id,
                subscription_plan_id=plan_id,
                stripe_subscription_id=stripe_subscription_id,
                # P2-3: map the real Stripe status instead of hardcoding
                # active — checkout can yield trialing/past_due subscriptions.
                status=cast(SubscriptionStatus, map_stripe_status(stripe_sub.get("status", "active"))),
                billing_interval=billing_interval,
                current_period_start=current_period_start,
                current_period_end=current_period_end,
                cancel_at_period_end=False,
            )

            async with _pkg.job_tenant_context(tenant_id) as db:
                # ATOMIC UPSERT: Check exists first, then create or update
                # Race condition handled: IntegrityError on create triggers fallback update
                existing = await _pkg.crud_user_subscriptions.exists(
                    db=db,
                    stripe_subscription_id=stripe_subscription_id,
                )

                if existing:
                    # Record exists - update directly
                    _pkg.logger.info(
                        "Subscription exists, updating | stripe_sub_id=%s | user=%s",
                        stripe_subscription_id,
                        user_id,
                    )

                    # Build update dict using stripe event data
                    # P1-4: items.data[0] first, top-level fallback (see extract_period_timestamps)
                    upd_start, upd_end = extract_period_timestamps(stripe_sub)
                    update_data: dict[str, Any] = {
                        "status": map_stripe_status(stripe_sub.get("status", "")),
                        "current_period_start": datetime.fromtimestamp(
                            upd_start or int(datetime.now(UTC).timestamp()), UTC
                        ),
                        "current_period_end": datetime.fromtimestamp(
                            upd_end or int(datetime.now(UTC).timestamp()), UTC
                        ),
                        "cancel_at_period_end": stripe_sub.get("cancel_at_period_end", False),
                    }
                    canceled_at = stripe_sub.get("canceled_at")
                    if canceled_at:
                        update_data["canceled_at"] = datetime.fromtimestamp(canceled_at, UTC)

                    await _pkg.crud_user_subscriptions.update(
                        db=db,
                        stripe_subscription_id=stripe_subscription_id,
                        object=update_data,
                        commit=False,
                    )
                    await db.commit()
                else:
                    # Record doesn't exist - try to create, handle race condition
                    try:
                        await _pkg.crud_user_subscriptions.create(
                            db=db,
                            object=subscription_data,
                            commit=False,
                        )
                        _pkg.logger.info(
                            "Created subscription | stripe_sub_id=%s | user=%s | tenant=%s",
                            stripe_subscription_id,
                            user_id,
                            tenant_id,
                        )
                        await db.commit()
                    except IntegrityError:
                        # Race condition: another request created it first
                        await db.rollback()
                        _pkg.logger.info(
                            "Race condition: subscription created by concurrent request | stripe_sub_id=%s",
                            stripe_subscription_id,
                        )

                        # Re-fetch and update
                        raw_status = stripe_sub.get("status", "")
                        mapped_status = map_stripe_status(raw_status)

                        # P1-4: items.data[0] first, top-level fallback (see extract_period_timestamps)
                        race_start, race_end = extract_period_timestamps(stripe_sub)
                        race_update_data: dict[str, Any] = {
                            "status": mapped_status,
                            "current_period_start": datetime.fromtimestamp(
                                race_start or int(datetime.now(UTC).timestamp()), UTC
                            ),
                            "current_period_end": datetime.fromtimestamp(
                                race_end or int(datetime.now(UTC).timestamp()), UTC
                            ),
                            "cancel_at_period_end": stripe_sub.get("cancel_at_period_end", False),
                        }
                        canceled_at = stripe_sub.get("canceled_at")
                        if canceled_at:
                            race_update_data["canceled_at"] = datetime.fromtimestamp(canceled_at, UTC)

                        await _pkg.crud_user_subscriptions.update(
                            db=db,
                            stripe_subscription_id=stripe_subscription_id,
                            object=race_update_data,
                            commit=False,
                        )
                        await db.commit()

            _pkg.logger.info("Created subscription for user in tenant", user_id=user_id, tenant_id=tenant_id)

        except stripe.StripeError as e:
            _pkg.logger.error("Stripe API error retrieving subscription", error=e)
            raise
    else:
        _pkg.logger.warning("No subscription ID in checkout session:", val=session["id"])


__all__ = ["handle_checkout_session_completed"]
