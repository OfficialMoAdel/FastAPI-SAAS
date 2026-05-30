"""Billing & Payments service layer.

Business logic for Stripe integration, subscription management,
products, pricing, and billing records.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any, cast

import stripe
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.core.logger import get_logger
from src.app.core.security.encryption import CredentialEncryption
from src.app.core.tenancy.context import get_current_tenant
from src.app.modules._base.exceptions import ModelNotFoundError
from src.app.modules._base.service import BaseService

logger = get_logger(__name__)

from src.app.core.config import settings
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
    BillingRecordCreate,
    BillingRecordCreateInternal,
    BillingRecordRead,
    CheckoutCreate,
    CheckoutSession,
    PricingCreate,
    PricingCreateInternal,
    PricingRead,
    PricingUpdate,
    ProductCreate,
    ProductCreateInternal,
    ProductRead,
    ProductUpdate,
    StripeSettingsCreateInternal,
    StripeSettingsRead,
    StripeSettingsUpdate,
    SubscriptionPlanCreate,
    SubscriptionPlanCreateInternal,
    SubscriptionPlanRead,
    SubscriptionPlanUpdate,
    UserSubscriptionCreate,
    UserSubscriptionCreateInternal,
    UserSubscriptionRead,
    UserSubscriptionUpdate,
)


def _require_active_tenant():
    """Get the current active tenant or raise 403.

    Raises:
        HTTPException: If tenant is not set or not active.
    """
    tenant = get_current_tenant()
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tenant context available")
    if not tenant.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant is not active")
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
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant is not active")
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
            # Update existing settings
            await crud_stripe_settings.update(db=self.db, object=update_data, id=stripe_settings_record["id"])
            update_result = await crud_stripe_settings.get(db=self.db, id=stripe_settings_record["id"])
            return dict(update_result) if update_result else {}


def _subscription_plan_to_dict(plan: SubscriptionPlan) -> dict[str, Any]:
    """Convert SubscriptionPlan ORM object to response dict directly."""
    return {
        "id": plan.id,
        "tenant_id": plan.tenant_id,
        "name": plan.name,
        "description": plan.description,
        "tier_name": getattr(plan, "tier_name", None),
        "sort_order": plan.sort_order,
        "stripe_price_id_monthly": plan.stripe_price_id_monthly,
        "stripe_price_id_yearly": plan.stripe_price_id_yearly,
        "features": plan.features,
        "is_active": plan.is_active,
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
    }


class SubscriptionPlanService(BaseService[SubscriptionPlan]):
    """Service for managing subscription plans."""

    def __init__(self, db: AsyncSession):
        super().__init__(SubscriptionPlan, db)

    async def create_plan(self, data: SubscriptionPlanCreate) -> dict[str, Any]:
        """Create a new subscription plan."""
        tenant = _require_active_tenant()

        plan_data = data.model_dump()
        plan_data["tenant_id"] = tenant.id
        create_obj = SubscriptionPlanCreateInternal(**plan_data)
        return cast(
            dict[str, Any],
            await crud_subscription_plans.create(db=self.db, object=create_obj, schema_to_select=SubscriptionPlanRead),
        )

    async def get_plan(self, plan_id: int) -> dict[str, Any]:
        """Get a subscription plan by ID."""
        plan = await crud_subscription_plans.get(db=self.db, id=plan_id)
        if plan is None:
            raise ModelNotFoundError("SubscriptionPlan", plan_id)

        # SECURITY FIX (H-08): tenant authorization on plan access
        tenant = get_current_tenant()
        if tenant and plan.get("tenant_id") != tenant.id:
            raise ModelNotFoundError("SubscriptionPlan", plan_id)

        return plan

    async def get_plans(self, page: int = 1, limit: int = 20, is_active: bool | None = None) -> dict[str, Any]:
        """Get paginated subscription plans."""
        offset = (page - 1) * limit
        filters: dict[str, Any] = {}
        if is_active is not None:
            filters["is_active"] = is_active

        # T063: Sort by sort_order
        return await crud_subscription_plans.get_multi(
            db=self.db,
            offset=offset,
            limit=limit,
            sort_columns=["sort_order"],
            schema_to_select=SubscriptionPlanRead,
            **filters,
        )

    async def update_plan(self, plan_id: int, data: SubscriptionPlanUpdate) -> dict[str, Any]:
        """Update a subscription plan.

        P2-3: Uses session refresh instead of second DB roundtrip.
        """
        # Fetch ORM object directly for in-place update
        plan = await self.db.get(SubscriptionPlan, plan_id)
        if plan is None:
            raise ModelNotFoundError("SubscriptionPlan", plan_id)

        # SECURITY FIX (H-08): tenant authorization on plan access
        tenant = get_current_tenant()
        if tenant and plan.tenant_id != tenant.id:
            raise ModelNotFoundError("SubscriptionPlan", plan_id)

        update_data = data.model_dump(exclude_unset=True)
        update_data["updated_at"] = datetime.now(UTC)

        # Apply updates to ORM object
        for key, value in update_data.items():
            setattr(plan, key, value)

        await self.db.commit()
        await self.db.refresh(plan)

        return _subscription_plan_to_dict(plan)

    async def delete_plan(self, plan_id: int) -> dict[str, str]:
        """Soft delete a subscription plan."""
        plan = await crud_subscription_plans.get(db=self.db, id=plan_id)
        if plan is None:
            raise ModelNotFoundError("SubscriptionPlan", plan_id)

        # SECURITY FIX (H-08): tenant authorization on plan access
        tenant = get_current_tenant()
        if tenant and plan.get("tenant_id") != tenant.id:
            raise ModelNotFoundError("SubscriptionPlan", plan_id)

        await crud_subscription_plans.update(
            db=self.db,
            object={"is_active": False, "updated_at": datetime.now(UTC)},
            id=plan_id,
        )
        return {"message": "Subscription plan deactivated"}


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
        product = await crud_products.get(db=self.db, id=product_id)
        if product is None:
            raise ModelNotFoundError("Product", product_id)

        # SECURITY FIX: Enforce tenant isolation (same pattern as get_plan).
        # Without this check, any authenticated user can read any tenant's product
        # by brute-forcing product IDs (IDOR vulnerability).
        tenant = get_current_tenant()
        if tenant and product.get("tenant_id") != tenant.id:
            raise ModelNotFoundError("Product", product_id)

        return product

    async def get_products(self, page: int = 1, limit: int = 20, is_active: bool | None = None) -> dict[str, Any]:
        """Get paginated products."""
        offset = (page - 1) * limit
        filters: dict[str, Any] = {}
        if is_active is not None:
            filters["is_active"] = is_active
        return await crud_products.get_multi(
            db=self.db, offset=offset, limit=limit, schema_to_select=ProductRead, **filters
        )

    async def update_product(self, product_id: int, data: ProductUpdate) -> dict[str, Any]:
        """Update a product."""
        product = await crud_products.get(db=self.db, id=product_id)
        if product is None:
            raise ModelNotFoundError("Product", product_id)

        # SECURITY FIX: Enforce tenant isolation to prevent cross-tenant IDOR.
        # Mirrors the check already present in get_product().
        tenant = get_current_tenant()
        if tenant and product.get("tenant_id") != tenant.id:
            raise ModelNotFoundError("Product", product_id)

        update_data = data.model_dump(exclude_unset=True)
        update_data["updated_at"] = datetime.now(UTC)
        await crud_products.update(db=self.db, object=update_data, id=product_id)
        result = await crud_products.get(db=self.db, id=product_id)
        return dict(result) if result else {}

    async def deactivate_product(self, product_id: int) -> dict[str, Any]:
        """Deactivate a product."""
        product = await crud_products.get(db=self.db, id=product_id)
        if product is None:
            raise ModelNotFoundError("Product", product_id)

        # SECURITY FIX: Enforce tenant isolation to prevent cross-tenant IDOR.
        tenant = get_current_tenant()
        if tenant and product.get("tenant_id") != tenant.id:
            raise ModelNotFoundError("Product", product_id)

        update_data = {"is_active": False, "updated_at": datetime.now(UTC)}
        await crud_products.update(db=self.db, object=update_data, id=product_id)
        result = await crud_products.get(db=self.db, id=product_id)
        return dict(result) if result else {}

    async def get_product_with_pricing(self, product_id: int) -> dict[str, Any]:
        """Get a product by ID with its pricing tiers.

        Uses selectinload to fetch product and pricing in a single query,
        avoiding N+1 query issues.
        """
        stmt = select(Product).options(selectinload(Product.pricing_tiers)).where(Product.id == product_id)
        result = await self.db.execute(stmt)
        product = result.scalar_one_or_none()

        if product is None:
            raise ModelNotFoundError("Product", product_id)

        # SECURITY FIX: Enforce tenant isolation to prevent cross-tenant IDOR.
        tenant = get_current_tenant()
        if tenant and product.tenant_id != tenant.id:
            raise ModelNotFoundError("Product", product_id)

        # P2-5: Always use optimized dict construction
        return _product_to_dict(product, product.pricing_tiers)

    async def add_pricing(
        self,
        product_id: int,
        data: PricingCreate,
    ) -> dict[str, Any]:
        """Add a pricing tier to a product."""
        product = await crud_products.get(db=self.db, id=product_id)
        if product is None:
            raise ModelNotFoundError("Product", product_id)

        # SECURITY FIX: Verify product belongs to current tenant before adding pricing.
        tenant = get_current_tenant()
        if tenant and product.get("tenant_id") != tenant.id:
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
        product = await crud_products.get(db=self.db, id=product_id)
        if product is None:
            raise ModelNotFoundError("Product", product_id)

        # SECURITY FIX: Verify product belongs to current tenant before creating pricing.
        tenant = get_current_tenant()
        if tenant and product.get("tenant_id") != tenant.id:
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
            product = await crud_products.get(db=self.db, id=pricing["product_id"])
            tenant = get_current_tenant()
            if tenant and (product is None or product.get("tenant_id") != tenant.id):
                raise ModelNotFoundError("Pricing", pricing_id)

        return pricing

    async def get_product_pricing(self, product_id: int) -> dict[str, Any]:
        """Get all pricing tiers for a product."""
        return await crud_pricing.get_multi(db=self.db, product_id=product_id, schema_to_select=PricingRead)

    async def list_prices(
        self,
        page: int = 1,
        limit: int = 100,
        product_id: int | None = None,
        is_active: bool | None = None,
        tenant_id: int | str | None = None,
    ) -> dict[str, Any]:
        """List prices with optional filtering."""
        offset = (page - 1) * limit
        filters: dict[str, Any] = {}
        if product_id is not None:
            filters["product_id"] = product_id
        if is_active is not None:
            filters["is_active"] = is_active
        if tenant_id is not None:
            filters["tenant_id"] = tenant_id

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
        product = await crud_products.get(db=self.db, id=pricing.product_id)
        tenant = get_current_tenant()
        if tenant and (product is None or product.get("tenant_id") != tenant.id):
            raise ModelNotFoundError("Pricing", pricing_id)

        update_data = data.model_dump(exclude_unset=True)
        update_data["updated_at"] = datetime.now(UTC)

        # Apply updates to ORM object
        for key, value in update_data.items():
            setattr(pricing, key, value)

        await self.db.commit()

        return PricingRead.model_validate(pricing).model_dump()


class UserSubscriptionService(BaseService[UserSubscription]):
    """Service for managing user subscriptions."""

    def __init__(self, db: AsyncSession):
        super().__init__(UserSubscription, db)

    async def create_subscription(self, user_id: int, data: UserSubscriptionCreate) -> dict[str, Any]:
        """Create a user subscription."""
        tenant = get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("Tenant", "current")

        logger.info("Creating subscription for user in tenant", user_id=user_id, id=tenant.id)
        sub_data = data.model_dump()
        sub_data["tenant_id"] = tenant.id
        sub_data["user_id"] = user_id
        create_obj = UserSubscriptionCreateInternal(**sub_data)
        result = cast(
            dict[str, Any],
            await crud_user_subscriptions.create(db=self.db, object=create_obj, schema_to_select=UserSubscriptionRead),
        )
        logger.info("Subscription created:", val=result.get("id") if result else None)
        return result

    async def get_subscription(self, subscription_id: int) -> dict[str, Any]:
        """Get a subscription by ID."""
        subscription = await crud_user_subscriptions.get(db=self.db, id=subscription_id)
        if subscription is None:
            raise ModelNotFoundError("UserSubscription", subscription_id)

        # SECURITY FIX: Enforce tenant isolation to prevent cross-tenant data leakage.
        tenant = get_current_tenant()
        if tenant and subscription.get("tenant_id") != tenant.id:
            raise ModelNotFoundError("UserSubscription", subscription_id)

        return subscription

    async def get_user_subscriptions(self, user_id: int) -> dict[str, Any]:
        """Get all subscriptions for a user."""
        filters: dict[str, Any] = {"user_id": user_id}
        tenant = get_current_tenant()
        if tenant:
            filters["tenant_id"] = tenant.id

        return await crud_user_subscriptions.get_multi(db=self.db, schema_to_select=UserSubscriptionRead, **filters)

    async def update_subscription(
        self, subscription_id: int, data: UserSubscriptionUpdate, user_id: int | None = None
    ) -> dict[str, Any]:
        """Update a subscription."""
        subscription = await crud_user_subscriptions.get(db=self.db, id=subscription_id)
        if subscription is None:
            raise ModelNotFoundError("UserSubscription", subscription_id)

        # SECURITY FIX: Enforce tenant and user isolation
        tenant = get_current_tenant()
        if tenant and subscription.get("tenant_id") != tenant.id:
            raise ModelNotFoundError("UserSubscription", subscription_id)

        if user_id is not None and subscription.get("user_id") != user_id:
            raise ModelNotFoundError("UserSubscription", subscription_id)

        update_data = data.model_dump(exclude_unset=True)
        update_data["updated_at"] = datetime.now(UTC)
        await crud_user_subscriptions.update(db=self.db, object=update_data, id=subscription_id)
        result = await crud_user_subscriptions.get(db=self.db, id=subscription_id)
        return dict(result) if result else {}

    async def cancel_subscription(self, subscription_id: int, user_id: int | None = None) -> dict[str, Any]:
        """Cancel a subscription at period end.

        This method:
        1. Validates subscription exists
        2. Calls Stripe API to cancel at period end (if stripe_subscription_id exists)
        3. Updates local database record

        Args:
            subscription_id: The subscription ID to cancel
            user_id: Optional user ID to enforce ownership validation

        Returns:
            Updated subscription record

        Raises:
            ModelNotFoundError: If subscription not found
            HTTPException: If Stripe API call fails
        """
        logger.info("Cancelling subscription", subscription_id=subscription_id)
        subscription = await crud_user_subscriptions.get(db=self.db, id=subscription_id)
        if subscription is None:
            raise ModelNotFoundError("UserSubscription", subscription_id)

        # SECURITY FIX: Enforce tenant and user isolation
        tenant = get_current_tenant()
        if tenant and subscription.get("tenant_id") != tenant.id:
            raise ModelNotFoundError("UserSubscription", subscription_id)

        if user_id is not None and subscription.get("user_id") != user_id:
            raise ModelNotFoundError("UserSubscription", subscription_id)

        # T109: Call Stripe API to cancel subscription at period end
        stripe_subscription_id = subscription.get("stripe_subscription_id")
        if stripe_subscription_id:
            tenant_id = subscription.get("tenant_id")
            stripe_settings_record = await crud_stripe_settings.get(db=self.db, tenant_id=tenant_id)
            api_key = None
            if stripe_settings_record and stripe_settings_record.get("encrypted_secret_key"):
                from src.app.core.security.encryption import CredentialEncryption

                api_key = CredentialEncryption().decrypt_column(stripe_settings_record["encrypted_secret_key"])
            else:
                api_key = settings.STRIPE_SECRET_KEY.get_secret_value() if settings.STRIPE_SECRET_KEY else None

            if api_key:
                try:
                    # Wrap sync Stripe call in thread to avoid blocking event loop
                    await asyncio.to_thread(
                        stripe.Subscription.modify,
                        stripe_subscription_id,
                        api_key=api_key,
                        cancel_at_period_end=True,
                    )
                    logger.info(
                        "Stripe subscription marked for cancellation", stripe_subscription_id=stripe_subscription_id
                    )
                except stripe.error.StripeError as e:
                    logger.error("Failed to cancel Stripe subscription", error=e)
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Failed to cancel subscription in Stripe: {str(e)}",
                    )

        await crud_user_subscriptions.update(
            db=self.db,
            object={
                "cancel_at_period_end": True,
                "canceled_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            },
            id=subscription_id,
        )
        result = await crud_user_subscriptions.get(db=self.db, id=subscription_id)
        return dict(result) if result else {}


class BillingRecordService(BaseService[BillingRecord]):
    """Service for managing billing records."""

    def __init__(self, db: AsyncSession):
        super().__init__(BillingRecord, db)

    async def create_record(self, data: BillingRecordCreate) -> dict[str, Any]:
        """Create a billing record."""
        tenant = get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("Tenant", "current")

        record_data = data.model_dump()
        record_data["tenant_id"] = tenant.id
        create_obj = BillingRecordCreateInternal(**record_data)
        return cast(
            dict[str, Any],
            await crud_billing_records.create(db=self.db, object=create_obj, schema_to_select=BillingRecordRead),
        )

    async def get_record(self, record_id: int) -> dict[str, Any]:
        """Get a billing record by ID."""
        record = await crud_billing_records.get(db=self.db, id=record_id)
        if record is None:
            raise ModelNotFoundError("BillingRecord", record_id)

        # SECURITY FIX: Enforce tenant isolation
        tenant = get_current_tenant()
        if tenant and record.get("tenant_id") != tenant.id:
            raise ModelNotFoundError("BillingRecord", record_id)

        return record

    async def get_user_records(
        self, user_id: int, page: int = 1, limit: int = 20, status: str | None = None
    ) -> dict[str, Any]:
        """Get paginated billing records for a user.

        Args:
            user_id: The user ID to filter records for
            page: Page number (1-indexed)
            limit: Number of records per page
            status: Optional status filter (succeeded, failed, pending, refunded)

        Returns:
            Dict with 'data' list and 'total_count'
        """
        # SECURITY FIX: Include tenant_id to prevent cross-tenant data access
        tenant = get_current_tenant()
        if tenant is None:
            raise HTTPException(status_code=403, detail="Tenant context required")
        offset = (page - 1) * limit
        filters: dict[str, Any] = {"user_id": user_id, "tenant_id": tenant.id}

        # T105: Add billing record status filtering
        if status is not None:
            filters["status"] = status

        return await crud_billing_records.get_multi(
            db=self.db,
            offset=offset,
            limit=limit,
            sort_columns=["created_at"],
            sort_orders=["desc"],
            schema_to_select=BillingRecordRead,
            **filters,
        )


# Dependency injection factories
class CheckoutService:
    """Service for managing Stripe checkout sessions."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_session(self, user_id: int, user_email: str, data: CheckoutCreate) -> CheckoutSession:
        """Create a Stripe Checkout session."""
        tenant = get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("Tenant", "current")

        try:
            # Use Platform API Key from Env
            api_key = settings.STRIPE_SECRET_KEY.get_secret_value() if settings.STRIPE_SECRET_KEY else None
        except AttributeError:
            api_key = None

        # Get settings to find Customer ID and tenant stripe key
        settings_service = StripeSettingsService(self.db)
        stripe_settings = await settings_service.get_settings()

        customer_id = None
        if stripe_settings:
            customer_id = stripe_settings.get("stripe_customer_id")
            if stripe_settings.get("encrypted_secret_key"):
                from src.app.core.security.encryption import CredentialEncryption

                api_key = CredentialEncryption().decrypt_column(stripe_settings["encrypted_secret_key"])

        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Stripe API key not configured"
            )

        import stripe

        try:
            import asyncio

            # Lightweight API call to validate the key
            await asyncio.to_thread(stripe.Account.retrieve, api_key=api_key)
        except stripe.error.AuthenticationError:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Invalid Stripe API key configured"
            )

        # Get Plan
        plan_service = SubscriptionPlanService(self.db)
        plan = await plan_service.get_plan(data.plan_id)

        # Get Price ID based on interval
        price_id = (
            plan["stripe_price_id_monthly"] if data.billing_interval == "monthly" else plan["stripe_price_id_yearly"]
        )

        if not price_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Plan does not have a {data.billing_interval} price configured",
            )

        try:
            # Create Stripe Session - build kwargs conditionally to satisfy type checker
            session_kwargs: dict[str, Any] = {
                "api_key": api_key,
                "payment_method_types": ["card"],
                "line_items": [{"price": price_id, "quantity": 1}],
                "mode": "subscription",
                "success_url": data.success_url,
                "cancel_url": data.cancel_url,
                "client_reference_id": str(user_id),
                "subscription_data": {
                    "metadata": {"tenant_id": str(tenant.id), "user_id": str(user_id), "plan_id": str(data.plan_id)}
                },
                "metadata": {"tenant_id": str(tenant.id), "user_id": str(user_id), "plan_id": str(data.plan_id)},
            }

            if customer_id:
                session_kwargs["customer"] = customer_id
            else:
                session_kwargs["customer_email"] = user_email

            # Wrap sync Stripe call in thread to avoid blocking event loop
            session = await asyncio.to_thread(lambda: stripe.checkout.Session.create(**session_kwargs))

            checkout_url = session.url
            if checkout_url is None:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Stripe did not return a checkout URL",
                )

            return CheckoutSession(
                checkout_url=checkout_url,
                session_id=session.id,
                expires_at=datetime.fromtimestamp(session.expires_at, UTC),
            )

        except stripe.error.StripeError as e:
            logger.error("Stripe portal error", error=str(e))
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to initialize customer portal.")

    async def create_portal_session(self, return_url: str) -> str:
        """Create a Stripe Customer Portal session."""
        from urllib.parse import urlparse

        tenant = get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("Tenant", "current")

        # Validate return_url to prevent open redirects (M-01)
        if return_url:
            # Allow relative URLs or URLs matching FRONTEND_URL
            is_relative = return_url.startswith("/") and not return_url.startswith("//")

            frontend_url = settings.FRONTEND_URL.rstrip("/") if settings.FRONTEND_URL else ""
            is_allowed_origin = False

            if frontend_url:
                parsed_return = urlparse(return_url)
                parsed_frontend = urlparse(frontend_url)

                # Check if it matches exactly or is a subdomain of frontend_url
                if parsed_return.netloc == parsed_frontend.netloc:
                    is_allowed_origin = True

            if not (is_relative or is_allowed_origin):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid return_url form: Must be relative or match allowed origins.",
                )

            # Stripe requires absolute URLs
            if is_relative:
                if not frontend_url:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Server misconfiguration: Need FRONTEND_URL to resolve relative return_url.",
                    )
                return_url = f"{frontend_url}{return_url}"

        api_key = settings.STRIPE_SECRET_KEY.get_secret_value() if settings.STRIPE_SECRET_KEY else None
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Stripe API key not configured"
            )

        # Get Customer ID
        settings_service = StripeSettingsService(self.db)
        stripe_settings = await settings_service.get_settings()

        if not stripe_settings or not stripe_settings.get("stripe_customer_id"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="No Stripe customer found for this tenant"
            )

        customer_id = stripe_settings["stripe_customer_id"]

        try:
            # Wrap sync Stripe call in thread to avoid blocking event loop
            session = await asyncio.to_thread(
                stripe.billing_portal.Session.create, api_key=api_key, customer=customer_id, return_url=return_url
            )
            return session.url
        except stripe.error.StripeError as e:
            logger.error("Stripe portal error", error=str(e))
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to initialize customer portal.")


def get_stripe_settings_service(db: AsyncSession) -> StripeSettingsService:
    """Get StripeSettingsService instance."""
    return StripeSettingsService(db)


def get_subscription_plan_service(db: AsyncSession) -> SubscriptionPlanService:
    """Get SubscriptionPlanService instance."""
    return SubscriptionPlanService(db)


def get_product_service(db: AsyncSession) -> ProductService:
    """Get ProductService instance."""
    return ProductService(db)


def get_pricing_service(db: AsyncSession) -> PricingService:
    """Get PricingService instance."""
    return PricingService(db)


def get_user_subscription_service(db: AsyncSession) -> UserSubscriptionService:
    """Get UserSubscriptionService instance."""
    return UserSubscriptionService(db)


def get_billing_record_service(db: AsyncSession) -> BillingRecordService:
    """Get billing record service instance."""
    return BillingRecordService(db)


def get_checkout_service(db: AsyncSession) -> CheckoutService:
    """Get checkout service instance."""
    return CheckoutService(db)
