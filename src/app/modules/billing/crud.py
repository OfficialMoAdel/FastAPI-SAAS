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
    BillingRecordRead,
    PricingCreate,
    PricingRead,
    PricingUpdate,
    ProductCreate,
    ProductRead,
    ProductUpdate,
    StripeSettingsCreate,
    StripeSettingsRead,
    StripeSettingsUpdate,
    SubscriptionPlanCreate,
    SubscriptionPlanRead,
    SubscriptionPlanUpdate,
    UserSubscriptionCreate,
    UserSubscriptionRead,
    UserSubscriptionUpdate,
)


# Stripe Settings CRUD (1:1 per tenant)
CRUDStripeSettings = TenantAwareCRUD[
    StripeSettings,
    StripeSettingsCreate,
    StripeSettingsUpdate,
    StripeSettingsUpdate,
    None,
    StripeSettingsRead,
]
crud_stripe_settings = CRUDStripeSettings(StripeSettings)


# Subscription Plan CRUD
CRUDSubscriptionPlan = TenantAwareCRUD[
    SubscriptionPlan,
    SubscriptionPlanCreate,
    SubscriptionPlanUpdate,
    SubscriptionPlanUpdate,
    None,
    SubscriptionPlanRead,
]
crud_subscription_plans = CRUDSubscriptionPlan(SubscriptionPlan)


# Product CRUD
CRUDProduct = TenantAwareCRUD[
    Product,
    ProductCreate,
    ProductUpdate,
    ProductUpdate,
    None,
    ProductRead,
]
crud_products = CRUDProduct(Product)


# Pricing CRUD
CRUDPricing = TenantAwareCRUD[
    Pricing,
    PricingCreate,
    PricingUpdate,
    PricingUpdate,
    None,
    PricingRead,
]
crud_pricing = CRUDPricing(Pricing)


# User Subscription CRUD
CRUDUserSubscription = TenantAwareCRUD[
    UserSubscription,
    UserSubscriptionCreate,
    UserSubscriptionUpdate,
    UserSubscriptionUpdate,
    None,
    UserSubscriptionRead,
]
crud_user_subscriptions = CRUDUserSubscription(UserSubscription)


# Billing Record CRUD
CRUDBillingRecord = TenantAwareCRUD[
    BillingRecord,
    BillingRecordCreate,
    None,
    None,
    None,
    BillingRecordRead,
]
crud_billing_records = CRUDBillingRecord(BillingRecord)
