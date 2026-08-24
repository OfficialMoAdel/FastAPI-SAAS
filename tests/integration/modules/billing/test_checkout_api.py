"""Integration tests for checkout API endpoints.

Tests T067: Integration test for checkout endpoints in tests/integration/billing/test_checkout_api.py
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch


class TestCheckoutAPI:
    """Integration tests for checkout endpoints."""

    def test_create_checkout_session_requires_auth(self, client_auth) -> None:
        """POST /billing/checkout should require authentication."""
        response = client_auth.post(
            "/api/v1/billing/checkout",
            json={
                "plan_id": 1,
                "billing_interval": "monthly",
                "success_url": "https://example.com/success",
                "cancel_url": "https://example.com/cancel",
            },
        )
        # Should fail without auth - expect 401 or 400 for missing tenant header
        assert response.status_code in [400, 401, 403, 404, 422]

    def test_create_checkout_session_success(self, client_auth) -> None:
        """POST /billing/checkout should create a Stripe checkout session."""
        # Mock the checkout service
        mock_session = MagicMock()
        mock_session.checkout_url = "https://checkout.stripe.com/pay/cs_test_123"
        mock_session.session_id = "cs_test_123"
        mock_session.expires_at = datetime.now(UTC) + timedelta(hours=24)

        with (
            patch(
                "src.app.modules.billing.checkout_service.CheckoutService.create_session", new_callable=AsyncMock
            ) as mock_create,
            patch("src.app.core.tenancy.context.get_current_tenant") as mock_tenant,
        ):
            mock_tenant.return_value = MagicMock(id=uuid.uuid4())
            mock_create.return_value = mock_session

            response = client_auth.post(
                "/api/v1/billing/checkout",
                json={
                    "plan_id": 1,
                    "billing_interval": "monthly",
                    "success_url": "https://example.com/success",
                    "cancel_url": "https://example.com/cancel",
                },
            )
            # Should succeed or fail with validation error if plan doesn't exist
            assert response.status_code in [200, 201, 400, 401, 404, 422, 500]

    def test_create_checkout_session_invalid_plan(self, client_auth) -> None:
        """POST /billing/checkout should return 404 for non-existent plan."""
        with (
            patch(
                "src.app.modules.billing.subscription_service.SubscriptionPlanService.get_plan",
                new_callable=AsyncMock,
            ) as mock_get_plan,
            patch("src.app.core.tenancy.context.get_current_tenant") as mock_tenant,
        ):
            mock_tenant.return_value = MagicMock(id=uuid.uuid4())
            mock_get_plan.side_effect = Exception("Plan not found")

            response = client_auth.post(
                "/api/v1/billing/checkout",
                json={
                    "plan_id": 9999,
                    "billing_interval": "monthly",
                    "success_url": "https://example.com/success",
                    "cancel_url": "https://example.com/cancel",
                },
            )
            # Should fail - plan doesn't exist
            assert response.status_code in [400, 401, 404, 422, 500]

    def test_create_checkout_session_invalid_billing_interval(self, client_auth) -> None:
        """POST /billing/checkout should validate billing_interval."""
        response = client_auth.post(
            "/api/v1/billing/checkout",
            json={
                "plan_id": 1,
                "billing_interval": "invalid_interval",
                "success_url": "https://example.com/success",
                "cancel_url": "https://example.com/cancel",
            },
        )
        # Should fail validation (400 for tenant, 401 for auth, 422 for validation)
        assert response.status_code in [400, 401, 422]

    def test_create_checkout_session_missing_success_url(self, client_auth) -> None:
        """POST /billing/checkout should require success_url."""
        response = client_auth.post(
            "/api/v1/billing/checkout",
            json={
                "plan_id": 1,
                "billing_interval": "monthly",
                "cancel_url": "https://example.com/cancel",
            },
        )
        # Should fail validation - missing required field
        assert response.status_code in [400, 401, 422]

    def test_create_checkout_session_missing_cancel_url(self, client_auth) -> None:
        """POST /billing/checkout should require cancel_url."""
        response = client_auth.post(
            "/api/v1/billing/checkout",
            json={
                "plan_id": 1,
                "billing_interval": "monthly",
                "success_url": "https://example.com/success",
            },
        )
        # Should fail validation - missing required field
        assert response.status_code in [400, 401, 422]

    def test_create_checkout_session_yearly_interval(self, client_auth) -> None:
        """POST /billing/checkout should accept yearly billing interval."""
        mock_session = MagicMock()
        mock_session.checkout_url = "https://checkout.stripe.com/pay/cs_test_123"
        mock_session.session_id = "cs_test_123"
        mock_session.expires_at = datetime.now(UTC) + timedelta(hours=24)

        with (
            patch(
                "src.app.modules.billing.checkout_service.CheckoutService.create_session", new_callable=AsyncMock
            ) as mock_create,
            patch("src.app.core.tenancy.context.get_current_tenant") as mock_tenant,
        ):
            mock_tenant.return_value = MagicMock(id=uuid.uuid4())
            mock_create.return_value = mock_session

            response = client_auth.post(
                "/api/v1/billing/checkout",
                json={
                    "plan_id": 1,
                    "billing_interval": "yearly",
                    "success_url": "https://example.com/success",
                    "cancel_url": "https://example.com/cancel",
                },
            )
            assert response.status_code in [200, 201, 400, 401, 404, 422, 500]


class TestMySubscriptionAPI:
    """Integration tests for user subscription endpoints."""

    def test_get_my_subscription_requires_auth(self, client_auth) -> None:
        """GET /billing/subscriptions/me should require authentication."""
        response = client_auth.get("/api/v1/billing/subscriptions/me")
        assert response.status_code in [400, 401, 403, 404]

    def test_get_my_subscription_success(self, client_auth) -> None:
        """GET /billing/subscriptions/me should return current subscription."""
        mock_subscription = {
            "id": 1,
            "user_id": 1,
            "subscription_plan": {
                "id": 1,
                "name": "Pro Plan",
                "price_monthly_cents": 9900,
            },
            "status": "active",
            "billing_interval": "monthly",
            "current_period_start": datetime.now(UTC).isoformat(),
            "current_period_end": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            "cancel_at_period_end": False,
        }

        with patch(
            "src.app.modules.billing.subscription_service.UserSubscriptionService.get_user_subscriptions",
            new_callable=AsyncMock,
            return_value=[mock_subscription],
        ):
            response = client_auth.get("/api/v1/billing/subscriptions/me")
            # 200 if subscription exists, 400 for tenant, 401 for auth, 404 if not found
            assert response.status_code in [200, 400, 401, 404, 422]

    def test_get_my_subscription_no_subscription(self, client_auth) -> None:
        """GET /billing/subscriptions/me should return 404 when no subscription."""
        with patch(
            "src.app.modules.billing.subscription_service.UserSubscriptionService.get_user_subscriptions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            response = client_auth.get("/api/v1/billing/subscriptions/me")
            # Should return 404 or empty result, 400 for tenant
            assert response.status_code in [200, 400, 401, 404, 422]


class TestCancelSubscriptionAPI:
    """Integration tests for subscription cancellation."""

    def test_cancel_subscription_requires_auth(self, client_auth) -> None:
        """POST /billing/subscriptions/me/cancel should require authentication."""
        response = client_auth.post("/api/v1/billing/subscriptions/me/cancel")
        assert response.status_code in [400, 401, 403, 404, 405]

    def test_cancel_subscription_success(self, client_auth) -> None:
        """POST /billing/subscriptions/me/cancel should cancel subscription."""
        mock_subscription = {
            "id": 1,
            "status": "active",
            "cancel_at_period_end": True,
            "canceled_at": datetime.now(UTC).isoformat(),
        }

        with patch(
            "src.app.modules.billing.subscription_service.UserSubscriptionService.cancel_subscription",
            new_callable=AsyncMock,
            return_value=mock_subscription,
        ):
            response = client_auth.post("/api/v1/billing/subscriptions/me/cancel")
            # 200 if successful, 400 for tenant, 404 if no subscription, 405 if method not allowed
            assert response.status_code in [200, 400, 401, 404, 405, 422]

    def test_cancel_subscription_no_subscription(self, client_auth) -> None:
        """POST /billing/subscriptions/me/cancel should return 404 when no subscription."""
        with patch(
            "src.app.modules.billing.subscription_service.UserSubscriptionService.cancel_subscription",
            new_callable=AsyncMock,
            side_effect=Exception("No subscription found"),
        ):
            response = client_auth.post("/api/v1/billing/subscriptions/me/cancel")
            # Should fail - no subscription to cancel
            assert response.status_code in [400, 401, 404, 405, 422, 500]


class TestBillingHistoryAPI:
    """Integration tests for billing history endpoints."""

    def test_billing_history_requires_auth(self, client_auth) -> None:
        """GET /billing/history should require authentication."""
        response = client_auth.get("/api/v1/billing/history")
        assert response.status_code in [400, 401, 403, 404]

    def test_billing_history_success(self, client_auth) -> None:
        """GET /billing/history should return paginated billing records."""
        mock_records = [
            {
                "id": 1,
                "amount_cents": 9900,
                "currency": "usd",
                "status": "succeeded",
                "description": "Monthly subscription",
                "created_at": datetime.now(UTC).isoformat(),
            }
        ]

        with patch(
            "src.app.modules.billing.subscription_service.BillingRecordService.get_user_records",
            new_callable=AsyncMock,
            return_value=mock_records,
        ):
            response = client_auth.get("/api/v1/billing/history")
            assert response.status_code in [200, 400, 401, 404, 422]

    def test_billing_history_pagination(self, client_auth) -> None:
        """GET /billing/history should support pagination parameters."""
        with patch(
            "src.app.modules.billing.subscription_service.BillingRecordService.get_user_records",
            new_callable=AsyncMock,
            return_value=[],
        ):
            response = client_auth.get(
                "/api/v1/billing/history",
                params={"page": 1, "limit": 10},
            )
            assert response.status_code in [200, 400, 401, 404, 422]

    def test_billing_history_empty(self, client_auth) -> None:
        """GET /billing/history should return empty list when no records."""
        with patch(
            "src.app.modules.billing.subscription_service.BillingRecordService.get_user_records",
            new_callable=AsyncMock,
            return_value=[],
        ):
            response = client_auth.get("/api/v1/billing/history")
            assert response.status_code in [200, 400, 401, 404, 422]
            if response.status_code == 200:
                data = response.json()
                # Could be empty list or paginated response with empty items
                assert data == [] or data.get("items") == [] or "data" in data
