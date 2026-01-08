import logging
import stripe
from typing import Any, Dict
from fastapi import Request, HTTPException, status
from src.app.core.config import settings

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


async def handle_webhook_event(event: stripe.Event, background_tasks: Any = None) -> Dict[str, str]:
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

    # Routing logic (T049)
    # Placeholder for specific event handlers (will be implemented in future tasks)
    if event_type == "checkout.session.completed":
        # await handle_checkout_session_completed(event)
        pass
    elif event_type == "customer.subscription.updated":
        # await handle_subscription_updated(event)
        pass
    elif event_type == "customer.subscription.deleted":
        # await handle_subscription_deleted(event)
        pass
    elif event_type == "invoice.payment_succeeded":
        # await handle_invoice_payment_succeeded(event)
        pass
    elif event_type == "invoice.payment_failed":
        # await handle_invoice_payment_failed(event)
        pass
    else:
        logger.info(f"Unhandled event type: {event_type}")

    return {"status": "success"}
