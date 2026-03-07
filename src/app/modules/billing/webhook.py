import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

import stripe
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.app.core.utils.cache import client as redis_client
from src.app.core.worker.rls_context import job_bypass_context, job_tenant_context
from src.app.modules.billing.crud import crud_billing_records, crud_user_subscriptions
from src.app.modules.billing.schemas import (
    BillingRecordCreateInternal,
    SubscriptionStatus,
    UserSubscriptionCreateInternal,
    UserSubscriptionUpdate,
)

logger = logging.getLogger(__name__)

# Redis key prefix for webhook idempotency tracking
# Stripe events have at-least-once delivery guarantee, so we must track processed events
_WEBHOOK_EVENT_KEY_PREFIX = "stripe:webhook:processed:"
_WEBHOOK_EVENT_TTL_SECONDS = 86400 * 7  # 7 days retention ( Stripe retries for 3 days)


async def _try_acquire_event_lock(event_id: str) -> bool:
    """Atomically try to acquire processing lock for a webhook event.

    Uses Redis SET with NX (set-if-not-exists) for atomic idempotency.
    This eliminates the TOCTOU race condition between check and mark operations.

    Args:
        event_id: The Stripe event ID (e.g., "evt_1234567890")

    Returns:
        True if lock acquired (event NOT previously processed), False if already processed
    """
    if redis_client is None:
        # Redis not available - we can't guarantee idempotency
        # Log warning and proceed (better to risk duplicate than lose event)
        logger.warning("Redis unavailable for webhook idempotency check")
        return True  # Allow processing

    try:
        key = f"{_WEBHOOK_EVENT_KEY_PREFIX}{event_id}"
        # SET key value NX EX ttl - atomic set-if-not-exists with expiration
        # Returns True if set (lock acquired), False if key already exists
        result = await redis_client.set(key, "1", nx=True, ex=_WEBHOOK_EVENT_TTL_SECONDS)
        return result is not None  # True = lock acquired, False = already processed
    except Exception as e:
        logger.error(f"Failed to acquire webhook event lock: {e}")
        # Fail open - allow processing to avoid losing events
        return True


def verify_webhook_signature(payload: bytes, sig_header: str, secret: str) -> stripe.Event:
    """
    Verify the Stripe webhook signature and construct the event.

    Args:
        payload: Raw request body bytes
        sig_header: value of Stripe-Signature header
        secret: Webhook signing secret

    Returns:
        stripe.Event object

    Raises:
        HTTPException: If signature verification fails
    """
    try:
        # Constructing event is CPU bound, but typically fast enough.
        # Ideally this would be run in a thread executor if payload is huge.
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
        return cast(stripe.Event, event)
    except ValueError as e:
        # Invalid payload
        logger.error(f"Invalid payload: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        logger.error(f"Invalid signature: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature")


async def handle_webhook_event(event: stripe.Event, background_tasks: Any = None) -> dict[str, str]:
    """
    Route and handle specific Stripe webhook events.

    Implements idempotency to prevent duplicate processing of the same event.
    Stripe guarantees at-least-once delivery, so the same event may be delivered
    multiple times. We track processed event IDs to ensure each event is handled
    exactly once.

    Args:
        event: The Stripe event object
        background_tasks: FastAPI BackgroundTasks object (optional)

    Returns:
        Status dict
    """
    event_type = event["type"]
    event_id = event["id"]
    logger.info(f"Received Stripe webhook event: {event_type} (ID: {event_id})")

    # IDEMPOTENCY CHECK: Atomically acquire processing lock
    # Uses SET NX for atomic check-and-mark, eliminating TOCTOU race
    if not await _try_acquire_event_lock(event_id):
        logger.info(f"Event {event_id} already processed, skipping")
        return {"status": "already_processed"}

    try:
        # Handlers manage their own DB sessions and RLS context
        if event_type == "checkout.session.completed":
            await handle_checkout_session_completed(event)
        elif event_type == "customer.subscription.updated":
            await handle_subscription_updated(event)
        elif event_type == "customer.subscription.deleted":
            await handle_subscription_deleted(event)
        elif event_type == "invoice.payment_succeeded":
            await handle_invoice_payment_succeeded(event)
        elif event_type == "invoice.payment_failed":
            await handle_invoice_payment_failed(event)
        elif event_type == "charge.refunded":
            await handle_charge_refunded(event)
        else:
            logger.info(f"Unhandled event type: {event_type}")
            # Lock already acquired, event is marked as processed
            return {"status": "unhandled"}

        # Lock was already acquired in _try_acquire_event_lock
        # No need to mark again - atomic SET NX handles this

    except Exception as e:
        logger.error(f"Error handling webhook event {event_type} (ID: {event_id}): {e}")
        # Do NOT mark as processed on failure - allow Stripe to retry
        # CRITICAL FIX: We MUST delete the Redis lock we just acquired, otherwise
        # Stripe's retries will be false-positively rejected as "already_processed" for 7 days!
        if redis_client:
            try:
                await redis_client.delete(f"{_WEBHOOK_EVENT_KEY_PREFIX}{event_id}")
            except Exception as redis_e:
                logger.error(f"Failed to release webhook event lock: {redis_e}")
        raise

    return {"status": "success"}


async def handle_checkout_session_completed(event: stripe.Event) -> None:
    """
    Handle checkout.session.completed event.

    Creates a new UserSubscription record when checkout is completed.

    T074: checkout.session.completed webhook handler
    """
    session = event["data"]["object"]
    logger.info(f"Processing checkout.session.completed: {session['id']}")

    # Extract metadata
    metadata = session.get("metadata", {})
    tenant_id_str = metadata.get("tenant_id", "0")
    tenant_id = UUID(tenant_id_str) if tenant_id_str and tenant_id_str != "0" else None
    user_id = int(metadata.get("user_id", 0))
    plan_id = int(metadata.get("plan_id", 0))

    if not all([tenant_id, user_id, plan_id]):
        logger.error(
            "Missing metadata in checkout session: tenant_id=%s, user_id=%s, plan_id=%s",
            str(tenant_id) if tenant_id else None,
            user_id,
            plan_id,
        )
        return

    stripe_subscription_id = session.get("subscription")
    # stripe_customer_id = session.get("customer")  # Unused

    # Retrieve subscription details from Stripe
    if stripe_subscription_id:
        try:
            stripe_sub = await asyncio.to_thread(stripe.Subscription.retrieve, stripe_subscription_id)
            # Access Stripe subscription period timestamps with .get or default to 0
            current_period_start = datetime.fromtimestamp(stripe_sub.get("current_period_start", 0), UTC)
            current_period_end = datetime.fromtimestamp(stripe_sub.get("current_period_end", 0), UTC)

            # Determine billing interval from Stripe subscription
            interval = "month"
            if stripe_sub.get("items") and stripe_sub["items"].get("data"):
                price = stripe_sub["items"]["data"][0].get("price")
                if price and price.get("recurring") and price["recurring"].get("interval"):
                    interval = price["recurring"]["interval"]
            billing_interval: Literal["monthly", "yearly"] = "yearly" if interval == "year" else "monthly"

            # Create user subscription with tenant_id in the object
            subscription_data = UserSubscriptionCreateInternal(
                tenant_id=tenant_id,  # type: ignore[arg-type]
                user_id=user_id,
                subscription_plan_id=plan_id,
                stripe_subscription_id=stripe_subscription_id,
                status="active",
                billing_interval=billing_interval,
                current_period_start=current_period_start,
                current_period_end=current_period_end,
                cancel_at_period_end=False,
            )

            async with job_tenant_context(tenant_id) as db:  # type: ignore[arg-type]
                # ATOMIC UPSERT: Check exists first, then create or update
                # Race condition handled: IntegrityError on create triggers fallback update
                existing = await crud_user_subscriptions.exists(
                    db=db,
                    stripe_subscription_id=stripe_subscription_id,
                )

                if existing:
                    # Record exists - update directly
                    logger.info(
                        "Subscription exists, updating | stripe_sub_id=%s | user=%s",
                        stripe_subscription_id,
                        user_id,
                    )

                    # Build update dict using stripe event data
                    # SECURITY: Ensure all timestamps are timezone-aware (UTC)
                    update_data: dict[str, Any] = {
                        "status": stripe_sub.get("status"),
                        "current_period_start": datetime.fromtimestamp(stripe_sub.get("current_period_start", 0), UTC),
                        "current_period_end": datetime.fromtimestamp(stripe_sub.get("current_period_end", 0), UTC),
                        "cancel_at_period_end": stripe_sub.get("cancel_at_period_end", False),
                    }
                    canceled_at = stripe_sub.get("canceled_at")
                    if canceled_at:
                        update_data["canceled_at"] = datetime.fromtimestamp(canceled_at, UTC)

                    await crud_user_subscriptions.update(
                        db=db,
                        stripe_subscription_id=stripe_subscription_id,
                        object=update_data,
                    )
                    await db.commit()
                else:
                    # Record doesn't exist - try to create, handle race condition
                    try:
                        await crud_user_subscriptions.create(
                            db=db,
                            object=subscription_data,
                        )
                        logger.info(
                            "Created subscription | stripe_sub_id=%s | user=%s | tenant=%s",
                            stripe_subscription_id,
                            user_id,
                            tenant_id,
                        )
                        await db.commit()
                    except IntegrityError:
                        # Race condition: another request created it first
                        await db.rollback()
                        logger.info(
                            "Race condition: subscription created by concurrent request | stripe_sub_id=%s",
                            stripe_subscription_id,
                        )

                        # Re-fetch and update
                        update_data: dict[str, Any] = {
                            "status": stripe_sub.get("status"),
                            "current_period_start": datetime.fromtimestamp(stripe_sub.get("current_period_start", 0), UTC),
                            "current_period_end": datetime.fromtimestamp(stripe_sub.get("current_period_end", 0), UTC),
                            "cancel_at_period_end": stripe_sub.get("cancel_at_period_end", False),
                        }
                        canceled_at = stripe_sub.get("canceled_at")
                        if canceled_at:
                            update_data["canceled_at"] = datetime.fromtimestamp(canceled_at, UTC)

                        await crud_user_subscriptions.update(
                            db=db,
                            stripe_subscription_id=stripe_subscription_id,
                            object=update_data,
                        )
                        await db.commit()

            logger.info(f"Created subscription for user {user_id} in tenant {tenant_id}")

        except stripe.error.StripeError as e:
            logger.error(f"Stripe API error retrieving subscription: {e}")
            raise
    else:
        logger.warning(f"No subscription ID in checkout session: {session['id']}")


async def handle_subscription_updated(event: stripe.Event) -> None:
    """
    Handle customer.subscription.updated event.

    Updates subscription status and period dates.

    T078: Subscription status update logic
    """
    subscription = event["data"]["object"]
    stripe_subscription_id = subscription["id"]
    logger.info(f"Processing subscription.updated: {stripe_subscription_id}")

    # Find subscription by stripe_subscription_id using bypass context
    tenant_id = None
    subscription_id = None

    async with job_bypass_context("Webhook subscription lookup") as db:
        result = await db.execute(
            select(crud_user_subscriptions.model).where(
                crud_user_subscriptions.model.stripe_subscription_id == stripe_subscription_id
            )
        )
        db_subscription = result.scalar_one_or_none()

        if not db_subscription:
            logger.warning(
                f"Subscription not found for stripe_subscription_id: {stripe_subscription_id}. "
                f"Raising error to trigger Stripe retry."
            )
            raise ValueError(f"Subscription not found for {stripe_subscription_id}")

        tenant_id = db_subscription.tenant_id
        subscription_id = db_subscription.id

    # Map Stripe status to our status
    status_map: dict[str, SubscriptionStatus] = {
        "active": "active",
        "past_due": "past_due",
        "canceled": "canceled",
        "unpaid": "past_due",
        "incomplete": "past_due",
        "incomplete_expired": "expired",
        "trialing": "trialing",
    }
    new_status: SubscriptionStatus = status_map.get(subscription["status"], "active")

    # Update subscription
    update_data = UserSubscriptionUpdate(
        status=new_status,
        current_period_start=datetime.fromtimestamp(subscription["current_period_start"], UTC),
        current_period_end=datetime.fromtimestamp(subscription["current_period_end"], UTC),
        cancel_at_period_end=subscription.get("cancel_at_period_end", False),
    )

    if subscription.get("canceled_at"):
        update_data.canceled_at = datetime.fromtimestamp(subscription["canceled_at"], UTC)

    async with job_tenant_context(tenant_id) as db:
        await crud_user_subscriptions.update(
            db=db,
            object=update_data,
            id=subscription_id,
        )

        # M-04: Explicitly downgrade tenant to free tier if canceled/expired
        if new_status in ("canceled", "expired"):
            from src.app.modules.billing.crud import crud_subscription_plans
            from src.app.modules.tenants.crud import crud_tenants

            # Find the free plan
            free_plan_stmt = select(crud_subscription_plans.model).where(
                crud_subscription_plans.model.name.ilike("%free%")
            ).limit(1)
            free_plan_result = await db.execute(free_plan_stmt)
            free_plan = free_plan_result.scalar_one_or_none()

            if free_plan:
                await crud_tenants.update(
                    db=db,
                    id=tenant_id,
                    object={"tier_id": free_plan.id}
                )
                logger.info(
                    f"Downgraded tenant {tenant_id} to free tier ({free_plan.id}) "
                    f"due to subscription {new_status}"
                )

        await db.commit()

    logger.info(f"Updated subscription {stripe_subscription_id} to status {new_status}")


async def handle_subscription_deleted(event: stripe.Event) -> None:
    """
    Handle customer.subscription.deleted event.

    Marks subscription as canceled/expired.
    """
    subscription = event["data"]["object"]
    stripe_subscription_id = subscription["id"]
    logger.info(f"Processing subscription.deleted: {stripe_subscription_id}")

    # Find subscription by stripe_subscription_id using bypass context
    tenant_id = None
    subscription_id = None

    async with job_bypass_context("Webhook subscription lookup") as db:
        result = await db.execute(
            select(crud_user_subscriptions.model).where(
                crud_user_subscriptions.model.stripe_subscription_id == stripe_subscription_id
            )
        )
        db_subscription = result.scalar_one_or_none()

        if not db_subscription:
            logger.warning(
                f"Subscription not found for stripe_subscription_id: {stripe_subscription_id}. "
                f"Raising error to trigger Stripe retry."
            )
            raise ValueError(f"Subscription not found for {stripe_subscription_id}")

        tenant_id = db_subscription.tenant_id
        subscription_id = db_subscription.id

    # Update subscription to canceled/expired
    update_data = UserSubscriptionUpdate(
        status="canceled",
        canceled_at=datetime.now(UTC),
    )

    async with job_tenant_context(tenant_id) as db:
        await crud_user_subscriptions.update(
            db=db,
            object=update_data,
            id=subscription_id,
        )

        # M-04: Explicitly downgrade tenant to free tier
        from src.app.modules.billing.crud import crud_subscription_plans
        from src.app.modules.tenants.crud import crud_tenants

        # Find the free plan
        free_plan_stmt = select(crud_subscription_plans.model).where(
            crud_subscription_plans.model.name.ilike("%free%")
        ).limit(1)
        free_plan_result = await db.execute(free_plan_stmt)
        free_plan = free_plan_result.scalar_one_or_none()

        if free_plan:
            await crud_tenants.update(
                db=db,
                id=tenant_id,
                object={"tier_id": free_plan.id}
            )
            logger.info(f"Downgraded tenant {tenant_id} to free tier ({free_plan.id})")

        await db.commit()

    logger.info(f"Marked subscription {stripe_subscription_id} as canceled")


async def handle_invoice_payment_succeeded(event: stripe.Event) -> None:
    """
    Handle invoice.payment_succeeded event.

    Creates a billing record with succeeded status.

    T075: invoice.payment_succeeded webhook handler
    """
    invoice = event["data"]["object"]
    logger.info(f"Processing invoice.payment_succeeded: {invoice['id']}")

    stripe_subscription_id = invoice.get("subscription")
    if not stripe_subscription_id:
        logger.info(f"Invoice {invoice['id']} is not for a subscription, skipping")
        return

    # Find subscription by stripe_subscription_id using bypass context
    tenant_id = None
    user_id = None
    subscription_id = None

    async with job_bypass_context("Webhook subscription lookup") as db:
        result = await db.execute(
            select(crud_user_subscriptions.model).where(
                crud_user_subscriptions.model.stripe_subscription_id == stripe_subscription_id
            )
        )
        db_subscription = result.scalar_one_or_none()

        if not db_subscription:
            logger.warning(
                f"Subscription not found for stripe_subscription_id: {stripe_subscription_id}. "
                f"Raising error to trigger Stripe retry."
            )
            raise ValueError(f"Subscription not found for {stripe_subscription_id}")

        tenant_id = db_subscription.tenant_id
        user_id = db_subscription.user_id
        subscription_id = db_subscription.id

    # Create billing record with tenant_id in the object
    billing_record = BillingRecordCreateInternal(
        tenant_id=tenant_id,
        user_id=user_id,
        user_subscription_id=subscription_id,
        stripe_payment_intent_id=invoice.get("payment_intent"),
        stripe_invoice_id=invoice["id"],
        amount_cents=invoice["amount_paid"],
        currency=invoice["currency"],
        status="succeeded",
        description="Invoice payment for subscription",
    )

    async with job_tenant_context(tenant_id) as db:
        await crud_billing_records.create(
            db=db,
            object=billing_record,
        )
        await db.commit()

    logger.info(f"Created succeeded billing record for invoice {invoice['id']}")


async def handle_invoice_payment_failed(event: stripe.Event) -> None:
    """
    Handle invoice.payment_failed event.

    Creates a billing record with failed status and updates subscription to past_due.

    T076: invoice.payment_failed webhook handler
    """
    invoice = event["data"]["object"]
    logger.info(f"Processing invoice.payment_failed: {invoice['id']}")

    stripe_subscription_id = invoice.get("subscription")
    if not stripe_subscription_id:
        logger.info(f"Invoice {invoice['id']} is not for a subscription, skipping")
        return

    # Find subscription by stripe_subscription_id using bypass context
    tenant_id = None
    user_id = None
    subscription_id = None

    async with job_bypass_context("Webhook subscription lookup") as db:
        result = await db.execute(
            select(crud_user_subscriptions.model).where(
                crud_user_subscriptions.model.stripe_subscription_id == stripe_subscription_id
            )
        )
        db_subscription = result.scalar_one_or_none()

        if not db_subscription:
            logger.warning(
                f"Subscription not found for stripe_subscription_id: {stripe_subscription_id}. "
                f"Raising error to trigger Stripe retry."
            )
            raise ValueError(f"Subscription not found for {stripe_subscription_id}")

        tenant_id = db_subscription.tenant_id
        user_id = db_subscription.user_id
        subscription_id = db_subscription.id

    # Create billing record with failed status
    billing_record = BillingRecordCreateInternal(
        tenant_id=tenant_id,
        user_id=user_id,
        user_subscription_id=subscription_id,
        stripe_payment_intent_id=invoice.get("payment_intent"),
        stripe_invoice_id=invoice["id"],
        amount_cents=invoice["amount_due"],
        currency=invoice["currency"],
        status="failed",
        description=f"Payment failed: {invoice.get('last_finalization_error', {}).get('message', 'Unknown error')}",
    )

    async with job_tenant_context(tenant_id) as db:
        await crud_billing_records.create(
            db=db,
            object=billing_record,
        )

        # Update subscription to past_due
        update_data = UserSubscriptionUpdate(status="past_due")
        await crud_user_subscriptions.update(
            db=db,
            object=update_data,
            id=subscription_id,
        )
        await db.commit()

    logger.info(f"Created failed billing record and updated subscription to past_due for invoice {invoice['id']}")


async def handle_charge_refunded(event: stripe.Event) -> None:
    """
    Handle charge.refunded event.

    Creates a billing record with refunded status.

    T077: charge.refunded webhook handler
    """
    charge = event["data"]["object"]
    logger.info(f"Processing charge.refunded: {charge['id']}")

    # Get the invoice if available
    stripe_invoice_id = charge.get("invoice")
    stripe_payment_intent_id = charge.get("payment_intent")

    if not stripe_invoice_id and not stripe_payment_intent_id:
        logger.info(f"Charge {charge['id']} has no invoice or payment_intent, skipping")
        return

    # Try to find the subscription through the invoice
    tenant_id = None
    user_id = None
    subscription_id = None

    # We need to use bypass context for both Stripe lookup (asyncio.to_thread is fine without db)
    # AND for the local subscription lookup

    stripe_subscription_id = None
    if stripe_invoice_id:
        try:
            invoice = await asyncio.to_thread(stripe.Invoice.retrieve, stripe_invoice_id)
            stripe_subscription_id = invoice.get("subscription")
        except stripe.error.StripeError as e:
            logger.error(f"Error retrieving invoice: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to retrieve invoice {stripe_invoice_id} from Stripe"
            )

    if not stripe_subscription_id:
        logger.warning(f"Could not determine subscription for charge {charge['id']}")
        return

    async with job_bypass_context("Webhook subscription lookup") as db:
        result = await db.execute(
            select(crud_user_subscriptions.model).where(
                crud_user_subscriptions.model.stripe_subscription_id == stripe_subscription_id
            )
        )
        db_subscription = result.scalar_one_or_none()

        if not db_subscription:
            logger.warning(
                f"Could not find subscription for charge {charge['id']} "
                f"(sub_id: {stripe_subscription_id}). Raising error to trigger Stripe retry."
            )
            raise ValueError(f"Subscription not found for {stripe_subscription_id}")

        tenant_id = db_subscription.tenant_id
        user_id = db_subscription.user_id
        subscription_id = db_subscription.id

    # Create billing record with refunded status
    refund_amount = charge.get("amount_refunded", 0)
    billing_record = BillingRecordCreateInternal(
        tenant_id=tenant_id,
        user_id=user_id,
        user_subscription_id=subscription_id,
        stripe_payment_intent_id=stripe_payment_intent_id,
        stripe_invoice_id=stripe_invoice_id,
        amount_cents=-refund_amount,  # Negative amount for refund
        currency=charge["currency"],
        status="refunded",
        description=f"Refund processed for charge {charge['id']}",
    )

    async with job_tenant_context(tenant_id) as db:
        await crud_billing_records.create(
            db=db,
            object=billing_record,
        )
        await db.commit()

    logger.info(f"Created refunded billing record for charge {charge['id']}")
