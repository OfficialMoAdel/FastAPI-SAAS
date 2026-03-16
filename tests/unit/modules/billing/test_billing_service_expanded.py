from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.billing.schemas import (
    CheckoutCreate,
    PricingCreate,
    ProductCreate,
    SubscriptionPlanCreate,
)
from src.app.modules.billing.service import (
    CheckoutService,
    PricingService,
    ProductService,
    StripeSettingsService,
    SubscriptionPlanService,
)


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def mock_tenant():
    tenant = MagicMock()
    tenant.id = uuid4()
    tenant.is_active = True
    return tenant


@pytest.mark.asyncio
@patch("src.app.modules.billing.service.get_current_tenant")
@patch("src.app.modules.billing.service.crud_stripe_settings")
async def test_stripe_settings_service_get(mock_crud, mock_get_tenant, mock_db, mock_tenant):
    mock_get_tenant.return_value = mock_tenant
    mock_crud.get = AsyncMock(return_value={"id": 1, "stripe_customer_id": "cus_123"})

    service = StripeSettingsService(mock_db)
    settings = await service.get_settings()

    assert settings["stripe_customer_id"] == "cus_123"


@pytest.mark.asyncio
@patch("src.app.modules.billing.service.get_current_tenant")
@patch("src.app.modules.billing.service.crud_subscription_plans")
async def test_subscription_plan_service_create(mock_crud, mock_get_tenant, mock_db, mock_tenant):
    mock_get_tenant.return_value = mock_tenant
    mock_crud.create = AsyncMock(return_value={"id": 1, "name": "Pro"})

    service = SubscriptionPlanService(mock_db)
    data = SubscriptionPlanCreate(
        name="Pro",
        sort_order=1,
        price_monthly_cents=1000,
        price_yearly_cents=10000,
        features=[],
    )
    result = await service.create_plan(data)

    assert result["name"] == "Pro"
    mock_crud.create.assert_awaited_once()


@pytest.mark.asyncio
@patch("src.app.modules.billing.service.get_current_tenant")
@patch("src.app.modules.billing.service.crud_products")
async def test_product_service_create(mock_crud, mock_get_tenant, mock_db, mock_tenant):
    mock_get_tenant.return_value = mock_tenant
    mock_crud.create = AsyncMock(return_value={"id": 1, "name": "Test Product"})

    service = ProductService(mock_db)
    data = ProductCreate(name="Test Product")
    result = await service.create_product(data)

    assert result["name"] == "Test Product"


@pytest.mark.asyncio
@patch("src.app.modules.billing.service.crud_pricing")
@patch("src.app.modules.billing.service.crud_products")
async def test_pricing_service_create(mock_crud_products, mock_crud_pricing, mock_db):
    mock_crud_products.get = AsyncMock(return_value={"id": 1})
    mock_crud_pricing.create = AsyncMock(return_value={"id": 1, "amount_cents": 1000})

    service = PricingService(mock_db)
    data = PricingCreate(name="Basic", amount_cents=1000, currency="usd", interval="monthly")
    result = await service.create_pricing(1, data)

    assert result["amount_cents"] == 1000


@pytest.mark.asyncio
@patch("src.app.modules.billing.service.get_current_tenant")
@patch("src.app.modules.billing.service.StripeSettingsService.get_settings")
@patch("src.app.modules.billing.service.SubscriptionPlanService.get_plan")
@patch("src.app.modules.billing.service.settings")
@patch("asyncio.to_thread")
async def test_checkout_service_create_session(
    mock_to_thread, mock_settings, mock_get_plan, mock_get_settings, mock_get_tenant, mock_db, mock_tenant
):  # noqa: E501
    mock_get_tenant.return_value = mock_tenant

    # Mock settings.STRIPE_SECRET_KEY
    mock_secret = MagicMock()
    mock_secret.get_secret_value.return_value = "sk_test_123"
    mock_settings.STRIPE_SECRET_KEY = mock_secret

    # Needs a real API key to bypass Stripe API validation internally perhaps? No, to_thread is mocked.
    mock_get_settings.return_value = {"stripe_customer_id": "cus_123"}
    mock_get_plan.return_value = {
        "stripe_price_id_monthly": "price_monthly",
        "stripe_price_id_yearly": "price_yearly",
    }

    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/c/pay/cs_test_123"
    mock_session.id = "cs_test_123"
    mock_session.expires_at = 1600000000
    mock_to_thread.return_value = mock_session

    service = CheckoutService(mock_db)
    from src.app.core.config import settings as app_settings
    frontend = app_settings.FRONTEND_URL.rstrip('/') if app_settings.FRONTEND_URL else "https://example.com"
    data = CheckoutCreate(
        plan_id=1, billing_interval="monthly", success_url=f"{frontend}/success", cancel_url=f"{frontend}/cancel"
    )

    result = await service.create_session(user_id=1, user_email="test@example.com", data=data)

    assert result.checkout_url == "https://checkout.stripe.com/c/pay/cs_test_123"
    assert result.session_id == "cs_test_123"
    mock_to_thread.assert_awaited_once()
