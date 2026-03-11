from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from src.app.modules.billing.service import CheckoutCreate, CheckoutService


@pytest.mark.asyncio
async def test_create_session_success(db_session, tenant):
    """Test successful checkout session creation."""
    # Mock settings to ensure key exists
    with patch("src.app.core.config.settings.STRIPE_SECRET_KEY", SecretStr("sk_test_123")):
        service = CheckoutService(db_session)

        # Mock dependencies
        with (
            patch("src.app.modules.billing.service.get_current_tenant", return_value=tenant),
            patch(
                "src.app.modules.billing.service.StripeSettingsService.get_settings",
                return_value={"stripe_customer_id": "cus_123"},
            ),
            patch(
                "src.app.modules.billing.service.SubscriptionPlanService.get_plan",
                return_value={"stripe_price_id_monthly": "price_123"},
            ),
            patch("stripe.checkout.Session.create") as mock_stripe_create,
        ):
            mock_stripe_create.return_value = MagicMock(
                url="https://checkout.stripe.com/...", id="cs_test_123", expires_at=1234567890
            )

            data = CheckoutCreate(
                plan_id=1,
                billing_interval="monthly",
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel",
            )

            result = await service.create_session(user_id=1, user_email="test@example.com", data=data)

            assert result.session_id == "cs_test_123"
            assert result.checkout_url == "https://checkout.stripe.com/..."

            mock_stripe_create.assert_called_once()
            call_kwargs = mock_stripe_create.call_args[1]
            assert call_kwargs["api_key"] == "sk_test_123"
            assert call_kwargs["customer"] == "cus_123"
            assert call_kwargs["line_items"][0]["price"] == "price_123"


@pytest.mark.asyncio
async def test_create_session_no_api_key(db_session, tenant):
    """Test error when API key is missing."""
    with patch("src.app.core.config.settings.STRIPE_SECRET_KEY", None):
        service = CheckoutService(db_session)
        with patch("src.app.modules.billing.service.get_current_tenant", return_value=tenant):
            data = CheckoutCreate(plan_id=1, billing_interval="monthly", success_url="x", cancel_url="x")
            with pytest.raises(HTTPException) as exc:
                await service.create_session(1, "x", data)
            assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_create_portal_session_success(db_session, tenant):
    """Test successful portal session creation."""
    # Mock settings to ensure key exists
    with patch("src.app.core.config.settings.STRIPE_SECRET_KEY", SecretStr("sk_test_123")):
        service = CheckoutService(db_session)

        with (
            patch("src.app.modules.billing.service.get_current_tenant", return_value=tenant),
            patch(
                "src.app.modules.billing.service.StripeSettingsService.get_settings",
                return_value={"stripe_customer_id": "cus_123"},
            ),
            patch("stripe.billing_portal.Session.create") as mock_portal_create,
        ):
            mock_portal_create.return_value = MagicMock(url="https://billing.stripe.com/...")

            url = await service.create_portal_session(return_url="/dashboard")

            assert url == "https://billing.stripe.com/..."


@pytest.mark.asyncio
async def test_create_session_has_tenant_metadata(db_session, tenant):
    """Test that checkout session creation includes tenant_id in metadata."""
    # Mock settings
    with patch("src.app.core.config.settings.STRIPE_SECRET_KEY", SecretStr("sk_test_123")):
        service = CheckoutService(db_session)

        # Mock dependencies
        with (
            patch("src.app.modules.billing.service.get_current_tenant", return_value=tenant),
            patch(
                "src.app.modules.billing.service.StripeSettingsService.get_settings",
                return_value={"stripe_customer_id": "cus_123"},
            ),
            patch(
                "src.app.modules.billing.service.SubscriptionPlanService.get_plan",
                return_value={"stripe_price_id_monthly": "price_123"},
            ),
            patch("stripe.checkout.Session.create") as mock_stripe_create,
        ):
            mock_stripe_create.return_value = MagicMock(
                url="https://checkout.stripe.com/...", id="cs_test_123", expires_at=1234567890
            )

            data = CheckoutCreate(
                plan_id=1,
                billing_interval="monthly",
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel",
            )

            # Act
            await service.create_session(user_id=1, user_email="test@example.com", data=data)

            # Assert
            mock_stripe_create.assert_called_once()
            call_kwargs = mock_stripe_create.call_args[1]

            # 1. Verify Session Metadata
            assert "metadata" in call_kwargs
            assert call_kwargs["metadata"]["tenant_id"] == str(tenant.id)
            assert call_kwargs["metadata"]["user_id"] == "1"

            # 2. Verify Subscription Metadata
            assert "subscription_data" in call_kwargs
            assert "metadata" in call_kwargs["subscription_data"]
            assert call_kwargs["subscription_data"]["metadata"]["tenant_id"] == str(tenant.id)
            assert call_kwargs["subscription_data"]["metadata"]["user_id"] == "1"
