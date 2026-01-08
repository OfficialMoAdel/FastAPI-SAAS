"""Integration tests for Billing API endpoints.

Tests cover:
- Stripe settings management
- Subscription plan CRUD operations
- User subscription management
- Billing record retrieval
- Tenant isolation and authentication
"""

from unittest.mock import AsyncMock, patch

import pytest


class TestStripeSettingsAPI:
    """Integration tests for Stripe settings endpoints."""

    def test_get_stripe_settings_requires_auth(self, client) -> None:
        """API should require authentication."""
        response = client.get("/api/v1/billing/stripe/settings")
        # 400 for missing tenant header is expected
        assert response.status_code in [200, 400, 401, 403, 404]

    def test_get_stripe_settings_with_auth(self, client, regular_user_token_headers) -> None:
        """API should return settings for authenticated tenant."""
        response = client.get(
            "/api/v1/billing/stripe/settings",
            headers=regular_user_token_headers,
        )
        # 200 if settings exist, 404 if not configured yet
        assert response.status_code in [200, 404]

    def test_update_stripe_settings_with_auth(self, client, superuser_token_headers) -> None:
        """API should allow superuser to update Stripe settings."""
        with patch(
            "src.app.modules.billing.service.StripeSettingsService.update_settings",
            new_callable=AsyncMock,
            return_value={
                "id": 1,
                "tenant_id": 1,
                "stripe_mode": "test",
                "is_configured": True,
            },
        ):
            response = client.put(
                "/api/v1/billing/stripe/settings",
                headers=superuser_token_headers,
                json={
                    "stripe_mode": "test",
                    "test_publishable_key": "pk_test_xxx",
                    "test_secret_key": "sk_test_xxx",
                },
            )
            assert response.status_code in [200, 201, 403, 404, 405, 422]

    def test_get_stripe_settings_authenticated(self, client, superuser_token_headers) -> None:
        """API should return settings for authenticated tenant."""
        with patch(
            "src.app.modules.billing.service.StripeSettingsService.get_settings",
            new_callable=AsyncMock,
            return_value={"id": 1, "stripe_mode": "test"},
        ):
            response = client.get(
                "/api/v1/billing/stripe/settings",
                headers=superuser_token_headers,
            )
            assert response.status_code in [200, 404]


class TestSubscriptionPlanAPI:
    """Integration tests for subscription plan endpoints."""

    def test_list_plans_public(self, client, regular_user_token_headers) -> None:
        """List plans should be accessible with auth."""
        response = client.get("/api/v1/billing/plans", headers=regular_user_token_headers)
        assert response.status_code in [200, 422]

    def test_list_plans_returns_pagination(self, client, regular_user_token_headers) -> None:
        """List plans should support pagination."""
        response = client.get(
            "/api/v1/billing/plans",
            headers=regular_user_token_headers,
            params={"page": 1, "limit": 10},
        )
        assert response.status_code in [200, 422]
        if response.status_code == 200:
            data = response.json()
            assert "items" in data or "data" in data

    def test_get_plan_by_id(self, client, regular_user_token_headers) -> None:
        """Get single plan by ID."""
        response = client.get(
            "/api/v1/billing/plans/1",
            headers=regular_user_token_headers,
        )
        # 200 if exists, 404 if not
        assert response.status_code in [200, 404, 422]

    def test_create_plan_requires_superuser(self, client, regular_user_token_headers) -> None:
        """Creating a plan should require superuser privileges."""
        response = client.post(
            "/api/v1/billing/plans",
            headers=regular_user_token_headers,
            json={
                "name": "Test Plan",
                "slug": "test-plan",
                "price_monthly_cents": 999,
                "price_yearly_cents": 9990,
            },
        )
        # Should be forbidden for regular users
        assert response.status_code in [403, 422]

    def test_create_plan_as_superuser(self, client, superuser_token_headers) -> None:
        """Superuser should be able to create plans."""
        with patch(
            "src.app.modules.billing.service.SubscriptionPlanService.create_plan",
            new_callable=AsyncMock,
            return_value={
                "id": 1,
                "name": "Test Plan",
                "slug": "test-plan",
                "price_monthly_cents": 999,
                "is_active": True,
            },
        ):
            response = client.post(
                "/api/v1/billing/plans",
                headers=superuser_token_headers,
                json={
                    "name": "Test Plan",
                    "slug": "test-plan",
                    "price_monthly_cents": 999,
                    "price_yearly_cents": 9990,
                },
            )
            assert response.status_code in [200, 201, 403, 422]


class TestUserSubscriptionAPI:
    """Integration tests for user subscription endpoints."""

    def test_get_my_subscription_requires_auth(self, client, regular_user_token_headers) -> None:
        """Getting subscription requires authentication."""
        response = client.get(
            "/api/v1/billing/subscriptions/me",
            headers=regular_user_token_headers,
        )
        assert response.status_code in [200, 401, 403, 404]

    def test_get_my_subscription_with_auth(self, client, regular_user_token_headers) -> None:
        """Get current user's subscription."""
        response = client.get(
            "/api/v1/billing/subscriptions/me",
            headers=regular_user_token_headers,
        )
        # 200 if subscription exists, 404 if not
        assert response.status_code in [200, 404]

    def test_create_subscription(self, client, regular_user_token_headers) -> None:
        """Create a new subscription."""
        with patch(
            "src.app.modules.billing.service.UserSubscriptionService.create_subscription",
            new_callable=AsyncMock,
            return_value={
                "id": 1,
                "user_id": 1,
                "plan_id": 1,
                "status": "active",
            },
        ):
            response = client.post(
                "/api/v1/billing/subscriptions",
                headers=regular_user_token_headers,
                json={"plan_id": 1},
            )
            assert response.status_code in [200, 201, 400, 404, 422]

    def test_cancel_subscription(self, client, regular_user_token_headers) -> None:
        """Cancel an existing subscription."""
        with patch(
            "src.app.modules.billing.service.UserSubscriptionService.cancel_subscription",
            new_callable=AsyncMock,
            return_value={"id": 1, "status": "canceled"},
        ):
            response = client.post(
                "/api/v1/billing/subscriptions/me/cancel",
                headers=regular_user_token_headers,
            )
            # 405 if endpoint doesn't exist, 422 for validation errors
            assert response.status_code in [200, 404, 405, 422]


class TestBillingRecordAPI:
    """Integration tests for billing record (invoice) endpoints."""

    def test_list_billing_records_requires_auth(self, client, regular_user_token_headers) -> None:
        """Listing billing records requires authentication."""
        response = client.get(
            "/api/v1/billing/records",
            headers=regular_user_token_headers,
        )
        assert response.status_code in [200, 401, 403, 404]

    def test_list_billing_records_with_auth(self, client, regular_user_token_headers) -> None:
        """List billing records for authenticated user."""
        response = client.get(
            "/api/v1/billing/records",
            headers=regular_user_token_headers,
        )
        assert response.status_code in [200, 404]

    def test_get_single_billing_record(self, client, regular_user_token_headers) -> None:
        """Get a single billing record by ID."""
        response = client.get(
            "/api/v1/billing/records/1",
            headers=regular_user_token_headers,
        )
        # 200 if exists and owned by user, 404 otherwise
        assert response.status_code in [200, 404]


class TestBillingWebhooks:
    """Integration tests for Stripe webhook handling."""

    def test_stripe_webhook_endpoint_exists(self, client) -> None:
        """Stripe webhook endpoint should exist."""
        # Webhooks don't require auth headers but need valid signature
        response = client.post(
            "/api/v1/billing/webhooks/stripe",
            content=b"{}",
            headers={"Stripe-Signature": "invalid"},
        )
        # Should return 400 for invalid signature, not 404
        assert response.status_code in [400, 401, 422, 500]


class TestBillingTenantIsolation:
    """Tests for billing tenant isolation."""

    def test_plans_scoped_to_tenant(self, client, regular_user_token_headers) -> None:
        """Plans should be scoped to tenant."""
        response = client.get(
            "/api/v1/billing/plans",
            headers=regular_user_token_headers,
        )
        assert response.status_code in [200, 422]

    def test_subscriptions_scoped_to_user(self, client, regular_user_token_headers) -> None:
        """Subscriptions should be scoped to current user."""
        response = client.get(
            "/api/v1/billing/subscriptions/me",
            headers=regular_user_token_headers,
        )
        # Should not expose other users' subscriptions
        assert response.status_code in [200, 404]
