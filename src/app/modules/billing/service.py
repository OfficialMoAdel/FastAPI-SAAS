"""Billing & Payments service layer.

Business logic for Stripe integration, subscription management,
products, pricing, and billing records.
"""

__all__ = [
    # CRUD
    "crud_billing_records",
    "crud_pricing",
    "crud_products",
    "crud_stripe_settings",
    "crud_subscription_plans",
    "crud_user_subscriptions",
    # Core & Tenant
    "get_current_tenant",
    "_require_active_tenant",
    "settings",
    # Services
    "StripeSettingsService",
    "ProductService",
    "PricingService",
    "SubscriptionPlanService",
    "UserSubscriptionService",
    "BillingRecordService",
    "CheckoutService",
    # Service Getters
    "get_stripe_settings_service",
    "get_product_service",
    "get_pricing_service",
    "get_subscription_plan_service",
    "get_user_subscription_service",
    "get_billing_record_service",
    "get_checkout_service",
    # Helpers
    "_subscription_plan_to_dict",
    # Models
    "BillingRecord",
    "Pricing",
    "Product",
    "StripeSettings",
    "SubscriptionPlan",
    "UserSubscription",
    # Schemas
    "BillingInterval",
    "BillingRecordCreate",
    "BillingRecordCreateInternal",
    "BillingRecordDelete",
    "BillingRecordList",
    "BillingRecordPage",
    "BillingRecordRead",
    "BillingRecordStatus",
    "BillingRecordUpdate",
    "CheckoutCreate",
    "CheckoutSession",
    "PaymentError",
    "PortalResponse",
    "PricingCreate",
    "PricingCreateInternal",
    "PricingDelete",
    "PricingInterval",
    "PricingRead",
    "PricingUpdate",
    "ProductCreate",
    "ProductCreateInternal",
    "ProductDelete",
    "ProductList",
    "ProductPage",
    "ProductRead",
    "ProductReadWithPricing",
    "ProductUpdate",
    "StripeSettingsCreate",
    "StripeSettingsCreateInternal",
    "StripeSettingsDelete",
    "StripeSettingsRead",
    "StripeSettingsReadFull",
    "StripeSettingsUpdate",
    "SubscriptionPlanCreate",
    "SubscriptionPlanCreateInternal",
    "SubscriptionPlanDelete",
    "SubscriptionPlanList",
    "SubscriptionPlanPage",
    "SubscriptionPlanRead",
    "SubscriptionPlanUpdate",
    "SubscriptionStatus",
    "UserSubscriptionCreate",
    "UserSubscriptionCreateInternal",
    "UserSubscriptionDelete",
    "UserSubscriptionRead",
    "UserSubscriptionReadWithPlan",
    "UserSubscriptionUpdate",
    "WebhookResponse",
]

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.security.encryption import CredentialEncryption
from src.app.core.tenancy.context import get_current_tenant
from src.app.modules._base.exceptions import (
    ModelNotFoundError,
    TenantContextError,
)
from src.app.modules._base.service import BaseService

logger = get_logger(__name__)

from src.app.modules.billing.crud import (
    crud_billing_records,
    crud_pricing,
    crud_products,
    crud_stripe_settings,
    crud_subscription_plans,
    crud_user_subscriptions,
)
from src.app.modules.billing.models import (
    BillingRecord,
    Pricing,
    Product,
    StripeSettings,
    SubscriptionPlan,
    UserSubscription,
)
from src.app.modules.billing.schemas import (
    BillingInterval,
    BillingRecordCreate,
    BillingRecordCreateInternal,
    BillingRecordDelete,
    BillingRecordList,
    BillingRecordPage,
    BillingRecordRead,
    BillingRecordStatus,
    BillingRecordUpdate,
    CheckoutCreate,
    CheckoutSession,
    PaymentError,
    PortalResponse,
    PricingCreate,
    PricingCreateInternal,
    PricingDelete,
    PricingInterval,
    PricingRead,
    PricingUpdate,
    ProductCreate,
    ProductCreateInternal,
    ProductDelete,
    ProductList,
    ProductPage,
    ProductRead,
    ProductReadWithPricing,
    ProductUpdate,
    StripeSettingsCreate,
    StripeSettingsCreateInternal,
    StripeSettingsDelete,
    StripeSettingsRead,
    StripeSettingsReadFull,
    StripeSettingsUpdate,
    SubscriptionPlanCreate,
    SubscriptionPlanCreateInternal,
    SubscriptionPlanDelete,
    SubscriptionPlanList,
    SubscriptionPlanPage,
    SubscriptionPlanRead,
    SubscriptionPlanUpdate,
    SubscriptionStatus,
    UserSubscriptionCreate,
    UserSubscriptionCreateInternal,
    UserSubscriptionDelete,
    UserSubscriptionRead,
    UserSubscriptionReadWithPlan,
    UserSubscriptionUpdate,
    WebhookResponse,
)


def _require_active_tenant():
    """Get the current active tenant or raise 403.

    Raises:
        HTTPException: If tenant is not set or not active.
    """
    tenant = get_current_tenant()
    if tenant is None:
        raise TenantContextError("No tenant context available")
    if not tenant.is_active:
        raise TenantContextError("Tenant is not active")
    return tenant


def _pricing_tier_to_dict(pricing: Pricing) -> dict[str, Any]:
    """Convert Pricing ORM object to response dict directly.

    P2-5: Bypasses Pydantic model construction for faster serialization.
    Fields match Pricing schema: id, product_id, name, amount_cents, interval,
    stripe_price_id, currency, features, is_active, created_at, updated_at.
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


def _product_to_dict(product: Product, pricing_tiers: list[Pricing]) -> dict[str, Any]:
    """Convert Product ORM object to response dict directly.

    P2-5: Bypasses Pydantic model construction for faster serialization.
    Fields match Product schema with nested pricing tiers.
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


class StripeSettingsService(BaseService[StripeSettings]):
    """Service for managing tenant Stripe settings."""

    def __init__(self, db: AsyncSession):
        super().__init__(StripeSettings, db)
        self._encryption = CredentialEncryption()

    async def get_settings(self) -> dict[str, Any] | None:
        """Get Stripe settings for the current tenant."""
        tenant = get_current_tenant()
        if tenant is None:
            return None
        if not tenant.is_active:
            raise TenantContextError("Tenant is not active")
        result = await crud_stripe_settings.get(db=self.db, tenant_id=tenant.id)
        return dict(result) if result else None

    async def update_settings(self, data: StripeSettingsUpdate) -> dict[str, Any]:
        """Update Stripe settings for the current tenant."""
        tenant = _require_active_tenant()

        stripe_settings_record = await crud_stripe_settings.get(db=self.db, tenant_id=tenant.id)

        update_data = data.model_dump(exclude_unset=True)

        # Encrypt the secret key if provided
        if "stripe_secret_key" in update_data and update_data["stripe_secret_key"]:
            encrypted = self._encryption.encrypt_column(update_data.pop("stripe_secret_key"))
            # encrypt_column returns a string, store as-is (not .encode())
            update_data["encrypted_secret_key"] = encrypted

        # Encrypt the webhook secret if provided
        if "webhook_secret" in update_data and update_data["webhook_secret"]:
            encrypted_ws = self._encryption.encrypt_column(update_data.pop("webhook_secret"))
            update_data["encrypted_webhook_secret"] = encrypted_ws

        update_data["updated_at"] = datetime.now(UTC)

        if stripe_settings_record is None:
            # Create new settings
            update_data["tenant_id"] = tenant.id
            # Create object must be a Pydantic model for FastCRUD
            create_obj = StripeSettingsCreateInternal(**update_data)
            create_result = await crud_stripe_settings.create(
                db=self.db,
                object=create_obj,
                schema_to_select=StripeSettingsRead,
            )
            return dict(create_result) if create_result else {}
        else:
            await crud_stripe_settings.update(db=self.db, object=update_data, id=stripe_settings_record["id"])
            stripe_settings_record.update(update_data)
            return dict(stripe_settings_record)

    async def get_webhook_secret(self, tenant_id: UUID) -> str | None:
        """Get the decrypted Stripe webhook secret for a specific tenant."""
        record = await crud_stripe_settings.get(db=self.db, tenant_id=tenant_id)
        if not record or not record.get("encrypted_webhook_secret"):
            return None
        return self._encryption.decrypt_column(record["encrypted_webhook_secret"])


class ProductService(BaseService[Product]):
    """Service for managing products."""

    def __init__(self, db: AsyncSession):
        super().__init__(Product, db)

    async def create_product(self, data: ProductCreate) -> dict[str, Any]:
        """Create a new product."""
        tenant = _require_active_tenant()

        product_data = data.model_dump()
        product_data["tenant_id"] = tenant.id
        create_obj = ProductCreateInternal(**product_data)
        return cast(
            dict[str, Any], await crud_products.create(db=self.db, object=create_obj, schema_to_select=ProductRead)
        )

    async def get_product(self, product_id: int) -> dict[str, Any]:
        """Get a product by ID."""
        tenant = get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("Product", product_id)

        product = await crud_products.get(db=self.db, id=product_id, tenant_id=tenant.id)
        if product is None:
            raise ModelNotFoundError("Product", product_id)

        return product

    async def get_products(self, page: int = 1, limit: int = 20, is_active: bool | None = None) -> dict[str, Any]:
        """Get paginated products (tenant-scoped)."""
        tenant = get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("Product", "list")
        offset = (page - 1) * limit
        filters: dict[str, Any] = {}
        if is_active is not None:
            filters["is_active"] = is_active
        return await crud_products.get_multi(
            db=self.db, offset=offset, limit=limit, schema_to_select=ProductRead, tenant_id=tenant.id, **filters
        )

    async def update_product(self, product_id: int, data: ProductUpdate) -> dict[str, Any]:
        """Update a product."""
        tenant = get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("Product", product_id)

        product = await crud_products.get(db=self.db, id=product_id, tenant_id=tenant.id)
        if product is None:
            raise ModelNotFoundError("Product", product_id)

        update_data = data.model_dump(exclude_unset=True)
        update_data["updated_at"] = datetime.now(UTC)
        await crud_products.update(db=self.db, object=update_data, id=product_id, tenant_id=tenant.id)
        result = await crud_products.get(db=self.db, id=product_id, tenant_id=tenant.id)
        return dict(result) if result else {}

    async def deactivate_product(self, product_id: int) -> dict[str, Any]:
        """Deactivate a product."""
        tenant = get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("Product", product_id)

        product = await crud_products.get(db=self.db, id=product_id, tenant_id=tenant.id)
        if product is None:
            raise ModelNotFoundError("Product", product_id)

        update_data = {"is_active": False, "updated_at": datetime.now(UTC)}
        await crud_products.update(db=self.db, object=update_data, id=product_id, tenant_id=tenant.id)
        result = await crud_products.get(db=self.db, id=product_id, tenant_id=tenant.id)
        return dict(result) if result else {}

    async def get_product_with_pricing(self, product_id: int) -> dict[str, Any]:
        """Get a product by ID with its pricing tiers.

        Uses selectinload to fetch product and pricing in a single query,
        avoiding N+1 query issues.
        """
        tenant = get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("Product", product_id)

        stmt = (
            select(Product)
            .options(selectinload(Product.pricing_tiers))
            .where(Product.id == product_id, Product.tenant_id == tenant.id)
        )
        result = await self.db.execute(stmt)
        product = result.scalar_one_or_none()

        if product is None:
            raise ModelNotFoundError("Product", product_id)

        # P2-5: Always use optimized dict construction
        return _product_to_dict(product, product.pricing_tiers)

    async def add_pricing(
        self,
        product_id: int,
        data: PricingCreate,
    ) -> dict[str, Any]:
        """Add a pricing tier to a product."""
        tenant = get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("Product", product_id)

        product = await crud_products.get(db=self.db, id=product_id, tenant_id=tenant.id)
        if product is None:
            raise ModelNotFoundError("Product", product_id)

        pricing_data = data.model_dump()
        pricing_data["product_id"] = product_id
        create_obj = PricingCreateInternal(**pricing_data)
        return cast(
            dict[str, Any], await crud_pricing.create(db=self.db, object=create_obj, schema_to_select=PricingRead)
        )


class PricingService(BaseService[Pricing]):
    """Service for managing pricing tiers."""

    def __init__(self, db: AsyncSession):
        super().__init__(Pricing, db)

    async def create_pricing(self, product_id: int, data: PricingCreate) -> dict[str, Any]:
        """Create a pricing tier for a product."""
        tenant = get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("Product", product_id)

        product = await crud_products.get(db=self.db, id=product_id, tenant_id=tenant.id)
        if product is None:
            raise ModelNotFoundError("Product", product_id)

        pricing_data = data.model_dump()
        pricing_data["product_id"] = product_id
        create_obj = PricingCreateInternal(**pricing_data)
        return cast(
            dict[str, Any], await crud_pricing.create(db=self.db, object=create_obj, schema_to_select=PricingRead)
        )

    async def get_pricing(self, pricing_id: int) -> dict[str, Any]:
        """Get a pricing tier by ID."""
        pricing = await crud_pricing.get(db=self.db, id=pricing_id)
        if pricing is None:
            raise ModelNotFoundError("Pricing", pricing_id)

        # SECURITY FIX: Verify pricing's parent product belongs to current tenant.
        if pricing is not None:
            tenant = get_current_tenant()
            if tenant is None:
                raise ModelNotFoundError("Pricing", pricing_id)
            # FastCRUD might return dict or object
            prod_id = pricing.get("product_id") if isinstance(pricing, dict) else pricing.product_id
            product = await crud_products.get(db=self.db, id=prod_id, tenant_id=tenant.id)
            if product is None:
                raise ModelNotFoundError("Pricing", pricing_id)

        return pricing

    async def get_product_pricing(self, product_id: int) -> dict[str, Any]:
        """Get all pricing tiers for a product (tenant-scoped via parent product)."""
        tenant = get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("Product", product_id)
        product = await crud_products.get(db=self.db, id=product_id, tenant_id=tenant.id)
        if product is None:
            raise ModelNotFoundError("Product", product_id)
        return await crud_pricing.get_multi(db=self.db, product_id=product_id, schema_to_select=PricingRead)

    async def list_prices(
        self,
        page: int = 1,
        limit: int = 100,
        product_id: int | None = None,
        is_active: bool | None = None,
        tenant_id: int | str | None = None,  # deprecated: ignored - tenant taken from context
    ) -> dict[str, Any]:
        """List prices with optional filtering (tenant-scoped)."""
        tenant = get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("Pricing", "list")
        if tenant_id is not None and str(tenant_id) != str(tenant.id):
            from src.app.core.logger import get_logger as _get_logger
            _get_logger(__name__).warning("tenant_id_override_blocked_in_list_prices", requested=str(tenant_id), context=str(tenant.id))  # noqa: E501
        if product_id is not None:
            product = await crud_products.get(db=self.db, id=product_id, tenant_id=tenant.id)
            if product is None:
                raise ModelNotFoundError("Product", product_id)
        offset = (page - 1) * limit
        filters: dict[str, Any] = {}
        if product_id is not None:
            filters["product_id"] = product_id
        if is_active is not None:
            filters["is_active"] = is_active
        if product_id is None:
            # Pricing has no tenant_id; must filter via tenant-owned products.
            # Pre-fetch tenant product IDs then use DB-side IN filter to avoid
            # loading and filtering in Python (which breaks pagination/total).
            prod_rows = await crud_products.get_multi(db=self.db, tenant_id=tenant.id, limit=1000, offset=0, schema_to_select=ProductRead)  # noqa: E501
            ids = [r["id"] if isinstance(r, dict) else r.id for r in prod_rows.get("data", [])] if isinstance(prod_rows, dict) else []  # noqa: E501
            if not ids:
                return {"data": [], "total_count": 0}
            # Pass tenant product IDs to crud for proper WHERE product_id IN (...) so DB handles pagination/count
            return await crud_pricing.get_multi(db=self.db, offset=offset, limit=limit, schema_to_select=PricingRead, product_ids=ids, **filters)  # noqa: E501
        return await crud_pricing.get_multi(
            db=self.db,
            offset=offset,
            limit=limit,
            schema_to_select=PricingRead,
            **filters,
        )

    async def update_pricing(self, pricing_id: int, data: PricingUpdate) -> dict[str, Any]:
        """Update a pricing tier.

        P2-3: Uses session refresh instead of second DB roundtrip.
        """
        pricing = await self.db.get(Pricing, pricing_id)
        if pricing is None:
            raise ModelNotFoundError("Pricing", pricing_id)

        # SECURITY FIX: Verify pricing's parent product belongs to current tenant.
        tenant = get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("Pricing", pricing_id)
        product = await crud_products.get(db=self.db, id=pricing.product_id, tenant_id=tenant.id)
        if product is None:
            raise ModelNotFoundError("Pricing", pricing_id)

        update_data = data.model_dump(exclude_unset=True)
        update_data["updated_at"] = datetime.now(UTC)

        # Apply updates to ORM object
        for key, value in update_data.items():
            setattr(pricing, key, value)

        await self.db.commit()

        return PricingRead.model_validate(pricing).model_dump()


def get_stripe_settings_service(db: AsyncSession) -> StripeSettingsService:
    """Get StripeSettingsService instance."""
    return StripeSettingsService(db)


def get_product_service(db: AsyncSession) -> ProductService:
    """Get ProductService instance."""
    return ProductService(db)


def get_pricing_service(db: AsyncSession) -> PricingService:
    """Get PricingService instance."""
    return PricingService(db)


from src.app.modules.billing.checkout_service import (  # noqa: E402, F401
    CheckoutService,
    get_checkout_service,
)
from src.app.modules.billing.subscription_service import (  # noqa: E402, F401
    BillingRecordService,
    SubscriptionPlanService,
    UserSubscriptionService,
    _subscription_plan_to_dict,
    get_billing_record_service,
    get_subscription_plan_service,
    get_user_subscription_service,
)
