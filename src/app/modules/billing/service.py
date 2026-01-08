"""Billing & Payments service layer.

Business logic for Stripe integration, subscription management,
products, pricing, and billing records.
"""

from datetime import UTC, datetime
from typing import Any

import stripe
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.security.encryption import CredentialEncryption
from src.app.core.tenancy.context import get_tenant_context
from src.app.modules._base.exceptions import ModelNotFoundError
from src.app.modules._base.service import BaseService

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
from src.app.core.config import settings
from .schemas import (
    BillingRecordCreate,
    BillingRecordCreateInternal,
    CheckoutCreate,
    CheckoutSession,
    PricingCreate,
    PricingCreateInternal,
    PricingUpdate,
    ProductCreate,
    ProductCreateInternal,
    ProductUpdate,
    StripeSettingsCreateInternal,
    StripeSettingsUpdate,
    SubscriptionPlanCreate,
    SubscriptionPlanCreateInternal,
    SubscriptionPlanUpdate,
    UserSubscriptionCreate,
    UserSubscriptionCreateInternal,
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
            return await crud_stripe_settings.create(db=self.db, object=create_obj)
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
        return await crud_subscription_plans.create(db=self.db, object=create_obj)

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
            db=self.db, offset=offset, limit=limit, sort_columns=["sort_order"], **filters
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
        return await crud_products.create(db=self.db, object=create_obj)

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
        return await crud_products.get_multi(db=self.db, offset=offset, limit=limit, **filters)

    async def update_product(self, product_id: int, data: ProductUpdate) -> dict[str, Any]:
        """Update a product."""
        product = await crud_products.get(db=self.db, id=product_id)
        if product is None:
            raise ModelNotFoundError("Product", product_id)

        update_data = data.model_dump(exclude_unset=True)
        update_data["updated_at"] = datetime.now(UTC)
        await crud_products.update(db=self.db, object=update_data, id=product_id)
        return await crud_products.get(db=self.db, id=product_id)


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
        return await crud_pricing.create(db=self.db, object=create_obj)

    async def get_pricing(self, pricing_id: int) -> dict[str, Any]:
        """Get a pricing tier by ID."""
        pricing = await crud_pricing.get(db=self.db, id=pricing_id)
        if pricing is None:
            raise ModelNotFoundError("Pricing", pricing_id)
        return pricing

    async def get_product_pricing(self, product_id: int) -> dict[str, Any]:
        """Get all pricing tiers for a product."""
        return await crud_pricing.get_multi(db=self.db, product_id=product_id)

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

        sub_data = data.model_dump()
        sub_data["tenant_id"] = tenant.id
        sub_data["user_id"] = user_id
        create_obj = UserSubscriptionCreateInternal(**sub_data)
        return await crud_user_subscriptions.create(db=self.db, object=create_obj)

    async def get_subscription(self, subscription_id: int) -> dict[str, Any]:
        """Get a subscription by ID."""
        subscription = await crud_user_subscriptions.get(db=self.db, id=subscription_id)
        if subscription is None:
            raise ModelNotFoundError("UserSubscription", subscription_id)
        return subscription

    async def get_user_subscriptions(self, user_id: int) -> dict[str, Any]:
        """Get all subscriptions for a user."""
        return await crud_user_subscriptions.get_multi(db=self.db, user_id=user_id)

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
        """Cancel a subscription at period end."""
        subscription = await crud_user_subscriptions.get(db=self.db, id=subscription_id)
        if subscription is None:
            raise ModelNotFoundError("UserSubscription", subscription_id)

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
        return await crud_billing_records.create(db=self.db, object=create_obj)

    async def get_record(self, record_id: int) -> dict[str, Any]:
        """Get a billing record by ID."""
        record = await crud_billing_records.get(db=self.db, id=record_id)
        if record is None:
            raise ModelNotFoundError("BillingRecord", record_id)
        return record

    async def get_user_records(self, user_id: int, page: int = 1, limit: int = 20) -> dict[str, Any]:
        """Get paginated billing records for a user."""
        offset = (page - 1) * limit
        return await crud_billing_records.get_multi(db=self.db, user_id=user_id, offset=offset, limit=limit)


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

            session = stripe.checkout.Session.create(**session_params)

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
            session = stripe.billing_portal.Session.create(api_key=api_key, customer=customer_id, return_url=return_url)
            return session.url
        except stripe.error.StripeError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Stripe error: {str(e)}")

        # Decrypt API key
        encrypted_key = stripe_settings.get("encrypted_secret_key")
        if not encrypted_key:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Stripe secret key not found")

        api_key = self.encryption.decrypt(encrypted_key)

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
            session = stripe.checkout.Session.create(
                api_key=api_key,
                customer_email=user_email,  # Pre-fill email
                # customer=... if we have stored customer_id for this user?
                # We should check UserSubscription or User model for stripe_customer_id?
                # For now, we rely on email or let Stripe create new customer.
                # Ideally we store customer_id on User.
                payment_method_types=["card"],
                line_items=[
                    {
                        "price": price_id,
                        "quantity": 1,
                    }
                ],
                mode="subscription",
                success_url=data.success_url,
                cancel_url=data.cancel_url,
                client_reference_id=str(user_id),  # To link back webhook
                subscription_data={
                    "metadata": {"tenant_id": str(tenant.id), "user_id": str(user_id), "plan_id": str(data.plan_id)}
                },
                metadata={"tenant_id": str(tenant.id), "user_id": str(user_id), "plan_id": str(data.plan_id)},
            )

            return CheckoutSession(
                checkout_url=session.url,
                session_id=session.id,
                expires_at=datetime.fromtimestamp(session.expires_at, UTC),
            )

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
