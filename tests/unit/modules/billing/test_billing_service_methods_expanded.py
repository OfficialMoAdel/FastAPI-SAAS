from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.billing.checkout_service import CheckoutService
from src.app.modules.billing.schemas import (
    BillingRecordCreate,
    ProductUpdate,
    StripeSettingsUpdate,
    UserSubscriptionCreate,
    UserSubscriptionUpdate,
)
from src.app.modules.billing.service import (
    PricingService,
    ProductService,
    StripeSettingsService,
)
from src.app.modules.billing.subscription_service import (
    BillingRecordService,
    SubscriptionPlanService,
    UserSubscriptionService,
)


@pytest.fixture(autouse=True)
def _route_windowed_fetch_to_mocked_crud(monkeypatch):
    """P0-5: services call fetch_page_with_total instead of crud.get_multi.

    Wiring tests mock crud.get_multi; route the new seam to the configured
    mock so service->response wiring stays verified. Query-shape equivalence
    is covered by tests/unit/modules/_base/test_windowed_pagination.py.
    """
    import src.app.modules.billing.service as _svc_mod
    import src.app.modules.billing.subscription_service as _sub_mod

    async def _fake(crud, db, **kwargs):
        return await crud.get_multi(db=db, **kwargs)

    monkeypatch.setattr(_svc_mod, "fetch_page_with_total", _fake)
    monkeypatch.setattr(_sub_mod, "fetch_page_with_total", _fake)


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
@patch("src.app.core.tenancy.context.get_current_tenant")
@patch("src.app.core.tenancy.context.get_current_tenant")
@patch("src.app.modules.billing.service.crud_stripe_settings")
async def test_stripe_settings_service_update(mock_crud, mock_get_tenancy, mock_get_svc, mock_db, mock_tenant):
    mock_get_tenancy.return_value = mock_tenant
    mock_get_svc.return_value = mock_tenant
    mock_crud.get = AsyncMock(return_value={"id": 1, "stripe_customer_id": "old_cus"})
    mock_crud.update = AsyncMock()

    service = StripeSettingsService(mock_db)

    # Mock encryption
    service._encryption.encrypt_column = MagicMock(return_value="encrypted_key")

    data = StripeSettingsUpdate(stripe_secret_key="sk_test")
    result = await service.update_settings(data)

    mock_crud.update.assert_awaited_once()
    assert result["id"] == 1
    assert result["stripe_customer_id"] == "old_cus"
    assert result["encrypted_secret_key"] == "encrypted_key"
    assert "updated_at" in result


# --- SubscriptionPlanService ---


@pytest.mark.asyncio
@patch("src.app.core.tenancy.context.get_current_tenant")
@patch("src.app.modules.billing.subscription_service.crud_subscription_plans")
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
@patch("src.app.core.tenancy.context.get_current_tenant")
@patch("src.app.modules.billing.subscription_service.crud_subscription_plans")
async def test_subscription_plan_service_delete(mock_crud, mock_get_tenant, mock_db, mock_tenant):
    mock_get_tenant.return_value = mock_tenant
    mock_crud.get = AsyncMock(return_value={"id": 1, "name": "Pro", "tenant_id": mock_tenant.id})
    mock_crud.update = AsyncMock()

    service = SubscriptionPlanService(mock_db)
    result = await service.delete_plan(1)
    assert result["message"] == "Subscription plan deactivated"
    mock_crud.update.assert_awaited_once()


# --- ProductService ---


@pytest.mark.asyncio
@patch("src.app.core.tenancy.context.get_current_tenant")
@patch("src.app.core.tenancy.context.get_current_tenant")
@patch("src.app.modules.billing.service.crud_products")
async def test_product_service_operations(mock_crud, mock_get_tenant_sub, mock_get_tenant_svc, mock_db, mock_tenant):
    mock_get_tenant_sub.return_value = mock_tenant
    mock_get_tenant_svc.return_value = mock_tenant
    mock_crud.get = AsyncMock(return_value={"id": 1, "name": "Test", "tenant_id": mock_tenant.id})
    mock_crud.get_multi = AsyncMock(return_value={"data": [{"id": 1}], "total_count": 1})
    mock_crud.update = AsyncMock()

    service = ProductService(mock_db)

    product = await service.get_product(1)
    assert product["name"] == "Test"

    products = await service.get_products()
    assert products["total_count"] == 1

    update_data = ProductUpdate(name="Updated")
    updated = await service.update_product(1, update_data)
    assert updated == {"id": 1, "name": "Test", "tenant_id": mock_tenant.id}

    deactivated = await service.deactivate_product(1)
    assert deactivated == {"id": 1, "name": "Test", "tenant_id": mock_tenant.id}
    assert mock_crud.update.call_count == 2


# --- PricingService ---


@pytest.mark.asyncio
@patch("src.app.core.tenancy.context.get_current_tenant")
@patch("src.app.modules.billing.service.crud_products")
@patch("src.app.modules.billing.service.crud_pricing")
async def test_pricing_service_operations(mock_crud, mock_products, mock_get_tenant, mock_db, mock_tenant):
    mock_get_tenant.return_value = mock_tenant
    mock_products.get = AsyncMock(return_value={"id": 1, "tenant_id": mock_tenant.id})
    mock_crud.get = AsyncMock(return_value={"id": 1, "product_id": 1, "amount_cents": 1000})
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
@patch("src.app.core.tenancy.context.get_current_tenant")
@patch("src.app.modules.billing.subscription_service.crud_user_subscriptions")
async def test_user_subscription_service_operations(mock_crud, mock_get_tenant, mock_db, mock_tenant):
    mock_get_tenant.return_value = mock_tenant
    mock_crud.create = AsyncMock(return_value={"id": 1, "status": "active"})
    mock_crud.get = AsyncMock(
        return_value={"id": 1, "status": "active", "stripe_subscription_id": None, "tenant_id": mock_tenant.id}
    )
    mock_crud.get_multi = AsyncMock(return_value={"data": [{"id": 1}], "total_count": 1})
    mock_crud.update = AsyncMock()

    service = UserSubscriptionService(mock_db)

    data = UserSubscriptionCreate(
        user_id=1,
        subscription_plan_id=1,
        status="active",
        billing_interval="monthly",
        current_period_start=datetime.now(UTC),
        current_period_end=datetime.now(UTC),
    )
    created = await service.create_subscription(1, data)
    assert created["status"] == "active"

    subscription = await service.get_subscription(1)
    assert subscription["status"] == "active"

    user_subs = await service.get_user_subscriptions(1)
    assert user_subs["total_count"] == 1

    update_data = UserSubscriptionUpdate(status="canceled")
    updated = await service.update_subscription(1, update_data)
    assert updated["id"] == 1 and updated["status"] == "active"

    canceled = await service.cancel_subscription(1)
    assert canceled["id"] == 1
    mock_crud.update.assert_awaited()


# --- BillingRecordService ---


@pytest.mark.asyncio
@patch("src.app.core.tenancy.context.get_current_tenant")
@patch("src.app.core.tenancy.context.get_current_tenant")
@patch("src.app.modules.billing.subscription_service.crud_billing_records")
async def test_billing_record_service_operations(mock_crud, mock_get_ctx, mock_get_tenant, mock_db, mock_tenant):
    mock_get_tenant.return_value = mock_tenant
    mock_get_ctx.return_value = mock_tenant
    mock_crud.create = AsyncMock(return_value={"id": 1, "amount_cents": 1000})
    mock_crud.get = AsyncMock(return_value={"id": 1, "amount_cents": 1000, "tenant_id": mock_tenant.id})
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
@patch("src.app.modules.billing.checkout_service.require_active_tenant")
@patch("src.app.modules.billing.service.StripeSettingsService.get_settings", new_callable=AsyncMock)
@patch("src.app.clients.billing.stripe.get_payment_gateway")
async def test_checkout_service_create_portal_session(
    mock_gateway_fn, mock_get_settings, mock_get_tenant, mock_db, mock_tenant
):
    mock_get_tenant.return_value = mock_tenant
    mock_get_settings.return_value = {"stripe_customer_id": "cus_123"}

    mock_session = MagicMock()
    mock_session.url = "https://billing.stripe.com/p/session/test"
    mock_gateway = AsyncMock()
    mock_gateway.get_api_key = AsyncMock(return_value="sk_test_123")
    mock_gateway.create_portal_session = AsyncMock(return_value=mock_session)
    mock_gateway_fn.return_value = mock_gateway

    service = CheckoutService(mock_db)

    url = await service.create_portal_session("/billing")
    assert url == "https://billing.stripe.com/p/session/test"
