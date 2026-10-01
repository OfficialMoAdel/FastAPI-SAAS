"""Stripe client boundary — webhook signature verification.

The only place in the webhook pipeline that trusts the wire: it authenticates
the payload against the signing secret before any business logic runs.

INBOUND BOUNDARY: this module and ``clients/billing/stripe.py`` are the only
two places the Stripe SDK may be imported on the request path. Everything
downstream spells the event type as ``StripeEvent``, imported from
``src.app.clients.billing`` — see the alias's definition for why the type is
aliased at the adapter rather than spelled as ``stripe.Event`` at each site.
"""

from typing import cast

import stripe

from src.app.clients.billing import StripeEvent
from src.app.core.logger import get_logger
from src.app.modules.billing.exceptions import WebhookVerificationError

logger = get_logger(__name__)


def verify_webhook_signature(payload: bytes, sig_header: str, secret: str) -> StripeEvent:
    """
    Verify the Stripe webhook signature and construct the event.

    Args:
        payload: Raw request body bytes
        sig_header: value of Stripe-Signature header
        secret: Webhook signing secret

    Returns:
        The verified event object (see ``StripeEvent``)

    Raises:
        WebhookVerificationError: If signature or payload verification fails
    """
    try:
        # Constructing event is CPU bound, but typically fast enough.
        # Ideally this would be run in a thread executor if payload is huge.
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
        return cast(StripeEvent, event)
    except ValueError as e:
        # Invalid payload
        logger.error("Invalid payload", error=str(e))
        raise WebhookVerificationError("Invalid payload") from e
    except stripe.SignatureVerificationError as e:
        # Invalid signature
        logger.error("Invalid signature", error=str(e))
        raise WebhookVerificationError("Invalid signature") from e

