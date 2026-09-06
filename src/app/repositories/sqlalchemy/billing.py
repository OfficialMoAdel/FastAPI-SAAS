"""SQLAlchemy implementation of BillingRepository."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.billing.crud import (
    crud_pricing,
    crud_products,
    crud_stripe_settings,
    crud_user_subscriptions,
)
from src.app.modules.billing.schemas import ProductRead, StripeSettingsRead
from src.app.repositories.interfaces.billing import BillingRepository


class SqlAlchemyBillingRepository(BillingRepository):
    """SQLAlchemy backed implementation of BillingRepository."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_subscription(self, tenant_id: UUID | int | str) -> dict[str, Any] | None:
        result = await crud_user_subscriptions.get(db=self.db, tenant_id=tenant_id)
        return dict(result) if result else None

    async def get_stripe_settings(self, tenant_id: UUID | int | str) -> dict[str, Any] | None:
        result = await crud_stripe_settings.get(
            db=self.db, tenant_id=tenant_id, schema_to_select=StripeSettingsRead
        )
        return dict(result) if result else None

    async def get_product(self, product_id: int, tenant_id: UUID | int | str | None = None) -> dict[str, Any] | None:
        kwargs: dict[str, Any] = {"db": self.db, "id": product_id, "schema_to_select": ProductRead}
        if tenant_id is not None:
            kwargs["tenant_id"] = tenant_id
        result = await crud_products.get(**kwargs)
        return dict(result) if result else None

    async def get_pricing(self, pricing_id: int) -> dict[str, Any] | None:
        result = await crud_pricing.get(db=self.db, id=pricing_id)
        return dict(result) if result else None

    async def save_stripe_settings(self, tenant_id: UUID | int | str, data: Any) -> dict[str, Any]:
        existing = await crud_stripe_settings.get(db=self.db, tenant_id=tenant_id)
        if existing:
            await crud_stripe_settings.update(db=self.db, id=existing["id"], object=data)
            return {**existing, **data}
        result = await crud_stripe_settings.create(db=self.db, object=data)
        return dict(result) if result else {}

    async def save_subscription(self, data: Any) -> dict[str, Any]:
        result = await crud_user_subscriptions.create(db=self.db, object=data)
        return dict(result) if result else {}


__all__ = ["SqlAlchemyBillingRepository"]
