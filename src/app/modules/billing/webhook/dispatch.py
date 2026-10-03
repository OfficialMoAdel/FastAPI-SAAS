"""Webhook event dispatch — registry-based routing (OCP).

Adding a new Stripe event type means registering one entry in
``EVENT_HANDLERS``; the dispatcher itself never changes.
"""


from src.app.clients.billing import StripeEvent
from src.app.modules.billing import webhook as _pkg
from src.app.modules.billing.webhook.idempotency import (
    EventLockResult,
    _release_webhook_event_lock,
)

# Event-type → handler-name registry. Replaces the former if/elif chain so new
# event types are added declaratively (Open/Closed Principle). Handlers are
# resolved through the package namespace at dispatch time (late binding), so
# runtime patching/mutation of ``billing.webhook.<handler>`` keeps working.
EVENT_HANDLERS: dict[str, str] = {
    "checkout.session.completed": "handle_checkout_session_completed",
    "customer.subscription.updated": "handle_subscription_updated",
    "customer.subscription.deleted": "handle_subscription_deleted",
    "invoice.payment_succeeded": "handle_invoice_payment_succeeded",
    "invoice.payment_failed": "handle_invoice_payment_failed",
    "charge.refunded": "handle_charge_refunded",
}


async def handle_webhook_event(event: StripeEvent) -> dict[str, str]:
    """
    Route and handle specific Stripe webhook events.

    Implements idempotency to prevent duplicate processing of the same event.
    Stripe guarantees at-least-once delivery, so the same event may be delivered
    multiple times. We track processed event IDs to ensure each event is handled
    exactly once.

    Args:
        event: The Stripe event object

    Returns:
        Status dict
    """
    event_type = event["type"]
    event_id = event["id"]
    _pkg.logger.info("Received Stripe webhook event", event_type=event_type, event_id=event_id)

    # R2-3: resolve the handler BEFORE touching Redis. Unhandled event types
    # return here costing zero lock ops and leaving no marker — so a type we
    # start handling later will still process its (re)deliveries instead of
    # tripping over a durable "processed" marker written for a no-op.
    handler_name = EVENT_HANDLERS.get(event_type)
    if handler_name is None:
        _pkg.logger.info("Unhandled event type:", event_type=event_type)
        return {"status": "unhandled"}

    # B4: consult the DURABLE processed marker BEFORE the processing lock.
    # The Redis SET NX lock is only a 300s in-flight guard; a Stripe
    # redelivery arriving after that window must be rejected by the terminal
    # marker instead of re-executing the handler.
    if await _pkg._is_event_processed(event_id):
        _pkg.logger.info("Event durably processed, skipping redelivery", event_id=event_id)
        return {"status": "already_processed"}

    # IDEMPOTENCY CHECK: acquire processing rights.
    # Uses SET NX for atomic check-and-mark, eliminating TOCTOU race.
    #
    # The result is three-valued because "another delivery is working on it"
    # and "it is already finished" need OPPOSITE HTTP answers. A bare bool
    # collapsed both into "already_processed", which `ingest_webhook` then
    # flattened into a 200 for everything — so a concurrent redelivery was
    # acked "handled" while the in-flight original had not yet succeeded. Had
    # that original then failed, Stripe had already been told "success", never
    # retried, and the event was silently lost.
    lock_result, lock_token = await _pkg._try_acquire_event_lock(event_id)
    if lock_result is not EventLockResult.ACQUIRED:
        _pkg.logger.info(
            "Event not acquirable, reporting disposition",
            event_id=event_id,
            event_type=event_type,
            lock_result=lock_result.value,
        )
        return {"status": lock_result.value}

    try:
        # Handlers manage their own DB sessions and RLS context.
        # Late-bound through the package namespace (see EVENT_HANDLERS note).
        await getattr(_pkg, handler_name)(event)

        # B4: write the durable terminal marker (Redis 7d TTL + WebhookEvent
        # row stamped completed) so late Stripe redeliveries are rejected above.
        from src.app.modules.billing.webhook.idempotency import mark_event_processed

        # Pass our token so the completion stamp cannot land on a claim a
        # concurrent delivery has since reclaimed for itself.
        await mark_event_processed(event_id, lock_token=lock_token)

    except Exception as e:
        _pkg.logger.error(
            f"Error handling webhook event (ID: {event_id}):",
            event_type=event_type,
            event_id=event_id,
            error=e,
        )

        # S-04: pass our lock token so the release is a compare-and-delete and
        # cannot erase a terminal marker written by a delivery that succeeded.
        await _release_webhook_event_lock(event_id, lock_token=lock_token)

        raise

    # Success path: the processing lock acquired in _try_acquire_event_lock
    # (bounded TTL) already marks the event as in-flight. On failure the
    # lock is released (see _release_webhook_event_lock) so Stripe can retry.
    return {"status": "success"}
