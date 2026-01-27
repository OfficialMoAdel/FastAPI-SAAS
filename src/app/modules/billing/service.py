"""Billing & Payments service layer.

Business logic for Stripe integration, subscription management,
products, pricing, and billing records.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import stripe
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.core.security.encryption import CredentialEncryption
from src.app.core.tenancy.context import get_tenant_context
from src.app.modules._base.exceptions import ModelNotFoundError
from src.app.modules._base.service import BaseService

logger = logging.getLogger(__name__)

from src.app.core.config import settings

from .crud import (
    crud_billing_records,
    crud_pricing,
    crud_products,
    crud_stripe_settings,
    crud_subscription_plans,
    crud_user_subscriptions,
)
from .models import (
    BillingRecord,
    Pricing,
    Product,
    StripeSettings,
    SubscriptionPlan,
    UserSubscription,
)
from .schemas import (
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


class StripeSettingsService(BaseService[StripeSettings]):
    """Service for managing tenant Stripe settings."""

    def __init__(self, db: AsyncSession):
        super().__init__(StripeSettings, db)
        self._encryption = CredentialEncryption()

    async def get_settings(self) -> dict[str, Any] | None:
        """Get Stripe settings for the current tenant."""
        tenant = get_tenant_context()
        if tenant is None:
            return None
        return await crud_stripe_settings.get(db=self.db, tenant_id=tenant.id)

    async def update_settings(self, data: StripeSettingsUpdate) -> dict[str, Any]:
        """Update Stripe settings for the current tenant."""
        tenant = get_tenant_context()
        if tenant is None:
            raise ModelNotFoundError("Tenant", "current")

        settings = await crud_stripe_settings.get(db=self.db, tenant_id=tenant.id)

        update_data = data.model_dump(exclude_unset=True)

        # Encrypt the secret key if provided
        if "stripe_secret_key" in update_data and update_data["stripe_secret_key"]:
            encrypted = self._encryption.encrypt(update_data.pop("stripe_secret_key"))
            update_data["encrypted_secret_key"] = encrypted

        update_data["updated_at"] = datetime.now(UTC)

        if settings is None:
            # Create new settings
            update_data["tenant_id"] = tenant.id
            # Create object must be a Pydantic model for FastCRUD
            create_obj = StripeSettingsCreateInternal(**update_data)
            return await crud_stripe_settings.create(db=self.db, object=create_obj, schema_to_select=StripeSettingsRead)
        else:
            # Update existing settings
            await crud_stripe_settings.update(db=self.db, object=update_data, id=settings["id"])
            return await crud_stripe_settings.get(db=self.db, id=settings["id"])


class SubscriptionPlanService(BaseService[SubscriptionPlan]):
    """Service for managing subscription plans."""

    def __init__(self, db: AsyncSession):
        super().__init__(SubscriptionPlan, db)

    async def create_plan(self, data: SubscriptionPlanCreate) -> dict[str, Any]:
        """Create a new subscription plan."""
        tenant = get_tenant_context()
        if tenant is None:
            raise ModelNotFoundError("Tenant", "current")

        plan_data = data.model_dump()
        plan_data["tenant_id"] = tenant.id
        create_obj = SubscriptionPlanCreateInternal(**plan_data)
        return await crud_subscription_plans.create(
            db=self.db, object=create_obj, schema_to_select=SubscriptionPlanRead
        )

    async def get_plan(self, plan_id: int) -> dict[str, Any]:
        """Get a subscription plan by ID."""
        plan = await crud_subscription_plans.get(db=self.db, id=plan_id)
        if plan is None:
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
        """Update a subscription plan."""
        plan = await crud_subscription_plans.get(db=self.db, id=plan_id)
        if plan is None:
            raise ModelNotFoundError("SubscriptionPlan", plan_id)

        update_data = data.model_dump(exclude_unset=True)
        update_data["updated_at"] = datetime.now(UTC)
        await crud_subscription_plans.update(db=self.db, object=update_data, id=plan_id)
        return await crud_subscription_plans.get(db=self.db, id=plan_id)

    async def delete_plan(self, plan_id: int) -> dict[str, str]:
        """Soft delete a subscription plan."""
        plan = await crud_subscription_plans.get(db=self.db, id=plan_id)
        if plan is None:
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
        tenant = get_tenant_context()
        if tenant is None:
            raise ModelNotFoundError("Tenant", "current")

        product_data = data.model_dump()
        product_data["tenant_id"] = tenant.id
        create_obj = ProductCreateInternal(**product_data)
        return await crud_products.create(db=self.db, object=create_obj, schema_to_select=ProductRead)

    async def get_product(self, product_id: int) -> dict[str, Any]:
        """Get a product by ID."""
        product = await crud_products.get(db=self.db, id=product_id)
        if product is None:
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

        update_data = data.model_dump(exclude_unset=True)
        update_data["updated_at"] = datetime.now(UTC)
        await crud_products.update(db=self.db, object=update_data, id=product_id)
        return await crud_products.get(db=self.db, id=product_id)

    async def deactivate_product(self, product_id: int) -> dict[str, Any]:
        """Deactivate a product."""
        product = await crud_products.get(db=self.db, id=product_id)
        if product is None:
            raise ModelNotFoundError("Product", product_id)

        update_data = {"is_active": False, "updated_at": datetime.now(UTC)}
        await crud_products.update(db=self.db, object=update_data, id=product_id)
        return await crud_products.get(db=self.db, id=product_id)

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

        return {
            "id": product.id,
            "tenant_id": product.tenant_id,
            "name": product.name,
            "description": product.description,
            "stripe_product_id": product.stripe_product_id,
            "is_active": product.is_active,
            "created_at": product.created_at,
            "updated_at": product.updated_at,
            "pricing_tiers": [
                {
                    "id": p.id,
                    "product_id": p.product_id,
                    "name": p.name,
                    "amount_cents": p.amount_cents,
                    "interval": p.interval,
                    "stripe_price_id": p.stripe_price_id,
                    "currency": p.currency,
                    "features": p.features,
                    "is_active": p.is_active,
                    "created_at": p.created_at,
                    "updated_at": p.updated_at,
                }
                for p in product.pricing_tiers
            ],
        }

    async def add_pricing(
        self,
        product_id: int,
        data: PricingCreate,
    ) -> dict[str, Any]:
        """Add a pricing tier to a product."""
        product = await crud_products.get(db=self.db, id=product_id)
        if product is None:
            raise ModelNotFoundError("Product", product_id)

        pricing_data = data.model_dump()
        pricing_data["product_id"] = product_id
        create_obj = PricingCreateInternal(**pricing_data)
        return await crud_pricing.create(db=self.db, object=create_obj, schema_to_select=PricingRead)


class PricingService(BaseService[Pricing]):
    """Service for managing pricing tiers."""

    def __init__(self, db: AsyncSession):
        super().__init__(Pricing, db)

    async def create_pricing(self, product_id: int, data: PricingCreate) -> dict[str, Any]:
        """Create a pricing tier for a product."""
        product = await crud_products.get(db=self.db, id=product_id)
        if product is None:
            raise ModelNotFoundError("Product", product_id)

        pricing_data = data.model_dump()
        pricing_data["product_id"] = product_id
        create_obj = PricingCreateInternal(**pricing_data)
        return await crud_pricing.create(db=self.db, object=create_obj, schema_to_select=PricingRead)

    async def get_pricing(self, pricing_id: int) -> dict[str, Any]:
        """Get a pricing tier by ID."""
        pricing = await crud_pricing.get(db=self.db, id=pricing_id)
        if pricing is None:
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
    ) -> dict[str, Any]:
        """List prices with optional filtering."""
        offset = (page - 1) * limit
        filters: dict[str, Any] = {}
        if product_id is not None:
            filters["product_id"] = product_id
        if is_active is not None:
            filters["is_active"] = is_active

        return await crud_pricing.get_multi(
            db=self.db,
            offset=offset,
            limit=limit,
            schema_to_select=PricingRead,
            **filters,
        )

    async def update_pricing(self, pricing_id: int, data: PricingUpdate) -> dict[str, Any]:
        """Update a pricing tier."""
        pricing = await crud_pricing.get(db=self.db, id=pricing_id)
        if pricing is None:
            raise ModelNotFoundError("Pricing", pricing_id)

        update_data = data.model_dump(exclude_unset=True)
        update_data["updated_at"] = datetime.now(UTC)
        await crud_pricing.update(db=self.db, object=update_data, id=pricing_id)
        return await crud_pricing.get(db=self.db, id=pricing_id)


class UserSubscriptionService(BaseService[UserSubscription]):
    """Service for managing user subscriptions."""

    def __init__(self, db: AsyncSession):
        super().__init__(UserSubscription, db)

    async def create_subscription(self, user_id: int, data: UserSubscriptionCreate) -> dict[str, Any]:
        """Create a user subscription."""
        tenant = get_tenant_context()
        if tenant is None:
            raise ModelNotFoundError("Tenant", "current")

        logger.info(f"Creating subscription for user {user_id} in tenant {tenant.id}")
        sub_data = data.model_dump()
        sub_data["tenant_id"] = tenant.id
        sub_data["user_id"] = user_id
        create_obj = UserSubscriptionCreateInternal(**sub_data)
        result = await crud_user_subscriptions.create(
            db=self.db, object=create_obj, schema_to_select=UserSubscriptionRead
        )
        logger.info(f"Subscription created: {result.get('id') if result else None}")
        return result

    async def get_subscription(self, subscription_id: int) -> dict[str, Any]:
        """Get a subscription by ID."""
        subscription = await crud_user_subscriptions.get(db=self.db, id=subscription_id)
        if subscription is None:
            raise ModelNotFoundError("UserSubscription", subscription_id)
        return subscription

    async def get_user_subscriptions(self, user_id: int) -> dict[str, Any]:
        """Get all subscriptions for a user."""
        return await crud_user_subscriptions.get_multi(
            db=self.db, user_id=user_id, schema_to_select=UserSubscriptionRead
        )

    async def update_subscription(self, subscription_id: int, data: UserSubscriptionUpdate) -> dict[str, Any]:
        """Update a subscription."""
        subscription = await crud_user_subscriptions.get(db=self.db, id=subscription_id)
        if subscription is None:
            raise ModelNotFoundError("UserSubscription", subscription_id)

        update_data = data.model_dump(exclude_unset=True)
        update_data["updated_at"] = datetime.now(UTC)
        await crud_user_subscriptions.update(db=self.db, object=update_data, id=subscription_id)
        return await crud_user_subscriptions.get(db=self.db, id=subscription_id)

    async def cancel_subscription(self, subscription_id: int) -> dict[str, Any]:
        """Cancel a subscription at period end.

        This method:
        1. Validates subscription exists
        2. Calls Stripe API to cancel at period end (if stripe_subscription_id exists)
        3. Updates local database record

        Args:
            subscription_id: The subscription ID to cancel

        Returns:
            Updated subscription record

        Raises:
            ModelNotFoundError: If subscription not found
            HTTPException: If Stripe API call fails
        """
        logger.info(f"Cancelling subscription {subscription_id}")
        subscription = await crud_user_subscriptions.get(db=self.db, id=subscription_id)
        if subscription is None:
            raise ModelNotFoundError("UserSubscription", subscription_id)

        # T109: Call Stripe API to cancel subscription at period end
        stripe_subscription_id = subscription.get("stripe_subscription_id")
        if stripe_subscription_id:
            api_key = settings.STRIPE_SECRET_KEY
            if api_key:
                try:
                    # Wrap sync Stripe call in thread to avoid blocking event loop
                    await asyncio.to_thread(
                        stripe.Subscription.modify,
                        stripe_subscription_id,
                        api_key=api_key,
                        cancel_at_period_end=True,
                    )
                    logger.info(f"Stripe subscription {stripe_subscription_id} marked for cancellation")
                except stripe.error.StripeError as e:
                    logger.error(f"Failed to cancel Stripe subscription: {e}")
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
        return await crud_user_subscriptions.get(db=self.db, id=subscription_id)


class BillingRecordService(BaseService[BillingRecord]):
    """Service for managing billing records."""

    def __init__(self, db: AsyncSession):
        super().__init__(BillingRecord, db)

    async def create_record(self, data: BillingRecordCreate) -> dict[str, Any]:
        """Create a billing record."""
        tenant = get_tenant_context()
        if tenant is None:
            raise ModelNotFoundError("Tenant", "current")

        record_data = data.model_dump()
        record_data["tenant_id"] = tenant.id
        create_obj = BillingRecordCreateInternal(**record_data)
        return await crud_billing_records.create(db=self.db, object=create_obj, schema_to_select=BillingRecordRead)

    async def get_record(self, record_id: int) -> dict[str, Any]:
        """Get a billing record by ID."""
        record = await crud_billing_records.get(db=self.db, id=record_id)
        if record is None:
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
        offset = (page - 1) * limit
        filters: dict[str, Any] = {"user_id": user_id}

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
        # self.encryption = CredentialEncryption() # Not needed if using env vars for platform keys

    async def create_session(self, user_id: int, user_email: str, data: CheckoutCreate) -> CheckoutSession:
        """Create a Stripe Checkout session."""
        tenant = get_tenant_context()
        if tenant is None:
            raise ModelNotFoundError("Tenant", "current")

        # Use Platform API Key from Env
        api_key = settings.STRIPE_SECRET_KEY
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Stripe API key not configured"
            )

        # Get settings to find Customer ID
        settings_service = StripeSettingsService(self.db)
        stripe_settings = await settings_service.get_settings()

        customer_id = None
        if stripe_settings:
            customer_id = stripe_settings.get("stripe_customer_id")

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
            # Create Stripe Session
            session_params = {
                "api_key": api_key,
                "payment_method_types": ["card"],
                "line_items": [
                    {
                        "price": price_id,
                        "quantity": 1,
                    }
                ],
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
                session_params["customer"] = customer_id
            else:
                session_params["customer_email"] = user_email

            # Wrap sync Stripe call in thread to avoid blocking event loop
            session = await asyncio.to_thread(stripe.checkout.Session.create, **session_params)

            return CheckoutSession(
                checkout_url=session.url,
                session_id=session.id,
                expires_at=datetime.fromtimestamp(session.expires_at, UTC),
            )

        except stripe.error.StripeError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Stripe error: {str(e)}")

    async def create_portal_session(self, return_url: str) -> str:
        """Create a Stripe Customer Portal session."""
        tenant = get_tenant_context()
        if tenant is None:
            raise ModelNotFoundError("Tenant", "current")

        api_key = settings.STRIPE_SECRET_KEY
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
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Stripe error: {str(e)}")


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
