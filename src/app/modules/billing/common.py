"""Shared billing helpers.

Leaf module: imports only tenancy context, base exceptions, and models.
No imports from billing.service — subscription/checkout/gateway modules
depend on this instead of reaching back through the service facade.
"""

from src.app.modules.billing.models import Pricing, Product


def _require_active_tenant():
    """Get the current active tenant (delegates to the canonical seam)."""
    from src.app.core.tenancy.context import require_active_tenant

    return require_active_tenant()


def _pricing_tier_to_dict(pricing: Pricing) -> dict:
    """Convert Pricing ORM object to response dict directly.

    P2-5: Bypasses Pydantic model construction for faster serialization.
    """
    return {
        "id": pricing.id,
        "product_id": pricing.product_id,
        "name": pricing.name,
        "amount_cents": pricing.amount_cents,
        "interval": pricing.interval,
        "stripe_price_id": pricing.stripe_price_id,
        "currency": pricing.currency,
        "features": pricing.features,
        "is_active": pricing.is_active,
        "created_at": pricing.created_at,
        "updated_at": pricing.updated_at,
    }


def _product_to_dict(product: Product, pricing_tiers: list[Pricing]) -> dict:
    """Convert Product ORM object to response dict directly.

    P2-5: Bypasses Pydantic model construction for faster serialization.
    """
    return {
        "id": product.id,
        "tenant_id": product.tenant_id,
        "name": product.name,
        "description": product.description,
        "stripe_product_id": product.stripe_product_id,
        "is_active": product.is_active,
        "created_at": product.created_at,
        "updated_at": product.updated_at,
        "pricing_tiers": [_pricing_tier_to_dict(p) for p in pricing_tiers],
    }
