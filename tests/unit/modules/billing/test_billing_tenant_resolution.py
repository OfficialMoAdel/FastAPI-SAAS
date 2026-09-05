"""Unit tests for Candidate 7: Resolve Tenant Once at Service Construction (Billing)."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.app.modules._base.exceptions import ModelNotFoundError
from src.app.modules.billing.service import PricingService, ProductService, StripeSettingsService
from src.app.modules.billing.subscription_service import SubscriptionPlanService, UserSubscriptionService


@pytest.mark.asyncio
async def test_product_service_explicit_tenant_id():
    tid = uuid4()
    mock_db = AsyncMock()
    service = ProductService(mock_db, tenant_id=tid)

    assert service.current_tenant_id == tid

    with patch("src.app.modules.billing.crud.crud_products.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        with pytest.raises(ModelNotFoundError):
            await service.get_product(product_id=999)


@pytest.mark.asyncio
async def test_pricing_service_explicit_tenant_id():
    tid = uuid4()
    mock_db = AsyncMock()
    service = PricingService(mock_db, tenant_id=tid)

    assert service.current_tenant_id == tid

    with patch("src.app.modules.billing.crud.crud_pricing.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        with pytest.raises(ModelNotFoundError):
            await service.get_pricing(pricing_id=999)


@pytest.mark.asyncio
async def test_subscription_plan_service_explicit_tenant_id():
    tid = uuid4()
    mock_db = AsyncMock()
    service = SubscriptionPlanService(mock_db, tenant_id=tid)

    assert service.current_tenant_id == tid

    with patch("src.app.modules.billing.crud.crud_subscription_plans.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        with pytest.raises(ModelNotFoundError):
            await service.get_plan(plan_id=999)


@pytest.mark.asyncio
async def test_user_subscription_service_explicit_tenant_id():
    tid = uuid4()
    mock_db = AsyncMock()
    service = UserSubscriptionService(mock_db, tenant_id=tid)

    assert service.current_tenant_id == tid

    with patch("src.app.modules.billing.crud.crud_user_subscriptions.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        with pytest.raises(ModelNotFoundError):
            await service.get_subscription(subscription_id=999)


@pytest.mark.asyncio
async def test_stripe_settings_service_explicit_tenant_id():
    tid = uuid4()
    mock_db = AsyncMock()
    service = StripeSettingsService(mock_db, tenant_id=tid)

    assert service.current_tenant_id == tid


@pytest.mark.asyncio
async def test_services_raise_when_tenant_missing():
    mock_db = AsyncMock()
    product_svc = ProductService(mock_db, tenant_id=None)
    pricing_svc = PricingService(mock_db, tenant_id=None)
    sub_svc = SubscriptionPlanService(mock_db, tenant_id=None)
    user_sub_svc = UserSubscriptionService(mock_db, tenant_id=None)

    # All services raise ModelNotFoundError when tenant is missing
    with pytest.raises(ModelNotFoundError):
        product_svc._require_tenant("Product", 1)

    with pytest.raises(ModelNotFoundError):
        pricing_svc._require_tenant("Pricing", 1)

    with pytest.raises(ModelNotFoundError):
        sub_svc._require_tenant("SubscriptionPlan", 1)

    with pytest.raises(ModelNotFoundError):
        user_sub_svc._require_tenant("UserSubscription", 1)
