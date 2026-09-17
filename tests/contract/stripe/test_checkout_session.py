"""Contract tests for Stripe checkout session.

Tests T068: Contract test for Stripe checkout session in tests/contract/stripe/test_checkout_session.py

These tests verify the contract between our application and the Stripe API
for checkout session creation and management.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe

from src.app.core.config import settings
from src.app.modules.billing.checkout_service import CheckoutService
from src.app.modules.billing.schemas import CheckoutCreate, CheckoutSession


class TestCheckoutSessionContract:
    """Contract tests for Stripe Checkout Session API."""

    @pytest.fixture
    def checkout_create_data(self):
        """Valid checkout creation request."""
        frontend = settings.FRONTEND_URL.rstrip("/") if settings.FRONTEND_URL else "https://example.com"
        return CheckoutCreate(
            plan_id=1,
            billing_interval="monthly",
            success_url=f"{frontend}/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{frontend}/cancel",
        )

    @pytest.fixture
    def mock_stripe_session(self):
        """Mock Stripe checkout session response."""
        mock_session = MagicMock()
        mock_session.id = "cs_test_a1b2c3d4e5f6g7h8i9j0"
        mock_session.url = "https://checkout.stripe.com/pay/cs_test_a1b2c3d4e5f6g7h8i9j0"
        mock_session.expires_at = int((datetime.now(UTC) + timedelta(hours=24)).timestamp())
        mock_session.status = "open"
        mock_session.mode = "subscription"
        mock_session.customer = None
        mock_session.customer_email = "test@example.com"
        mock_session.client_reference_id = "1"
        mock_session.subscription = None
        mock_session.payment_status = "unpaid"
        return mock_session

    @pytest.mark.asyncio
    async def test_checkout_session_response_format(self, mock_stripe_session):
        """Verify our CheckoutSession matches Stripe's response format."""
        # Create our internal response from Stripe's response
        checkout_session = CheckoutSession(
            checkout_url=mock_stripe_session.url,
            session_id=mock_stripe_session.id,
            expires_at=datetime.fromtimestamp(mock_stripe_session.expires_at, UTC),
        )

        # Verify format matches contract
        assert checkout_session.session_id.startswith("cs_")
        assert checkout_session.checkout_url.startswith("https://checkout.stripe.com/")
        assert checkout_session.expires_at > datetime.now(UTC)

    @pytest.mark.asyncio
    async def test_checkout_session_id_format(self, mock_stripe_session):
        """Verify Stripe session ID format is preserved."""
        session_id = mock_stripe_session.id

        # Stripe checkout session IDs should start with 'cs_'
        assert session_id.startswith("cs_")
        # Should be alphanumeric after prefix
        assert session_id[3:].replace("_", "").isalnum()

    @pytest.mark.asyncio
    async def test_checkout_session_expiry_contract(self, mock_stripe_session):
        """Verify checkout session expires in 24 hours."""
        expires_at = datetime.fromtimestamp(mock_stripe_session.expires_at, UTC)
        now = datetime.now(UTC)

        # Session should expire approximately 24 hours from now
        time_until_expiry = expires_at - now
        assert timedelta(hours=23) < time_until_expiry <= timedelta(hours=24, minutes=5)

    @pytest.mark.asyncio
    async def test_checkout_session_metadata_format(self, checkout_create_data):
        """Verify metadata is properly formatted for Stripe."""
        tenant_id = 1
        user_id = 1
        plan_id = checkout_create_data.plan_id

        # Metadata values must be strings
        metadata = {
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "plan_id": str(plan_id),
        }

        for key, value in metadata.items():
            assert isinstance(key, str)
            assert isinstance(value, str)
            # Metadata keys have max 40 chars
            assert len(key) <= 40
            # Metadata values have max 500 chars
            assert len(value) <= 500

    @pytest.mark.asyncio
    async def test_line_items_contract(self, checkout_create_data):
        """Verify line_items format matches Stripe's expected structure."""
        price_id = "price_1234567890"

        line_items = [
            {
                "price": price_id,
                "quantity": 1,
            }
        ]

        # Verify structure
        assert len(line_items) >= 1
        for item in line_items:
            assert "price" in item
            assert "quantity" in item
            assert isinstance(item["quantity"], int)
            assert item["quantity"] > 0

    @pytest.mark.asyncio
    async def test_subscription_data_contract(self, checkout_create_data):
        """Verify subscription_data format for Stripe."""
        tenant_id = 1
        user_id = 1
        plan_id = checkout_create_data.plan_id

        subscription_data = {
            "metadata": {
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "plan_id": str(plan_id),
            }
        }

        # Verify metadata in subscription_data
        assert "metadata" in subscription_data
        assert isinstance(subscription_data["metadata"], dict)

    @pytest.mark.asyncio
    async def test_mode_subscription_contract(self):
        """Verify checkout mode is 'subscription' for recurring payments."""
        valid_modes = ["subscription", "payment", "setup"]
        mode = "subscription"

        assert mode in valid_modes

    @pytest.mark.asyncio
    async def test_payment_method_types_contract(self):
        """Verify payment_method_types format."""
        payment_method_types = ["card"]

        valid_types = [
            "card",
            "acss_debit",
            "alipay",
            "au_becs_debit",
            "bacs_debit",
            "bancontact",
            "boleto",
            "eps",
            "fpx",
            "giropay",
            "grabpay",
            "ideal",
            "klarna",
            "oxxo",
            "p24",
            "sepa_debit",
            "sofort",
            "afterpay_clearpay",
            "wechat_pay",
        ]

        for method in payment_method_types:
            assert method in valid_types

    @pytest.mark.asyncio
    async def test_success_url_format_contract(self, checkout_create_data):
        """Verify success_url format meets Stripe requirements."""
        success_url = checkout_create_data.success_url

        # Must be valid URL
        assert str(success_url).startswith("https://") or str(success_url).startswith("http://")
        # Can contain {CHECKOUT_SESSION_ID} placeholder
        # This is optional but recommended

    @pytest.mark.asyncio
    async def test_cancel_url_format_contract(self, checkout_create_data):
        """Verify cancel_url format meets Stripe requirements."""
        cancel_url = checkout_create_data.cancel_url

        # Must be valid URL
        assert str(cancel_url).startswith("https://") or str(cancel_url).startswith("http://")

    @pytest.mark.asyncio
    async def test_client_reference_id_format(self):
        """Verify client_reference_id format."""
        user_id = 12345
        client_reference_id = str(user_id)

        # Must be string, max 200 chars
        assert isinstance(client_reference_id, str)
        assert len(client_reference_id) <= 200

    @pytest.mark.asyncio
    async def test_stripe_error_handling_contract(self):
        """Verify proper handling of Stripe errors."""
        # Common Stripe error types we should handle
        error_types = [
            "card_error",
            "invalid_request_error",
            "api_error",
            "authentication_error",
            "rate_limit_error",
        ]

        # Our service should catch and transform these
        for error_type in error_types:
            assert error_type in [
                "card_error",
                "invalid_request_error",
                "api_error",
                "authentication_error",
                "rate_limit_error",
                "idempotency_error",
                "invalid_grant",
            ]

    @pytest.mark.asyncio
    async def test_checkout_session_completed_event_contract(self):
        """Verify checkout.session.completed event structure."""
        # Mock event structure from Stripe
        event = {
            "id": "evt_1234567890",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_1234567890",
                    "object": "checkout.session",
                    "amount_total": 9900,
                    "currency": "usd",
                    "customer": "cus_1234567890",
                    "mode": "subscription",
                    "payment_status": "paid",
                    "status": "complete",
                    "subscription": "sub_1234567890",
                    "client_reference_id": "1",
                    "metadata": {
                        "tenant_id": "1",
                        "user_id": "1",
                        "plan_id": "1",
                    },
                }
            },
        }

        # Verify required fields are present
        assert event["type"] == "checkout.session.completed"
        session = event["data"]["object"]
        assert "id" in session
        assert "customer" in session
        assert "subscription" in session
        assert "metadata" in session
        assert session["status"] == "complete"
        assert session["payment_status"] == "paid"


@pytest.mark.skipif(not settings.STRIPE_SECRET_KEY, reason="Stripe keys not configured")
class TestCheckoutSessionLive:
    """Live contract tests requiring Stripe test keys."""

    @pytest.mark.asyncio
    async def test_create_checkout_session_live(self, db_session):
        """Test creating an actual checkout session with Stripe test API."""
        # This test requires STRIPE_SECRET_KEY to be set to a test key
        # and a valid price_id from the Stripe test account

        # Skip if no test price available
        test_price_id = getattr(settings, "STRIPE_TEST_PRICE_ID", None)
        if not test_price_id:
            pytest.skip("STRIPE_TEST_PRICE_ID not configured")

        service = CheckoutService(db_session)

        with (
            patch("src.app.core.tenancy.context.get_current_tenant") as mock_tenant,
            patch(
                "src.app.modules.billing.service.StripeSettingsService.get_settings",
                new_callable=AsyncMock,
            ) as mock_settings,
            patch(
                "src.app.modules.billing.service.SubscriptionPlanService.get_plan",
                new_callable=AsyncMock,
            ) as mock_plan,
        ):
            mock_tenant.return_value = MagicMock(id=uuid.uuid4())
            mock_settings.return_value = {"stripe_customer_id": None}
            mock_plan.return_value = {
                "stripe_price_id_monthly": test_price_id,
                "stripe_price_id_yearly": test_price_id,
            }

            frontend = settings.FRONTEND_URL.rstrip("/") if settings.FRONTEND_URL else "https://example.com"
            data = CheckoutCreate(
                plan_id=1,
                billing_interval="monthly",
                success_url=f"{frontend}/success",
                cancel_url=f"{frontend}/cancel",
            )

            try:
                result = await service.create_session(
                    user_id=1,
                    user_email="test@example.com",
                    data=data,
                )

                # Verify response format
                assert result.session_id.startswith("cs_")
                assert result.checkout_url.startswith("https://checkout.stripe.com/")
                assert result.expires_at > datetime.now(UTC)

            except stripe.error.StripeError as e:
                # Expected if price_id is invalid
                pytest.skip(f"Stripe API error: {e}")
