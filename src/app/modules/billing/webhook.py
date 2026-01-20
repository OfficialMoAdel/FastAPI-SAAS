import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import stripe
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import local_session

from .crud import crud_billing_records, crud_user_subscriptions
from .schemas import (
    BillingRecordCreate,
    UserSubscriptionCreate,
    UserSubscriptionUpdate,
)

logger = logging.getLogger(__name__)


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
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
        return event
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

    Args:
        event: The Stripe event object
        background_tasks: FastAPI BackgroundTasks object (optional)

    Returns:
        Status dict
    """
    event_type = event["type"]
    logger.info(f"Received Stripe webhook event: {event_type}")

    try:
        async with local_session() as db:
            if event_type == "checkout.session.completed":
                await handle_checkout_session_completed(db, event)
            elif event_type == "customer.subscription.updated":
                await handle_subscription_updated(db, event)
            elif event_type == "customer.subscription.deleted":
                await handle_subscription_deleted(db, event)
            elif event_type == "invoice.payment_succeeded":
                await handle_invoice_payment_succeeded(db, event)
            elif event_type == "invoice.payment_failed":
                await handle_invoice_payment_failed(db, event)
            elif event_type == "charge.refunded":
                await handle_charge_refunded(db, event)
            else:
                logger.info(f"Unhandled event type: {event_type}")

            await db.commit()
    except Exception as e:
        logger.error(f"Error handling webhook event {event_type}: {e}")
        raise

    return {"status": "success"}


async def handle_checkout_session_completed(db: AsyncSession, event: stripe.Event) -> None:
    """
    Handle checkout.session.completed event.

    Creates a new UserSubscription record when checkout is completed.

    T074: checkout.session.completed webhook handler
    """
    session = event["data"]["object"]
    logger.info(f"Processing checkout.session.completed: {session['id']}")

    # Extract metadata
    metadata = session.get("metadata", {})
    tenant_id = int(metadata.get("tenant_id", 0))
    user_id = int(metadata.get("user_id", 0))
    plan_id = int(metadata.get("plan_id", 0))

    if not all([tenant_id, user_id, plan_id]):
        logger.error(
            "Missing metadata in checkout session: tenant_id=%s, user_id=%s, plan_id=%s",
            tenant_id,
            user_id,
            plan_id,
        )
        return

    stripe_subscription_id = session.get("subscription")
    # stripe_customer_id = session.get("customer")  # Unused

    # Retrieve subscription details from Stripe
    if stripe_subscription_id:
        try:
            stripe_sub = await asyncio.to_thread(
                stripe.Subscription.retrieve, stripe_subscription_id
            )
            current_period_start = datetime.fromtimestamp(stripe_sub.current_period_start, UTC)
            current_period_end = datetime.fromtimestamp(stripe_sub.current_period_end, UTC)

            # Determine billing interval from Stripe subscription
            interval = stripe_sub.items.data[0].price.recurring.interval if stripe_sub.items.data else "month"
            billing_interval = "yearly" if interval == "year" else "monthly"

            # Create user subscription
            subscription_data = UserSubscriptionCreate(
                user_id=user_id,
                subscription_plan_id=plan_id,
                stripe_subscription_id=stripe_subscription_id,
                status="active",
                billing_interval=billing_interval,
                current_period_start=current_period_start,
                current_period_end=current_period_end,
                cancel_at_period_end=False,
            )

            await crud_user_subscriptions.create(
                db=db,
                tenant_id=tenant_id,
                obj_in=subscription_data,
            )

            logger.info(f"Created subscription for user {user_id} in tenant {tenant_id}")

        except stripe.error.StripeError as e:
            logger.error(f"Stripe API error retrieving subscription: {e}")
            raise
    else:
        logger.warning(f"No subscription ID in checkout session: {session['id']}")


async def handle_subscription_updated(db: AsyncSession, event: stripe.Event) -> None:
    """
    Handle customer.subscription.updated event.

    Updates subscription status and period dates.

    T078: Subscription status update logic
    """
    subscription = event["data"]["object"]
    stripe_subscription_id = subscription["id"]
    logger.info(f"Processing subscription.updated: {stripe_subscription_id}")

    # Find subscription by stripe_subscription_id
    result = await db.execute(
        select(crud_user_subscriptions.model).where(
            crud_user_subscriptions.model.stripe_subscription_id == stripe_subscription_id
        )
    )
    db_subscription = result.scalar_one_or_none()

    if not db_subscription:
        logger.warning(f"Subscription not found for stripe_subscription_id: {stripe_subscription_id}")
        return

    # Map Stripe status to our status
    status_map = {
        "active": "active",
        "past_due": "past_due",
        "canceled": "canceled",
        "unpaid": "past_due",
        "incomplete": "past_due",
        "incomplete_expired": "expired",
        "trialing": "trialing",
    }
    new_status = status_map.get(subscription["status"], "active")

    # Update subscription
    update_data = UserSubscriptionUpdate(
        status=new_status,
        current_period_start=datetime.fromtimestamp(subscription["current_period_start"], UTC),
        current_period_end=datetime.fromtimestamp(subscription["current_period_end"], UTC),
        cancel_at_period_end=subscription.get("cancel_at_period_end", False),
    )

    if subscription.get("canceled_at"):
        update_data.canceled_at = datetime.fromtimestamp(subscription["canceled_at"], UTC)

    await crud_user_subscriptions.update(
        db=db,
        tenant_id=db_subscription.tenant_id,
        db_obj=db_subscription,
        obj_in=update_data,
    )

    logger.info(f"Updated subscription {stripe_subscription_id} to status {new_status}")


async def handle_subscription_deleted(db: AsyncSession, event: stripe.Event) -> None:
    """
    Handle customer.subscription.deleted event.

    Marks subscription as canceled/expired.
    """
    subscription = event["data"]["object"]
    stripe_subscription_id = subscription["id"]
    logger.info(f"Processing subscription.deleted: {stripe_subscription_id}")

    # Find subscription by stripe_subscription_id
    result = await db.execute(
        select(crud_user_subscriptions.model).where(
            crud_user_subscriptions.model.stripe_subscription_id == stripe_subscription_id
        )
    )
    db_subscription = result.scalar_one_or_none()

    if not db_subscription:
        logger.warning(f"Subscription not found for stripe_subscription_id: {stripe_subscription_id}")
        return

    # Update subscription to canceled/expired
    update_data = UserSubscriptionUpdate(
        status="canceled",
        canceled_at=datetime.now(UTC),
    )

    await crud_user_subscriptions.update(
        db=db,
        tenant_id=db_subscription.tenant_id,
        db_obj=db_subscription,
        obj_in=update_data,
    )

    logger.info(f"Marked subscription {stripe_subscription_id} as canceled")


async def handle_invoice_payment_succeeded(db: AsyncSession, event: stripe.Event) -> None:
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

    # Find subscription by stripe_subscription_id
    result = await db.execute(
        select(crud_user_subscriptions.model).where(
            crud_user_subscriptions.model.stripe_subscription_id == stripe_subscription_id
        )
    )
    db_subscription = result.scalar_one_or_none()

    if not db_subscription:
        logger.warning(f"Subscription not found for stripe_subscription_id: {stripe_subscription_id}")
        return

    # Create billing record
    billing_record = BillingRecordCreate(
        user_id=db_subscription.user_id,
        user_subscription_id=db_subscription.id,
        stripe_payment_intent_id=invoice.get("payment_intent"),
        stripe_invoice_id=invoice["id"],
        amount_cents=invoice["amount_paid"],
        currency=invoice["currency"],
        status="succeeded",
        description="Invoice payment for subscription",
    )

    await crud_billing_records.create(
        db=db,
        tenant_id=db_subscription.tenant_id,
        obj_in=billing_record,
    )

    logger.info(f"Created succeeded billing record for invoice {invoice['id']}")


async def handle_invoice_payment_failed(db: AsyncSession, event: stripe.Event) -> None:
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

    # Find subscription by stripe_subscription_id
    result = await db.execute(
        select(crud_user_subscriptions.model).where(
            crud_user_subscriptions.model.stripe_subscription_id == stripe_subscription_id
        )
    )
    db_subscription = result.scalar_one_or_none()

    if not db_subscription:
        logger.warning(f"Subscription not found for stripe_subscription_id: {stripe_subscription_id}")
        return

    # Create billing record with failed status
    billing_record = BillingRecordCreate(
        user_id=db_subscription.user_id,
        user_subscription_id=db_subscription.id,
        stripe_payment_intent_id=invoice.get("payment_intent"),
        stripe_invoice_id=invoice["id"],
        amount_cents=invoice["amount_due"],
        currency=invoice["currency"],
        status="failed",
        description=f"Payment failed: {invoice.get('last_finalization_error', {}).get('message', 'Unknown error')}",
    )

    await crud_billing_records.create(
        db=db,
        tenant_id=db_subscription.tenant_id,
        obj_in=billing_record,
    )

    # Update subscription to past_due
    update_data = UserSubscriptionUpdate(status="past_due")
    await crud_user_subscriptions.update(
        db=db,
        tenant_id=db_subscription.tenant_id,
        db_obj=db_subscription,
        obj_in=update_data,
    )

    logger.info(f"Created failed billing record and updated subscription to past_due for invoice {invoice['id']}")


async def handle_charge_refunded(db: AsyncSession, event: stripe.Event) -> None:
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
    db_subscription = None
    if stripe_invoice_id:
        try:
            invoice = await asyncio.to_thread(
                stripe.Invoice.retrieve, stripe_invoice_id
            )
            stripe_subscription_id = invoice.get("subscription")
            if stripe_subscription_id:
                result = await db.execute(
                    select(crud_user_subscriptions.model).where(
                        crud_user_subscriptions.model.stripe_subscription_id == stripe_subscription_id
                    )
                )
                db_subscription = result.scalar_one_or_none()
        except stripe.error.StripeError as e:
            logger.error(f"Error retrieving invoice: {e}")

    if not db_subscription:
        logger.warning(f"Could not find subscription for charge {charge['id']}")
        return

    # Create billing record with refunded status
    refund_amount = charge.get("amount_refunded", 0)
    billing_record = BillingRecordCreate(
        user_id=db_subscription.user_id,
        user_subscription_id=db_subscription.id,
        stripe_payment_intent_id=stripe_payment_intent_id,
        stripe_invoice_id=stripe_invoice_id,
        amount_cents=-refund_amount,  # Negative amount for refund
        currency=charge["currency"],
        status="refunded",
        description=f"Refund processed for charge {charge['id']}",
    )

    await crud_billing_records.create(
        db=db,
        tenant_id=db_subscription.tenant_id,
        obj_in=billing_record,
    )

    logger.info(f"Created refunded billing record for charge {charge['id']}")
