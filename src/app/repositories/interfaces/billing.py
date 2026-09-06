"""Billing repository protocol."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID


class BillingRepository(Protocol):
    """Protocol for billing records, subscriptions, and tenant settings persistence."""

    async def get_subscription(self, tenant_id: UUID | int | str) -> dict[str, Any] | None:
        """Fetch active user subscription for a tenant."""
        ...

    async def get_stripe_settings(self, tenant_id: UUID | int | str) -> dict[str, Any] | None:
        """Fetch tenant Stripe configuration."""
        ...

    async def get_product(self, product_id: int, tenant_id: UUID | int | str | None = None) -> dict[str, Any] | None:
        """Fetch product by ID."""
        ...

    async def get_pricing(self, pricing_id: int) -> dict[str, Any] | None:
        """Fetch pricing tier by ID."""
        ...

    async def save_stripe_settings(self, tenant_id: UUID | int | str, data: Any) -> dict[str, Any]:
        """Save or update Stripe settings for a tenant."""
        ...

    async def save_subscription(self, data: Any) -> dict[str, Any]:
        """Save or update subscription record."""
        ...


__all__ = ["BillingRepository"]
