"""Billing and payment integration clients.

``StripeEvent`` is re-exported here because the INBOUND Stripe surface
(webhook signature verification and the event object handlers receive) is
part of this adapter's contract too: feature code spells the event type
through this package rather than importing the vendor SDK.
"""

from src.app.clients.billing.stripe import (
    PaymentGateway,
    StripeEvent,
    StripeGateway,
    get_payment_gateway,
)

__all__ = ["PaymentGateway", "StripeEvent", "StripeGateway", "get_payment_gateway"]
