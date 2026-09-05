"""Refund webhook event handlers."""

from __future__ import annotations

import asyncio

import stripe
from sqlalchemy import func as _func
from sqlalchemy import select

from src.app.modules.billing import webhook as _pkg
from src.app.modules.billing.models import BillingRecord
from src.app.modules.billing.schemas import (
    BillingRecordCreateInternal,
)


async def handle_charge_refunded(event: stripe.Event) -> None:
    """Handle charge.refunded event.

    Creates a billing record with refunded status.
    T077: charge.refunded webhook handler
    """
    charge = event["data"]["object"]
    _pkg.logger.info("Processing charge.refunded:", val=charge["id"])

    # Get the invoice if available
    stripe_invoice_id = charge.get("invoice")
    stripe_payment_intent_id = charge.get("payment_intent")

    if not stripe_invoice_id and not stripe_payment_intent_id:
        _pkg.logger.info("Charge has no invoice or payment_intent, skipping", val=charge["id"])
        return

    # Try to find the subscription through the invoice
    tenant_id = None
    user_id = None
    subscription_id = None

    stripe_subscription_id = None
    if stripe_invoice_id:
        try:
            api_key = (
                _pkg.settings.STRIPE_SECRET_KEY.get_secret_value()
                if hasattr(_pkg.settings.STRIPE_SECRET_KEY, "get_secret_value")
                else getattr(_pkg.settings, "STRIPE_SECRET_KEY", None)
            )
            invoice = await asyncio.to_thread(stripe.Invoice.retrieve, stripe_invoice_id, api_key=api_key)
            stripe_subscription_id = invoice.get("subscription")
        except stripe.StripeError as e:
            _pkg.logger.error("Error retrieving invoice", error=e)
            raise ValueError(f"Failed to retrieve invoice {stripe_invoice_id} from Stripe") from e

    if not stripe_subscription_id:
        _pkg.logger.warning("Could not determine subscription for charge", val=charge["id"])
        return

    async with _pkg.job_bypass_context("Webhook subscription lookup") as db:
        result = await db.execute(
            select(_pkg.crud_user_subscriptions.model)
            .where(_pkg.crud_user_subscriptions.model.stripe_subscription_id == stripe_subscription_id)
            .with_for_update()
        )
        db_subscription = result.scalar_one_or_none()

        if not db_subscription:
            _pkg.logger.warning(
                f"Could not find subscription for charge {charge['id']} "
                f"(sub_id: {stripe_subscription_id}). Raising error to trigger Stripe retry."
            )
            raise ValueError(f"Subscription not found for {stripe_subscription_id}")

        tenant_id = db_subscription.tenant_id
        user_id = db_subscription.user_id
        subscription_id = db_subscription.id

    async with _pkg.job_tenant_context(tenant_id) as db:
        refunds = charge.get("refunds", {}).get("data", [])

        if not refunds:
            # B9 FIX: charge.amount_refunded is CUMULATIVE across all refunds
            # of the charge. Record only the delta vs refund amounts already persisted.
            if stripe_payment_intent_id is None and stripe_invoice_id is None:
                _pkg.logger.warning("Refund event lacks scoping identifiers, skipping", val=charge["id"])
                return

            scope_filters = [BillingRecord.status == "refunded"]
            if stripe_payment_intent_id is not None:
                scope_filters.append(BillingRecord.stripe_payment_intent_id == stripe_payment_intent_id)
            else:
                scope_filters.append(BillingRecord.stripe_invoice_id == stripe_invoice_id)

            recorded_result = await db.execute(
                select(_func.coalesce(_func.sum(BillingRecord.amount_cents), 0)).where(*scope_filters)
            )
            # Stored refund amounts are negative.
            already_refunded_cents = -int(recorded_result.scalar_one() or 0)

            refund_amount = int(charge.get("amount_refunded", 0) or 0) - already_refunded_cents
            if refund_amount <= 0:
                _pkg.logger.info(
                    "Refund delta non-positive, skipping duplicate insert",
                    val=charge["id"],
                    cumulative=int(charge.get("amount_refunded", 0) or 0),
                    already_recorded=already_refunded_cents,
                )
                return

            billing_record = BillingRecordCreateInternal(
                tenant_id=tenant_id,
                user_id=user_id,
                user_subscription_id=subscription_id,
                stripe_payment_intent_id=stripe_payment_intent_id,
                stripe_invoice_id=stripe_invoice_id,
                amount_cents=-refund_amount,
                currency=charge["currency"],
                status="refunded",
                description=f"Refund processed for charge {charge['id']}",
            )
            await _pkg.crud_billing_records.create(db=db, object=billing_record)
        else:
            for refund in refunds:
                refund_id = refund["id"]
                if await _pkg.crud_billing_records.exists(
                    db=db,
                    stripe_refund_id=refund_id,
                ):
                    continue

                billing_record = BillingRecordCreateInternal(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    user_subscription_id=subscription_id,
                    stripe_payment_intent_id=stripe_payment_intent_id,
                    stripe_invoice_id=stripe_invoice_id,
                    stripe_refund_id=refund_id,
                    amount_cents=-refund["amount"],
                    currency=charge["currency"],
                    status="refunded",
                    description=f"Refund {refund_id} processed for charge {charge['id']}",
                )
                await _pkg.crud_billing_records.create(db=db, object=billing_record)

        await db.commit()

    _pkg.logger.info("Created refunded billing record(s) for charge", val=charge["id"])


__all__ = ["handle_charge_refunded"]
