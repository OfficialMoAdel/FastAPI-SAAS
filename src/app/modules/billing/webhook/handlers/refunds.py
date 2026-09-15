"""Refund webhook event handlers."""

from __future__ import annotations

import stripe
from sqlalchemy import func as _func
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.app.modules.billing import webhook as _pkg
from src.app.modules.billing.models import BillingRecord
from src.app.modules.billing.schemas import (
    BillingRecordCreateInternal,
)
from src.app.modules.billing.webhook.locks import find_subscription_by_stripe_id
from src.app.shared.exceptions import StripeError


async def handle_charge_refunded(event: stripe.Event) -> None:
    """Handle charge.refunded event.

    Creates a billing record with refunded status.
    T077: charge.refunded webhook handler

    Invariant (P0-2): refund rows never carry stripe_invoice_id — the
    succeeded/failed row owns the per-invoice unique slot
    (uq_billing_records_stripe_invoice). Refunds link via
    stripe_payment_intent_id / stripe_refund_id.
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
            from src.app.modules.billing.gateway import get_payment_gateway

            # P1-9: route through the PaymentGateway seam (B15 pattern from
            # handlers/checkout.py) — per-tenant key resolution plus
            # with_retry on transient Stripe failures. The tenant is still
            # unknown here (this lookup is what discovers it), so no
            # tenant_id is passed and the gateway falls back to the platform
            # key — the same key the raw call below used, minus the bypass.
            invoice = await get_payment_gateway().retrieve_invoice(stripe_invoice_id)
            stripe_subscription_id = invoice.get("subscription")
        except StripeError as e:
            _pkg.logger.error("Error retrieving invoice", error=e)
            raise ValueError(f"Failed to retrieve invoice {stripe_invoice_id} from Stripe") from e

    if not stripe_subscription_id:
        _pkg.logger.warning("Could not determine subscription for charge", val=charge["id"])
        return

    # P2-#12: the lock dance lives in webhook.locks (refunds has no Phase-2
    # re-lock by design — it never mutates the subscription row).
    subscription_ref = await find_subscription_by_stripe_id(stripe_subscription_id)
    tenant_id = subscription_ref.tenant_id
    user_id = subscription_ref.user_id
    subscription_id = subscription_ref.subscription_id

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
                # No payment_intent: fall back to subscription scope. Refund
                # rows no longer carry stripe_invoice_id (see below), so an
                # invoice-scoped sum would miss them. Subscription scope can
                # over-count when sibling charges were refunded — that errs
                # toward skipping (safe direction), never toward a 500.
                scope_filters.append(BillingRecord.user_subscription_id == subscription_id)

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
                # P0-2: refund rows must NOT carry stripe_invoice_id.
                # uq_billing_records_stripe_invoice allows exactly one row per
                # invoice (the succeeded/failed row owns it); writing it here
                # raised IntegrityError -> 500 -> Stripe retry storm. The
                # invoice stays recoverable via payment_intent + charge id
                # in the description.
                stripe_invoice_id=None,
                amount_cents=-refund_amount,
                currency=charge["currency"],
                status="refunded",
                description=f"Refund processed for charge {charge['id']}",
            )
            try:
                await _pkg.crud_billing_records.create(db=db, object=billing_record)
            except IntegrityError:
                # Concurrent redelivery already inserted the delta row.
                # Roll back and skip instead of 500ing the webhook.
                await db.rollback()
                _pkg.logger.info(
                    "Refund delta row already exists (race), skipping duplicate insert",
                    val=charge["id"],
                )
                return
        else:
            # P1-11: bulk dedup — one SELECT ... WHERE stripe_refund_id IN
            # (...) replaces N per-refund exists() round-trips, and a single
            # add_all + flush replaces N INSERT round-trips. Set membership
            # over the same id list in the same txn is equivalent to per-id
            # exists(); same rows, same commit boundary.
            refund_ids = [refund["id"] for refund in refunds]
            from src.app.core.db.tenant_crud import tenant_scope

            existing_result = await db.execute(
                select(BillingRecord.stripe_refund_id).where(
                    tenant_scope(BillingRecord, tenant_id),
                    BillingRecord.stripe_refund_id.in_(refund_ids),
                )
            )
            already_recorded = set(existing_result.scalars().all())

            new_rows = [
                BillingRecord(
                    **BillingRecordCreateInternal(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        user_subscription_id=subscription_id,
                        stripe_payment_intent_id=stripe_payment_intent_id,
                        # P0-2: see above — the succeeded/failed row owns the
                        # invoice id; refund rows link via payment_intent +
                        # stripe_refund_id (which has its own unique index).
                        stripe_invoice_id=None,
                        stripe_refund_id=refund["id"],
                        amount_cents=-refund["amount"],
                        currency=charge["currency"],
                        status="refunded",
                        description=f"Refund {refund['id']} processed for charge {charge['id']}",
                    ).model_dump()
                )
                for refund in refunds
                if refund["id"] not in already_recorded
            ]
            if new_rows:
                db.add_all(new_rows)
                try:
                    await db.flush()
                except IntegrityError:
                    # Concurrent redelivery inside the stripe_refund_id dedup
                    # window inserted some of these rows between our IN query
                    # and the flush. Roll back and fall back to the per-row
                    # exists()+create path, which skips row-by-row.
                    await db.rollback()
                    _pkg.logger.info(
                        "Bulk refund insert raced redelivery, falling back to per-row dedup",
                        val=charge["id"],
                    )
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
                            stripe_invoice_id=None,
                            stripe_refund_id=refund_id,
                            amount_cents=-refund["amount"],
                            currency=charge["currency"],
                            status="refunded",
                            description=f"Refund {refund_id} processed for charge {charge['id']}",
                        )
                        try:
                            await _pkg.crud_billing_records.create(db=db, object=billing_record)
                        except IntegrityError:
                            await db.rollback()
                            _pkg.logger.info(
                                "Refund row already exists (race), skipping duplicate insert",
                                val=refund_id,
                            )
                            continue

        await db.commit()

    _pkg.logger.info("Created refunded billing record(s) for charge", val=charge["id"])


__all__ = ["handle_charge_refunded"]
