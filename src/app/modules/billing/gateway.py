"""Payment gateway integration layer (re-exported from src.app.clients.billing.stripe).

Provides PaymentGateway protocol and StripeGateway implementation with resilience (with_retry).
"""

from __future__ import annotations

import warnings

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.clients.billing.stripe import (
    PaymentGateway,
    StripeGateway,
    invalidate_stripe_api_key_cache,
)
from src.app.clients.billing.stripe import (
    get_payment_gateway as _get_payment_gateway,
)


def get_payment_gateway(db: AsyncSession | None = None, api_key: str | None = None) -> PaymentGateway:
    """Get PaymentGateway instance."""
    return _get_payment_gateway(db=db, api_key=api_key)


# Deprecated aliases for port formalization (PR-08 / Finding #9).
BillingGateway = PaymentGateway


def get_billing_gateway(db: AsyncSession | None = None) -> PaymentGateway:
    """Deprecated alias of get_payment_gateway (kept for back-compat)."""
    warnings.warn(
        "get_billing_gateway is deprecated; use get_payment_gateway instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return get_payment_gateway(db)


__all__ = [
    "BillingGateway",
    "PaymentGateway",
    "StripeGateway",
    "get_billing_gateway",
    "get_payment_gateway",
    "invalidate_stripe_api_key_cache",
]
