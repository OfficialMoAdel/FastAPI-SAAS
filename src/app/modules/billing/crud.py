"""Billing & Payments CRUD operations.

Tenant-aware CRUD classes for all billing-related models.
"""

from src.app.core.db.tenant_crud import TenantAwareCRUD

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
    BillingRecordDelete,
    BillingRecordRead,
    BillingRecordUpdate,
    PricingCreate,
    PricingDelete,
    PricingRead,
    PricingUpdate,
    ProductCreate,
    ProductDelete,
    ProductRead,
    ProductUpdate,
    StripeSettingsCreate,
    StripeSettingsDelete,
    StripeSettingsRead,
    StripeSettingsUpdate,
    SubscriptionPlanCreate,
    SubscriptionPlanDelete,
    SubscriptionPlanRead,
    SubscriptionPlanUpdate,
    UserSubscriptionCreate,
    UserSubscriptionDelete,
    UserSubscriptionRead,
    UserSubscriptionUpdate,
)

# Stripe Settings CRUD (1:1 per tenant)
CRUDStripeSettings = TenantAwareCRUD[
    StripeSettings,
    StripeSettingsCreate,
    StripeSettingsUpdate,
    StripeSettingsUpdate,
    StripeSettingsDelete,
    StripeSettingsRead,
]
crud_stripe_settings = CRUDStripeSettings(StripeSettings)


# Subscription Plan CRUD
CRUDSubscriptionPlan = TenantAwareCRUD[
    SubscriptionPlan,
    SubscriptionPlanCreate,
    SubscriptionPlanUpdate,
    SubscriptionPlanUpdate,
    SubscriptionPlanDelete,
    SubscriptionPlanRead,
]
crud_subscription_plans = CRUDSubscriptionPlan(SubscriptionPlan)


# Product CRUD
CRUDProduct = TenantAwareCRUD[
    Product,
    ProductCreate,
    ProductUpdate,
    ProductUpdate,
    ProductDelete,
    ProductRead,
]
crud_products = CRUDProduct(Product)


# Pricing CRUD
CRUDPricing = TenantAwareCRUD[
    Pricing,
    PricingCreate,
    PricingUpdate,
    PricingUpdate,
    PricingDelete,
    PricingRead,
]
crud_pricing = CRUDPricing(Pricing)


# User Subscription CRUD
CRUDUserSubscription = TenantAwareCRUD[
    UserSubscription,
    UserSubscriptionCreate,
    UserSubscriptionUpdate,
    UserSubscriptionUpdate,
    UserSubscriptionDelete,
    UserSubscriptionRead,
]
crud_user_subscriptions = CRUDUserSubscription(UserSubscription)


# Billing Record CRUD
CRUDBillingRecord = TenantAwareCRUD[
    BillingRecord,
    BillingRecordCreate,
    BillingRecordUpdate,
    BillingRecordUpdate,
    BillingRecordDelete,
    BillingRecordRead,
]
crud_billing_records = CRUDBillingRecord(BillingRecord)
