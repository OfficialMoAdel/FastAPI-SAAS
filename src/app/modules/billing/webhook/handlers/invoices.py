"""Invoice webhook event handlers."""

from __future__ import annotations

import stripe
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.app.modules.billing import webhook as _pkg
from src.app.modules.billing.lifecycle import is_valid_transition
from src.app.modules.billing.schemas import (
    BillingRecordCreateInternal,
    UserSubscriptionUpdate,
)


async def handle_invoice_payment_succeeded(event: stripe.Event) -> None:
    """Handle invoice.payment_succeeded event.

    Creates a billing record with succeeded status.
    T075: invoice.payment_succeeded webhook handler
    """
    invoice = event["data"]["object"]
    _pkg.logger.info("Processing invoice.payment_succeeded:", val=invoice["id"])

    stripe_subscription_id = invoice.get("subscription")
    if not stripe_subscription_id:
        _pkg.logger.info("Invoice is not for a subscription, skipping", val=invoice["id"])
        return

    # Find subscription by stripe_subscription_id using bypass context
    tenant_id = None
    user_id = None
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

    async with _pkg.job_tenant_context(tenant_id) as db:
        # P2-3: a failed row for this invoice may already exist (retried
        # payment that later succeeded). Fetch instead of a bare exists check
        # so a prior failed record is promoted to succeeded rather than
        # skipped — otherwise history shows only "failed" forever. Refund
        # rows never collide here (P0-2 stores stripe_invoice_id=None).
        invoice_id = invoice["id"]
        existing_record = await _pkg.crud_billing_records.get(
            db=db,
            stripe_invoice_id=invoice_id,
        )
        if existing_record is None:
            try:
                await _pkg.crud_billing_records.create(
                    db=db,
                    object=billing_record,
                )
            except IntegrityError:
                # Concurrent redelivery inside the dedup window already inserted the
                # record (unique constraint on stripe_invoice_id). Skip duplicate.
                await db.rollback()
                _pkg.logger.info(
                    "Billing record with stripe_invoice_id already exists (race), skipping duplicate insert",
                    stripe_invoice_id=invoice_id,
                )
                return
        elif existing_record["status"] == "succeeded":
            _pkg.logger.info(
                "Billing record with stripe_invoice_id already succeeded, skipping duplicate insert",
                stripe_invoice_id=invoice_id,
            )
            return
        else:
            await _pkg.crud_billing_records.update(
                db=db,
                object={
                    "status": "succeeded",
                    "amount_cents": invoice["amount_paid"],
                    "stripe_payment_intent_id": invoice.get("payment_intent"),
                    "description": "Invoice payment for subscription",
                },
                id=existing_record["id"],
                commit=False,
            )
            _pkg.logger.info(
                "Promoted failed billing record to succeeded on retried payment",
                stripe_invoice_id=invoice_id,
            )

        # P1.7: A successful payment must restore a past_due subscription to
        # active. Otherwise the user stays past_due (and locked out of paid
        # features) despite having paid, relying solely on a separate
        # customer.subscription.updated event that may be delayed or missed.
        # Only flip past_due -> active; never resurrect a canceled subscription.
        #
        # P1-6 FIX: the bypass-context read above is a stale snapshot (lock
        # released when that session exited). Re-verify and lock the row here
        # — mirroring handle_invoice_payment_failed — so a concurrently
        # canceled subscription cannot be resurrected by this update.
        lock_result = await db.execute(
            select(_pkg.crud_user_subscriptions.model)
            .where(_pkg.crud_user_subscriptions.model.id == subscription_id)
            .with_for_update()
        )
        db_subscription_locked = lock_result.scalar_one_or_none()

        if not db_subscription_locked:
            _pkg.logger.warning(
                "Subscription deleted in race condition gap, skipping status update",
                stripe_subscription_id=stripe_subscription_id,
            )
            await db.commit()  # persist the succeeded BillingRecord insert only
            return

        if db_subscription_locked.status == "past_due":
            await _pkg.crud_user_subscriptions.update(
                db=db,
                id=subscription_id,
                object={"status": "active"},
                commit=False,
            )

        await db.commit()

    _pkg.logger.info("Created succeeded billing record for invoice", val=invoice["id"])


async def handle_invoice_payment_failed(event: stripe.Event) -> None:
    """Handle invoice.payment_failed event.

    Creates a billing record with failed status and updates subscription to past_due.
    T076: invoice.payment_failed webhook handler
    """
    invoice = event["data"]["object"]
    _pkg.logger.info("Processing invoice.payment_failed:", val=invoice["id"])

    stripe_subscription_id = invoice.get("subscription")
    if not stripe_subscription_id:
        _pkg.logger.info("Invoice is not for a subscription, skipping", val=invoice["id"])
        return

    # Find subscription by stripe_subscription_id using bypass context
    tenant_id = None
    user_id = None
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
        description=f"Payment failed: {(invoice.get('last_finalization_error') or {}).get('message', 'Unknown error')}",
    )

    async with _pkg.job_tenant_context(tenant_id) as db:
        # Check if billing record with stripe_invoice_id already exists to prevent duplicate inserts
        if await _pkg.crud_billing_records.exists(
            db=db,
            stripe_invoice_id=invoice["id"],
            status="failed",
        ):
            _pkg.logger.info(
                "Failed billing record with stripe_invoice_id already exists, skipping duplicate insert",
                stripe_invoice_id=invoice["id"],
            )
            return

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

        try:
            await _pkg.crud_billing_records.create(
                db=db,
                object=billing_record,
                commit=False,
            )
        except IntegrityError:
            # Concurrent redelivery inside the dedup window already inserted the
            # record. Roll back and skip both insert and past_due update.
            await db.rollback()
            _pkg.logger.info(
                "Failed billing record with stripe_invoice_id already exists (race), skipping duplicate insert",
                stripe_invoice_id=invoice["id"],
            )
            return

        # B5 FIX: only move the subscription to past_due when its CURRENT
        # status allows it.
        if not is_valid_transition(db_subscription_locked.status, "past_due"):
            _pkg.logger.info(
                "Skipping past_due transition: current status does not permit it",
                stripe_subscription_id=stripe_subscription_id,
                current_status=str(db_subscription_locked.status),
            )
            await db.commit()  # persist the failed BillingRecord insert only
            return

        update_data = UserSubscriptionUpdate(status="past_due")
        await _pkg.crud_user_subscriptions.update(
            db=db,
            object=update_data,
            id=subscription_id,
            commit=False,
        )
        await db.commit()

    _pkg.logger.info(
        "Created failed billing record and updated subscription to past_due for invoice", val=invoice["id"]
    )


__all__ = ["handle_invoice_payment_failed", "handle_invoice_payment_succeeded"]
