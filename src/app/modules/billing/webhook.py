import asyncio
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

import stripe
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.utils.cache import client as redis_client
from src.app.core.worker.rls_context import job_bypass_context, job_tenant_context
from src.app.modules.billing.crud import crud_billing_records, crud_user_subscriptions
from src.app.modules.billing.lifecycle import map_stripe_status
from src.app.modules.billing.schemas import (
    BillingRecordCreateInternal,
    SubscriptionStatus,
    UserSubscriptionCreateInternal,
    UserSubscriptionUpdate,
)

logger = get_logger(__name__)

# Redis key prefix for webhook idempotency tracking
# Stripe events have at-least-once delivery guarantee, so we must track processed events
_WEBHOOK_EVENT_KEY_PREFIX = "stripe:webhook:processed:"
# Short-lived lock held ONLY while a handler is actively running. If the process
# crashes mid-handling, this key auto-expires and Stripe can safely retry,
# instead of the event being permanently marked "processed" and lost.
_WEBHOOK_PROCESSING_TTL_SECONDS = 300  # 5 minutes max handler time


async def _try_acquire_event_lock(event_id: str) -> bool:
    """Atomically try to acquire processing lock for a webhook event.

    Primary: Redis SET with NX (set-if-not-exists) for atomic idempotency.
    Fallback: DB billing record check when Redis is unavailable.

    Args:
        event_id: The Stripe event ID (e.g., "evt_1234567890")

    Returns:
        True if lock acquired (event NOT previously processed), False if already processed
    """
    if redis_client is None:
        # Redis not available — fall back to DB idempotency check
        return await _db_idempotency_check(event_id)

    try:
        key = f"{_WEBHOOK_EVENT_KEY_PREFIX}{event_id}"
        # SET key value NX EX ttl - atomic set-if-not-exists with a SHORT
        # processing-window TTL. This is a *processing* lock, NOT the
        # "processed" marker. The durable marker is written only AFTER the
        # handler commits (see _mark_event_processed), so a crash mid-handling
        # lets the short lock expire and Stripe retry instead of silently
        # losing the event for 7 days.
        result = await redis_client.set(key, "processing", nx=True, ex=_WEBHOOK_PROCESSING_TTL_SECONDS)
        return result is not None  # True = lock acquired, False = already processed
    except Exception as e:
        logger.error(
            "Failed to acquire webhook event lock, falling back to DB check",
            event_id=event_id,
            error=str(e),
        )
        # Fall back to DB idempotency check instead of failing open
        return await _db_idempotency_check(event_id)


async def _db_idempotency_check(event_id: str) -> bool:
    """DB-based idempotency fallback for when Redis is unavailable.

    Atomically claims the event by inserting a ``WebhookEvent`` row; the
    unique ``event_id`` PK makes a duplicate insert fail with ``IntegrityError``,
    which we treat as "already processed". On failure we delete the row
    (see ``_release_webhook_event_lock``) so Stripe can safely retry.

    Args:
        event_id: The Stripe event ID

    Returns:
        True if event should be processed (not yet seen), False if duplicate
    """
    from sqlalchemy.exc import IntegrityError

    from src.app.modules.billing.models import WebhookEvent

    try:
        async with job_bypass_context("Webhook DB idempotency check") as db:
            event = WebhookEvent(event_id=event_id)
            db.add(event)
            try:
                await db.commit()
                return True  # Successfully inserted, safe to process
            except IntegrityError:
                await db.rollback()
                logger.info(
                    "DB idempotency check: event already processed, skipping",
                    event_id=event_id,
                )
                return False  # Already processed
    except Exception as db_err:
        # If both Redis AND DB checks fail, fail closed.
        # Let Stripe retry later when the DB/Cache is healthy to avoid duplicates.
        logger.error(
            "DB idempotency fallback also failed, rejecting processing",
            event_id=event_id,
            error=str(db_err),
        )
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="Service unavailable for idempotency check. Please retry.")


async def _release_webhook_event_lock(event_id: str) -> None:
    """Release Redis and DB locks if event processing fails."""
    lock_released = False

    # Do NOT mark as processed on failure - allow Stripe to retry
    # CRITICAL FIX: We MUST delete the Redis lock we just acquired, otherwise
    # Stripe's retries will be false-positively rejected as "already_processed" for 7 days!
    if redis_client:
        try:
            await redis_client.delete(f"{_WEBHOOK_EVENT_KEY_PREFIX}{event_id}")
            lock_released = True
        except Exception as redis_e:
            logger.error("Failed to release webhook event lock:", redis_e=redis_e)

    # Also remove from DB to allow retries if DB fallback was used
    from sqlalchemy import delete

    from src.app.core.worker.rls_context import job_bypass_context
    from src.app.modules.billing.models import WebhookEvent

    try:
        async with job_bypass_context("Webhook DB unlock on error") as db:
            await db.execute(delete(WebhookEvent).where(WebhookEvent.event_id == event_id))
            await db.commit()
            lock_released = True
    except Exception as db_err:
        logger.error("Failed to release DB webhook event lock:", db_err=str(db_err))

    if not lock_released:
        logger.critical(
            "Failed to release BOTH Redis and DB locks for failed webhook. "
            "Stripe retries will be falsely rejected! Manual intervention required.",
            event_id=event_id,
        )
        # Enqueue a fallback cleanup task
        try:
            from src.app.core.utils.queue import pool

            if pool:
                await pool.enqueue_job("cleanup_leaked_webhook_lock", event_id, _defer_by=300)
        except Exception as q_err:
            logger.error("Failed to enqueue fallback cleanup task for webhook lock:", error=str(q_err))


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
        logger.error("Invalid payload", error=e)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        logger.error("Invalid signature", error=e)
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
    logger.info("Received Stripe webhook event", event_type=event_type, event_id=event_id)

    # IDEMPOTENCY CHECK: Atomically acquire processing lock
    # Uses SET NX for atomic check-and-mark, eliminating TOCTOU race
    if not await _try_acquire_event_lock(event_id):
        logger.info("Event already processed, skipping", event_id=event_id)
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
            logger.info("Unhandled event type:", event_type=event_type)
            # Lock already acquired in _try_acquire_event_lock; no extra mark needed.
            return {"status": "unhandled"}

        # Lock was already acquired in _try_acquire_event_lock
        # No need to mark again - atomic SET NX handles this

    except Exception as e:
        logger.error(
            f"Error handling webhook event (ID: {event_id}):",
            event_type=event_type,
            event_id=event_id,
            error=e,
        )

        await _release_webhook_event_lock(event_id)

        raise

    # Success path: the processing lock acquired in _try_acquire_event_lock
    # (bounded TTL) already marks the event as in-flight. On failure the
    # lock is released (see _release_webhook_event_lock) so Stripe can retry.
    return {"status": "success"}


async def handle_checkout_session_completed(event: stripe.Event) -> None:
    """
    Handle checkout.session.completed event.

    Creates a new UserSubscription record when checkout is completed.

    T074: checkout.session.completed webhook handler
    """
    session = event["data"]["object"]
    logger.info("Processing checkout.session.completed:", val=session["id"])

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
            logger.warning("Invalid UUID format for tenant_id:", tenant_id_str=tenant_id_str)

    user_id = int(user_id_str) if user_id_str and user_id_str.isdigit() else None
    plan_id = int(plan_id_str) if plan_id_str and plan_id_str.isdigit() else None

    if tenant_id is None or user_id is None or plan_id is None:
        logger.error(
            "Missing or invalid metadata in checkout session",
            session_id=session["id"],
            tenant_id=str(tenant_id) if tenant_id else None,
            user_id=user_id,
            plan_id=plan_id,
        )
        raise ValueError(f"Missing metadata: tenant_id={tenant_id}, user_id={user_id}, plan_id={plan_id}")

    stripe_subscription_id = session.get("subscription")
    # stripe_customer_id = session.get("customer")  # Unused

    # Retrieve subscription details from Stripe
    if stripe_subscription_id:
        try:
            api_key = (
                settings.STRIPE_SECRET_KEY.get_secret_value()
                if hasattr(settings.STRIPE_SECRET_KEY, "get_secret_value")
                else getattr(settings, "STRIPE_SECRET_KEY", None)
            )
            stripe_sub = await asyncio.to_thread(stripe.Subscription.retrieve, stripe_subscription_id, api_key=api_key)
            period_start = stripe_sub.get("current_period_start")
            period_end = stripe_sub.get("current_period_end")
            if period_start is None or period_end is None:
                raise ValueError(f"Missing critical timestamps in Stripe subscription {stripe_subscription_id}")

            current_period_start = datetime.fromtimestamp(period_start, UTC)
            current_period_end = datetime.fromtimestamp(period_end, UTC)

            # Determine billing interval from Stripe subscription
            interval = "month"
            if stripe_sub.get("items") and stripe_sub["items"].get("data"):
                price = stripe_sub["items"]["data"][0].get("price")
                if price and price.get("recurring") and price["recurring"].get("interval"):
                    interval = price["recurring"]["interval"]
            billing_interval: Literal["monthly", "yearly"] = "yearly" if interval == "year" else "monthly"

            # Create user subscription with tenant_id in the object
            subscription_data = UserSubscriptionCreateInternal(
                tenant_id=tenant_id,
                user_id=user_id,
                subscription_plan_id=plan_id,
                stripe_subscription_id=stripe_subscription_id,
                status="active",
                billing_interval=billing_interval,
                current_period_start=current_period_start,
                current_period_end=current_period_end,
                cancel_at_period_end=False,
            )

            async with job_tenant_context(tenant_id) as db:
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
                    # Normalize the raw Stripe status through map_stripe_status so the
                    # stored enum is always valid (consistent with the race branch
                    # and handle_subscription_updated).
                    update_data: dict[str, Any] = {
                        "status": map_stripe_status(stripe_sub.get("status", "")),
                        "current_period_start": datetime.fromtimestamp(
                            stripe_sub.get("current_period_start") or int(datetime.now(UTC).timestamp()), UTC
                        ),
                        "current_period_end": datetime.fromtimestamp(
                            stripe_sub.get("current_period_end") or int(datetime.now(UTC).timestamp()), UTC
                        ),
                        "cancel_at_period_end": stripe_sub.get("cancel_at_period_end", False),
                    }
                    canceled_at = stripe_sub.get("canceled_at")
                    if canceled_at:
                        update_data["canceled_at"] = datetime.fromtimestamp(canceled_at, UTC)

                    await crud_user_subscriptions.update(
                        db=db,
                        stripe_subscription_id=stripe_subscription_id,
                        object=update_data,
                        commit=False,
                    )
                    await db.commit()
                else:
                    # Record doesn't exist - try to create, handle race condition
                    try:
                        await crud_user_subscriptions.create(
                            db=db,
                            object=subscription_data,
                            commit=False,
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
                        status_map = {
                            "active": "active",
                            "past_due": "past_due",
                            "unpaid": "past_due",
                            "canceled": "canceled",
                            "incomplete": "pending",
                            "incomplete_expired": "canceled",
                            "trialing": "trialing",
                        }
                        raw_status = stripe_sub.get("status", "")
                        mapped_status = status_map.get(raw_status, "active")

                        race_update_data: dict[str, Any] = {
                            "status": mapped_status,
                            "current_period_start": datetime.fromtimestamp(
                                stripe_sub.get("current_period_start") or int(datetime.now(UTC).timestamp()), UTC
                            ),
                            "current_period_end": datetime.fromtimestamp(
                                stripe_sub.get("current_period_end") or int(datetime.now(UTC).timestamp()), UTC
                            ),
                            "cancel_at_period_end": stripe_sub.get("cancel_at_period_end", False),
                        }
                        canceled_at = stripe_sub.get("canceled_at")
                        if canceled_at:
                            race_update_data["canceled_at"] = datetime.fromtimestamp(canceled_at, UTC)

                        await crud_user_subscriptions.update(
                            db=db,
                            stripe_subscription_id=stripe_subscription_id,
                            object=race_update_data,
                            commit=False,
                        )
                        await db.commit()

            logger.info("Created subscription for user in tenant", user_id=user_id, tenant_id=tenant_id)

        except stripe.error.StripeError as e:
            logger.error("Stripe API error retrieving subscription", error=e)
            raise
    else:
        logger.warning("No subscription ID in checkout session:", val=session["id"])


async def handle_subscription_updated(event: stripe.Event) -> None:
    """
    Handle customer.subscription.updated event.

    Updates subscription status and period dates.

    T078: Subscription status update logic
    """
    subscription = event["data"]["object"]
    stripe_subscription_id = subscription["id"]
    logger.info("Processing subscription.updated:", stripe_subscription_id=stripe_subscription_id)

    # Find subscription by stripe_subscription_id using bypass context
    tenant_id = None
    subscription_id = None

    async with job_bypass_context("Webhook subscription lookup") as db:
        result = await db.execute(
            select(crud_user_subscriptions.model)
            .where(crud_user_subscriptions.model.stripe_subscription_id == stripe_subscription_id)
            .with_for_update()
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
    from src.app.modules.billing.lifecycle import map_stripe_status

    new_status: SubscriptionStatus = map_stripe_status(subscription["status"])  # type: ignore

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
        # ATOMIC LOCK: Re-verify and lock record within tenant context to prevent TOCTOU
        lock_result = await db.execute(
            select(crud_user_subscriptions.model)
            .where(crud_user_subscriptions.model.id == subscription_id)
            .with_for_update()
        )
        db_subscription_locked = lock_result.scalar_one_or_none()

        if not db_subscription_locked:
            logger.warning(
                "Subscription deleted in race condition gap, skipping update",
                stripe_subscription_id=stripe_subscription_id,
            )
            return

        await crud_user_subscriptions.update(
            db=db,
            object=update_data,
            id=subscription_id,
        )

        # M-04: Explicitly downgrade user to free tier if canceled/expired
        if new_status in ("canceled", "expired"):
            from sqlalchemy import func

            from src.app.modules.tiers.crud import crud_tiers
            from src.app.modules.users.crud import crud_users

            # Find the free plan
            # BUG FIX: Use exact match and lower case for case-insensitivity
            free_plan_stmt = (
                select(crud_tiers.model)
                .where(func.lower(crud_tiers.model.name) == settings.DEFAULT_FREE_TIER_NAME.lower())
                .limit(1)
            )
            free_plan_result = await db.execute(free_plan_stmt)
            free_plan = free_plan_result.scalar_one_or_none()

            if free_plan:
                await crud_users.update(db=db, id=db_subscription.user_id, object={"tier_id": free_plan.id})
                logger.info(
                    "Downgraded user to free tier due to subscription cancellation",
                    user_id=db_subscription.user_id,
                    tenant_id=tenant_id,
                    tier_id=free_plan.id,
                    reason=new_status,
                )

        await db.commit()

    logger.info("Updated subscription to status", stripe_subscription_id=stripe_subscription_id, new_status=new_status)


async def handle_subscription_deleted(event: stripe.Event) -> None:
    """
    Handle customer.subscription.deleted event.

    Marks subscription as canceled/expired.
    """
    subscription = event["data"]["object"]
    stripe_subscription_id = subscription["id"]
    logger.info("Processing subscription.deleted:", stripe_subscription_id=stripe_subscription_id)

    # Find subscription by stripe_subscription_id using bypass context
    tenant_id = None
    subscription_id = None

    async with job_bypass_context("Webhook subscription lookup") as db:
        result = await db.execute(
            select(crud_user_subscriptions.model)
            .where(crud_user_subscriptions.model.stripe_subscription_id == stripe_subscription_id)
            .with_for_update()
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

    canceled_at_ts = subscription.get("canceled_at")
    # Update subscription to canceled/expired
    update_data = UserSubscriptionUpdate(
        status="canceled",
        canceled_at=datetime.fromtimestamp(canceled_at_ts, UTC) if canceled_at_ts else datetime.now(UTC),
    )

    async with job_tenant_context(tenant_id) as db:
        # ATOMIC LOCK: Re-verify and lock record within tenant context to prevent TOCTOU
        lock_result = await db.execute(
            select(crud_user_subscriptions.model)
            .where(crud_user_subscriptions.model.id == subscription_id)
            .with_for_update()
        )
        db_subscription_locked = lock_result.scalar_one_or_none()

        if not db_subscription_locked:
            logger.warning(
                "Subscription deleted in race condition gap, skipping update",
                stripe_subscription_id=stripe_subscription_id,
            )
            return

        await crud_user_subscriptions.update(
            db=db,
            object=update_data,
            id=subscription_id,
        )

        # M-04: Explicitly downgrade user to free tier
        from sqlalchemy import func

        from src.app.modules.tiers.crud import crud_tiers
        from src.app.modules.users.crud import crud_users

        # Find the free plan
        # BUG FIX: Use exact match and lower case for case-insensitivity
        free_plan_stmt = (
            select(crud_tiers.model)
            .where(func.lower(crud_tiers.model.name) == settings.DEFAULT_FREE_TIER_NAME.lower())
            .limit(1)
        )
        free_plan_result = await db.execute(free_plan_stmt)
        free_plan = free_plan_result.scalar_one_or_none()

        if free_plan:
            await crud_users.update(db=db, id=db_subscription.user_id, object={"tier_id": free_plan.id})
            logger.info("Downgraded user to free tier ()", user_id=db_subscription.user_id, id=free_plan.id)

        await db.commit()

    logger.info("Marked subscription as canceled", stripe_subscription_id=stripe_subscription_id)


async def handle_invoice_payment_succeeded(event: stripe.Event) -> None:
    """
    Handle invoice.payment_succeeded event.

    Creates a billing record with succeeded status.

    T075: invoice.payment_succeeded webhook handler
    """
    invoice = event["data"]["object"]
    logger.info("Processing invoice.payment_succeeded:", val=invoice["id"])

    stripe_subscription_id = invoice.get("subscription")
    if not stripe_subscription_id:
        logger.info("Invoice is not for a subscription, skipping", val=invoice["id"])
        return

    # Find subscription by stripe_subscription_id using bypass context
    tenant_id = None
    user_id = None
    subscription_id = None

    async with job_bypass_context("Webhook subscription lookup") as db:
        result = await db.execute(
            select(crud_user_subscriptions.model)
            .where(crud_user_subscriptions.model.stripe_subscription_id == stripe_subscription_id)
            .with_for_update()
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
        # Check if billing record with stripe_invoice_id already exists to prevent duplicate inserts
        invoice_id = invoice["id"]
        exists_record = await crud_billing_records.exists(
            db=db,
            stripe_invoice_id=invoice_id,
        )
        if exists_record:
            logger.info(
                "Billing record with stripe_invoice_id already exists, skipping duplicate insert",
                stripe_invoice_id=invoice_id,
            )
            return

        await crud_billing_records.create(
            db=db,
            object=billing_record,
        )

        # P1.7: A successful payment must restore a past_due subscription to
        # active. Otherwise the user stays past_due (and locked out of paid
        # features) despite having paid, relying solely on a separate
        # customer.subscription.updated event that may be delayed or missed.
        # Only flip past_due -> active; never resurrect a canceled subscription.
        if db_subscription.status == "past_due":
            await crud_user_subscriptions.update(
                db=db,
                id=subscription_id,
                object={"status": "active"},
                commit=False,
            )

        await db.commit()

    logger.info("Created succeeded billing record for invoice", val=invoice["id"])


async def handle_invoice_payment_failed(event: stripe.Event) -> None:
    """
    Handle invoice.payment_failed event.

    Creates a billing record with failed status and updates subscription to past_due.

    T076: invoice.payment_failed webhook handler
    """
    invoice = event["data"]["object"]
    logger.info("Processing invoice.payment_failed:", val=invoice["id"])

    stripe_subscription_id = invoice.get("subscription")
    if not stripe_subscription_id:
        logger.info("Invoice is not for a subscription, skipping", val=invoice["id"])
        return

    # Find subscription by stripe_subscription_id using bypass context
    tenant_id = None
    user_id = None
    subscription_id = None

    async with job_bypass_context("Webhook subscription lookup") as db:
        result = await db.execute(
            select(crud_user_subscriptions.model)
            .where(crud_user_subscriptions.model.stripe_subscription_id == stripe_subscription_id)
            .with_for_update()
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
        description=f"Payment failed: {(invoice.get('last_finalization_error') or {}).get('message', 'Unknown error')}",
    )

    async with job_tenant_context(tenant_id) as db:
        # Check if billing record with stripe_invoice_id already exists to prevent duplicate inserts
        if await crud_billing_records.exists(
            db=db,
            stripe_invoice_id=invoice["id"],
            status="failed",
        ):
            logger.info(
                "Failed billing record with stripe_invoice_id already exists, skipping duplicate insert",
                stripe_invoice_id=invoice["id"],
            )
            return

        # ATOMIC LOCK: Re-verify and lock record within tenant context to prevent TOCTOU
        lock_result = await db.execute(
            select(crud_user_subscriptions.model)
            .where(crud_user_subscriptions.model.id == subscription_id)
            .with_for_update()
        )
        db_subscription_locked = lock_result.scalar_one_or_none()

        if not db_subscription_locked:
            logger.warning(
                "Subscription deleted in race condition gap, skipping update",
                stripe_subscription_id=stripe_subscription_id,
            )
            return

        await crud_billing_records.create(
            db=db,
            object=billing_record,
            commit=False,
        )

        # Update subscription to past_due
        update_data = UserSubscriptionUpdate(status="past_due")
        await crud_user_subscriptions.update(
            db=db,
            object=update_data,
            id=subscription_id,
            commit=False,
        )
        await db.commit()

    logger.info("Created failed billing record and updated subscription to past_due for invoice", val=invoice["id"])


async def handle_charge_refunded(event: stripe.Event) -> None:
    """
    Handle charge.refunded event.

    Creates a billing record with refunded status.

    T077: charge.refunded webhook handler
    """
    charge = event["data"]["object"]
    logger.info("Processing charge.refunded:", val=charge["id"])

    # Get the invoice if available
    stripe_invoice_id = charge.get("invoice")
    stripe_payment_intent_id = charge.get("payment_intent")

    if not stripe_invoice_id and not stripe_payment_intent_id:
        logger.info("Charge has no invoice or payment_intent, skipping", val=charge["id"])
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
            api_key = (
                settings.STRIPE_SECRET_KEY.get_secret_value()
                if hasattr(settings.STRIPE_SECRET_KEY, "get_secret_value")
                else getattr(settings, "STRIPE_SECRET_KEY", None)
            )
            invoice = await asyncio.to_thread(stripe.Invoice.retrieve, stripe_invoice_id, api_key=api_key)
            stripe_subscription_id = invoice.get("subscription")
        except stripe.error.StripeError as e:
            logger.error("Error retrieving invoice", error=e)
            raise ValueError(f"Failed to retrieve invoice {stripe_invoice_id} from Stripe") from e

    if not stripe_subscription_id:
        logger.warning("Could not determine subscription for charge", val=charge["id"])
        return

    async with job_bypass_context("Webhook subscription lookup") as db:
        result = await db.execute(
            select(crud_user_subscriptions.model)
            .where(crud_user_subscriptions.model.stripe_subscription_id == stripe_subscription_id)
            .with_for_update()
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

    async with job_tenant_context(tenant_id) as db:
        refunds = charge.get("refunds", {}).get("data", [])

        if not refunds:
            # Fallback if refunds array is missing
            refund_amount = charge.get("amount_refunded", 0)
            # Build a dedup filter that never matches a NULL payment_intent
            # (a None value would otherwise match unrelated records whose
            # stripe_payment_intent_id is also NULL). Prefer the payment
            # intent, falling back to the invoice id when it is absent.
            dedup_filter: dict[str, Any] = {
                "status": "refunded",
                "amount_cents": -refund_amount,
            }
            if stripe_payment_intent_id is not None:
                dedup_filter["stripe_payment_intent_id"] = stripe_payment_intent_id
            elif stripe_invoice_id is not None:
                dedup_filter["stripe_invoice_id"] = stripe_invoice_id
            if await crud_billing_records.exists(db=db, **dedup_filter):
                logger.info("Refund billing record already exists, skipping duplicate insert")
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
            await crud_billing_records.create(db=db, object=billing_record)
        else:
            for refund in refunds:
                refund_id = refund["id"]
                if await crud_billing_records.exists(
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
                await crud_billing_records.create(db=db, object=billing_record)

        await db.commit()

    logger.info("Created refunded billing record(s) for charge", val=charge["id"])
