"""Billing & Payments module.

Provides Stripe integration for payment processing, subscription management,
checkout flow, and billing records tracking.

Components are imported lazily to avoid circular imports.
Direct imports:
    from src.app.modules.billing.models import StripeSettings, SubscriptionPlan
    from src.app.modules.billing.router import router as billing_router
    from src.app.modules.billing.service import StripeSettingsService, ProductService
    from src.app.modules.billing.subscription_service import SubscriptionPlanService
    from src.app.modules.billing.checkout_service import CheckoutService
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
        from src.app.modules.billing.router import router

        return router
    if name == "StripeSettings":
        from src.app.modules.billing.models import StripeSettings

        return StripeSettings
    if name == "SubscriptionPlan":
        from src.app.modules.billing.models import SubscriptionPlan

        return SubscriptionPlan
    if name == "Product":
        from src.app.modules.billing.models import Product

        return Product
    if name == "Pricing":
        from src.app.modules.billing.models import Pricing

        return Pricing
    if name == "UserSubscription":
        from src.app.modules.billing.models import UserSubscription

        return UserSubscription
    if name == "BillingRecord":
        from src.app.modules.billing.models import BillingRecord

        return BillingRecord
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
