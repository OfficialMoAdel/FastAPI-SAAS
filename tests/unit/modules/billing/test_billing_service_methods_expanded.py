import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4
from datetime import UTC, datetime

from src.app.modules.billing.schemas import (
    StripeSettingsUpdate,
    SubscriptionPlanUpdate,
    ProductUpdate,
    PricingCreate,
    PricingUpdate,
    UserSubscriptionCreate,
    UserSubscriptionUpdate,
    BillingRecordCreate
)
from src.app.modules.billing.service import (
    StripeSettingsService,
    SubscriptionPlanService,
    ProductService,
    PricingService,
    UserSubscriptionService,
    BillingRecordService,
    CheckoutService
)
from src.app.modules._base.exceptions import ModelNotFoundError
from fastapi import HTTPException

# Mock tenant
@pytest.fixture
def mock_tenant():
    tenant = MagicMock()
    tenant.id = uuid4()
    tenant.is_active = True
    return tenant

@pytest.fixture
def mock_db():
    return AsyncMock()

# --- StripeSettingsService ---

@pytest.mark.asyncio
@patch("src.app.modules.billing.service.get_current_tenant")
@patch("src.app.modules.billing.service.crud_stripe_settings")
async def test_stripe_settings_service_update(mock_crud, mock_get_tenant, mock_db, mock_tenant):
    mock_get_tenant.return_value = mock_tenant
    mock_crud.get = AsyncMock(return_value={"id": 1, "stripe_customer_id": "old_cus"})
    mock_crud.update = AsyncMock()
    
    service = StripeSettingsService(mock_db)
    
    # Mock encryption
    service._encryption.encrypt_column = MagicMock(return_value="encrypted_key")
    
    data = StripeSettingsUpdate(stripe_secret_key="sk_test")
    result = await service.update_settings(data)
    
    mock_crud.update.assert_awaited_once()
    assert result == {"id": 1, "stripe_customer_id": "old_cus"}  # Returns updated object from get

# --- SubscriptionPlanService ---

@pytest.mark.asyncio
@patch("src.app.modules.billing.service.get_current_tenant")
@patch("src.app.modules.billing.service.crud_subscription_plans")
async def test_subscription_plan_service_get_and_list(mock_crud, mock_get_tenant, mock_db, mock_tenant):
    mock_get_tenant.return_value = mock_tenant
    mock_crud.get = AsyncMock(return_value={"id": 1, "tenant_id": mock_tenant.id, "name": "Pro"})
    mock_crud.get_multi = AsyncMock(return_value={"data": [{"id": 1, "name": "Pro"}], "total_count": 1})
    
    service = SubscriptionPlanService(mock_db)
    
    # get_plan
    plan = await service.get_plan(1)
    assert plan["name"] == "Pro"
    
    # get_plans
    plans = await service.get_plans(page=1, limit=10, is_active=True)
    assert plans["total_count"] == 1

@pytest.mark.asyncio
@patch("src.app.modules.billing.service.crud_subscription_plans")
async def test_subscription_plan_service_delete(mock_crud, mock_db):
    mock_crud.get = AsyncMock(return_value={"id": 1, "name": "Pro"})
    mock_crud.update = AsyncMock()
    
    service = SubscriptionPlanService(mock_db)
    result = await service.delete_plan(1)
    assert result["message"] == "Subscription plan deactivated"
    mock_crud.update.assert_awaited_once()

# --- ProductService ---

@pytest.mark.asyncio
@patch("src.app.modules.billing.service.crud_products")
async def test_product_service_operations(mock_crud, mock_db):
    mock_crud.get = AsyncMock(return_value={"id": 1, "name": "Test"})
    mock_crud.get_multi = AsyncMock(return_value={"data": [{"id": 1}], "total_count": 1})
    mock_crud.update = AsyncMock()
    
    service = ProductService(mock_db)
    
    product = await service.get_product(1)
    assert product["name"] == "Test"
    
    products = await service.get_products()
    assert products["total_count"] == 1
    
    update_data = ProductUpdate(name="Updated")
    updated = await service.update_product(1, update_data)
    assert updated == {"id": 1, "name": "Test"}
    
    deactivated = await service.deactivate_product(1)
    assert deactivated == {"id": 1, "name": "Test"}
    assert mock_crud.update.call_count == 2

# --- PricingService ---

@pytest.mark.asyncio
@patch("src.app.modules.billing.service.crud_pricing")
async def test_pricing_service_operations(mock_crud, mock_db):
    mock_crud.get = AsyncMock(return_value={"id": 1, "amount_cents": 1000})
    mock_crud.get_multi = AsyncMock(return_value={"data": [{"id": 1}], "total_count": 1})
    
    service = PricingService(mock_db)
    
    pricing = await service.get_pricing(1)
    assert pricing["amount_cents"] == 1000
    
    product_pricing = await service.get_product_pricing(1)
    assert product_pricing["total_count"] == 1
    
    listed = await service.list_prices(product_id=1, is_active=True)
    assert listed["total_count"] == 1

# --- UserSubscriptionService ---

@pytest.mark.asyncio
@patch("src.app.modules.billing.service.get_current_tenant")
@patch("src.app.modules.billing.service.crud_user_subscriptions")
async def test_user_subscription_service_operations(mock_crud, mock_get_tenant, mock_db, mock_tenant):
    mock_get_tenant.return_value = mock_tenant
    mock_crud.create = AsyncMock(return_value={"id": 1, "status": "active"})
    mock_crud.get = AsyncMock(return_value={"id": 1, "status": "active", "stripe_subscription_id": None})
    mock_crud.get_multi = AsyncMock(return_value={"data": [{"id": 1}], "total_count": 1})
    mock_crud.update = AsyncMock()
    
    service = UserSubscriptionService(mock_db)
    
    data = UserSubscriptionCreate(
        user_id=1,
        subscription_plan_id=1,
        status="active",
        billing_interval="monthly",
        current_period_start=datetime.now(UTC),
        current_period_end=datetime.now(UTC)
    )
    created = await service.create_subscription(1, data)
    assert created["status"] == "active"
    
    subscription = await service.get_subscription(1)
    assert subscription["status"] == "active"
    
    user_subs = await service.get_user_subscriptions(1)
    assert user_subs["total_count"] == 1
    
    update_data = UserSubscriptionUpdate(status="canceled")
    updated = await service.update_subscription(1, update_data)
    assert updated == {"id": 1, "status": "active", "stripe_subscription_id": None}
    
    canceled = await service.cancel_subscription(1)
    assert canceled["id"] == 1
    mock_crud.update.assert_awaited()

# --- BillingRecordService ---

@pytest.mark.asyncio
@patch("src.app.modules.billing.service.get_current_tenant")
@patch("src.app.modules.billing.service.crud_billing_records")
async def test_billing_record_service_operations(mock_crud, mock_get_tenant, mock_db, mock_tenant):
    mock_get_tenant.return_value = mock_tenant
    mock_crud.create = AsyncMock(return_value={"id": 1, "amount_cents": 1000})
    mock_crud.get = AsyncMock(return_value={"id": 1, "amount_cents": 1000})
    mock_crud.get_multi = AsyncMock(return_value={"data": [{"id": 1}], "total_count": 1})
    
    service = BillingRecordService(mock_db)
    
    data = BillingRecordCreate(user_id=1, amount_cents=1000, currency="usd", status="succeeded")
    created = await service.create_record(data)
    assert created["amount_cents"] == 1000
    
    record = await service.get_record(1)
    assert record["id"] == 1
    
    user_records = await service.get_user_records(1, status="succeeded")
    assert user_records["total_count"] == 1

# --- CheckoutService ---

@pytest.mark.asyncio
@patch("src.app.modules.billing.service.get_current_tenant")
@patch("src.app.modules.billing.service.settings")
@patch("src.app.modules.billing.service.StripeSettingsService.get_settings")
@patch("asyncio.to_thread")
async def test_checkout_service_create_portal_session(mock_to_thread, mock_get_settings, mock_settings, mock_get_tenant, mock_db, mock_tenant):
    mock_get_tenant.return_value = mock_tenant
    
    mock_secret = MagicMock()
    mock_secret.get_secret_value.return_value = "sk_test_123"
    mock_settings.STRIPE_SECRET_KEY = mock_secret
    mock_settings.FRONTEND_URL = "http://localhost:3000/"
    
    mock_get_settings.return_value = {"stripe_customer_id": "cus_123"}
    
    mock_session = MagicMock()
    mock_session.url = "https://billing.stripe.com/p/session/test"
    mock_to_thread.return_value = mock_session
    
    service = CheckoutService(mock_db)
    
    url = await service.create_portal_session("/billing")
    assert url == "https://billing.stripe.com/p/session/test"
    mock_to_thread.assert_awaited_once()

