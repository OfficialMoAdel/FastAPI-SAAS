"""Stripe client boundary — webhook signature verification.

The only place in the webhook pipeline that trusts the wire: it authenticates
the payload against the signing secret before any business logic runs.
"""

from typing import cast

import stripe

from src.app.core.logger import get_logger
from src.app.modules.billing.exceptions import WebhookVerificationError

logger = get_logger(__name__)


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
        WebhookVerificationError: If signature or payload verification fails
    """
    try:
        # Constructing event is CPU bound, but typically fast enough.
        # Ideally this would be run in a thread executor if payload is huge.
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
        return cast(stripe.Event, event)
    except ValueError as e:
        # Invalid payload
        logger.error("Invalid payload", error=str(e))
        raise WebhookVerificationError("Invalid payload") from e
    except stripe.SignatureVerificationError as e:
        # Invalid signature
        logger.error("Invalid signature", error=str(e))
        raise WebhookVerificationError("Invalid signature") from e
