"""Billing & Payments module.

Provides Stripe integration for payment processing, subscription management,
checkout flow, and billing records tracking.

Components are imported lazily to avoid circular imports.
Direct imports:
    from src.app.modules.billing.models import StripeSettings, SubscriptionPlan
    from src.app.modules.billing.service import billing_service
    from src.app.modules.billing.router import router as billing_router
"""

__all__ = [
    "router",
    "StripeSettings",
    "SubscriptionPlan",
    "Product",
    "Pricing",
    "UserSubscription",
    "BillingRecord",
]


def __getattr__(name: str):
    """Lazy import to avoid circular imports."""
    if name == "router":
        from .router import router

        return router
    if name == "StripeSettings":
        from .models import StripeSettings

        return StripeSettings
    if name == "SubscriptionPlan":
        from .models import SubscriptionPlan

        return SubscriptionPlan
    if name == "Product":
        from .models import Product

        return Product
    if name == "Pricing":
        from .models import Pricing

        return Pricing
    if name == "UserSubscription":
        from .models import UserSubscription

        return UserSubscription
    if name == "BillingRecord":
        from .models import BillingRecord

        return BillingRecord
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
