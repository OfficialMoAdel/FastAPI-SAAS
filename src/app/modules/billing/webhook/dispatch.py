"""Webhook event dispatch — registry-based routing (OCP).

Adding a new Stripe event type means registering one entry in
``EVENT_HANDLERS``; the dispatcher itself never changes.
"""


import stripe

from src.app.modules.billing import webhook as _pkg
from src.app.modules.billing.webhook.idempotency import (
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


async def handle_webhook_event(event: stripe.Event) -> dict[str, str]:
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

    # IDEMPOTENCY CHECK: Atomically acquire processing lock
    # Uses SET NX for atomic check-and-mark, eliminating TOCTOU race
    if not await _pkg._try_acquire_event_lock(event_id):
        _pkg.logger.info("Event already processed, skipping", event_id=event_id)
        return {"status": "already_processed"}

    handler_name = EVENT_HANDLERS.get(event_type)
    if handler_name is None:
        _pkg.logger.info("Unhandled event type:", event_type=event_type)
        # Lock already acquired in _try_acquire_event_lock; no extra mark needed.
        return {"status": "unhandled"}

    try:
        # Handlers manage their own DB sessions and RLS context.
        # Late-bound through the package namespace (see EVENT_HANDLERS note).
        await getattr(_pkg, handler_name)(event)

        # Lock was already acquired in _try_acquire_event_lock
        # No need to mark again - atomic SET NX handles this

    except Exception as e:
        _pkg.logger.error(
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
