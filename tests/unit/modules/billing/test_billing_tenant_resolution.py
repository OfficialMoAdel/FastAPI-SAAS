"""Unit tests for Candidate 7: Resolve Tenant Once at Service Construction (Billing)."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.app.modules.billing.service import PricingService, ProductService, StripeSettingsService
from src.app.modules.billing.subscription_service import SubscriptionPlanService, UserSubscriptionService
from src.app.shared.exceptions import ModelNotFoundError


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

    # A1: every service resolves through the ONE seam, so a missing scope
    # fails closed with the same error regardless of service.
    for service, entity in (
        (product_svc, "Product"),
        (pricing_svc, "Pricing"),
        (sub_svc, "SubscriptionPlan"),
        (user_sub_svc, "UserSubscription"),
    ):
        assert service.current_tenant_id is None
        with pytest.raises(ModelNotFoundError):
            service._resolve_tenant(entity, 1)


@pytest.mark.asyncio
async def test_current_tenant_id_is_a_view_over_the_seam():
    """A1: the property delegates to the seam instead of re-deriving precedence."""
    from src.app.core.tenancy.context import (
        TenantContext,
        clear_tenant_context,
        set_current_tenant,
    )

    mock_db = AsyncMock()
    tid = uuid4()
    explicit_only = (
        ProductService(mock_db, tenant_id=tid),
        PricingService(mock_db, tenant_id=tid),
        SubscriptionPlanService(mock_db, tenant_id=tid),
        UserSubscriptionService(mock_db, tenant_id=tid),
        StripeSettingsService(mock_db, tenant_id=tid),
    )

    clear_tenant_context()
    for service in explicit_only:
        assert service.current_tenant_id == tid

    # Tenant-less path with no construction value: None, not a raise.
    assert ProductService(mock_db).current_tenant_id is None

    set_current_tenant(
        TenantContext(tenant_id=tid, tenant_slug="acme", tenant_name="Acme", is_active=True)
    )
    try:
        for service in explicit_only:
            assert service.current_tenant_id == tid

        # A construction value outside the bound context is the scope
        # mismatch the seam soft-404s — the view must not silently shadow it.
        with pytest.raises(ModelNotFoundError):
            ProductService(mock_db, tenant_id=uuid4()).current_tenant_id
    finally:
        clear_tenant_context()


@pytest.mark.asyncio
async def test_resolve_tenant_rejects_cross_tenant_explicit():
    """A1: resolution rejects a constructor value outside the active context."""
    from src.app.core.tenancy.context import (
        TenantContext,
        clear_tenant_context,
        set_current_tenant,
    )

    set_current_tenant(
        TenantContext(tenant_id=uuid4(), tenant_slug="acme", tenant_name="Acme", is_active=True)
    )
    try:
        mock_db = AsyncMock()
        with pytest.raises(ModelNotFoundError):
            ProductService(mock_db, tenant_id=uuid4())._resolve_tenant("Product", 1)
    finally:
        clear_tenant_context()
