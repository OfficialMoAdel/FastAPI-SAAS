"""Tests for the checkouts router endpoints (src/app/modules/checkouts/router.py).

Patches the UserSubscriptionService and CheckoutService class methods directly
to bypass any real database queries.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID

from fastapi.testclient import TestClient

MOCK_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
MOCK_NOW = datetime.now(UTC)
MOCK_CHECKOUT_SESSION = {
    "checkout_url": "https://stripe.com/checkout/session_abc",
    "session_id": "cs_test_abc123",
    "expires_at": MOCK_NOW + timedelta(hours=1),
}
MOCK_SUBSCRIPTION = {
    "id": 2,
    "tenant_id": MOCK_TENANT_ID,
    "user_id": 1,
    "subscription_plan_id": 1,
    "stripe_subscription_id": "sub_123",
    "status": "active",
    "billing_interval": "monthly",
    "current_period_start": MOCK_NOW - timedelta(days=5),
    "current_period_end": MOCK_NOW + timedelta(days=25),
    "cancel_at_period_end": False,
    "canceled_at": None,
    "created_at": MOCK_NOW,
    "updated_at": MOCK_NOW,
}
MOCK_SUBSCRIPTION_CANCELED = {**MOCK_SUBSCRIPTION, "status": "canceled"}

VALID_CHECKOUT_PAYLOAD = {
    "plan_id": 1,
    "billing_interval": "monthly",
    "success_url": "https://example.com/success",
    "cancel_url": "https://example.com/cancel",
}


def test_create_checkout_success(client: TestClient):
    """POST /checkouts - creates checkout session."""
    with (
        patch(
            "src.app.modules.billing.service.CheckoutService.create_session",
            new=AsyncMock(return_value=MOCK_CHECKOUT_SESSION),
        ),
    ):
        client.headers.update({"X-Tenant-ID": "test-tenant"})
        response = client.post("/api/v1/checkouts", json=VALID_CHECKOUT_PAYLOAD)

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["checkout_url"] == MOCK_CHECKOUT_SESSION["checkout_url"]


def test_create_checkout_missing_fields(client: TestClient):
    """POST /checkouts - returns 422 when required fields are missing."""
    client.headers.update({"X-Tenant-ID": "test-tenant"})
    response = client.post("/api/v1/checkouts", json={})
    assert response.status_code == 422


def test_get_current_subscription_active(client: TestClient):
    """GET /checkouts/current - returns first active subscription."""
    with patch(
        "src.app.modules.billing.service.UserSubscriptionService.get_user_subscriptions",
        new=AsyncMock(return_value={
            "data": [
                {**MOCK_SUBSCRIPTION, "id": 1, "status": "canceled"},
                {**MOCK_SUBSCRIPTION, "id": 2, "status": "active"},
            ]
        }),
    ):
        client.headers.update({"X-Tenant-ID": "test-tenant"})
        response = client.get("/api/v1/checkouts/current")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["id"] == 2
    assert data["status"] == "active"


def test_get_current_subscription_no_active(client: TestClient):
    """GET /checkouts/current - returns null when no active subscriptions."""
    with patch(
        "src.app.modules.billing.service.UserSubscriptionService.get_user_subscriptions",
        new=AsyncMock(return_value={
            "data": [{**MOCK_SUBSCRIPTION, "status": "canceled"}]
        }),
    ):
        client.headers.update({"X-Tenant-ID": "test-tenant"})
        response = client.get("/api/v1/checkouts/current")

    assert response.status_code == 200, response.text
    assert response.json() is None


def test_get_current_subscription_empty(client: TestClient):
    """GET /checkouts/current - returns null when no subscriptions at all."""
    with patch(
        "src.app.modules.billing.service.UserSubscriptionService.get_user_subscriptions",
        new=AsyncMock(return_value={"data": []}),
    ):
        client.headers.update({"X-Tenant-ID": "test-tenant"})
        response = client.get("/api/v1/checkouts/current")

    assert response.status_code == 200, response.text
    assert response.json() is None


def test_cancel_subscription_with_explicit_id(client: TestClient):
    """POST /checkouts/cancel - cancels subscription by explicit ID in body."""
    with (
        patch(
            "src.app.modules.billing.service.UserSubscriptionService.get_subscription",
            new=AsyncMock(return_value={**MOCK_SUBSCRIPTION, "user_id": 1}),
        ),
        patch(
            "src.app.modules.billing.service.UserSubscriptionService.cancel_subscription",
            new=AsyncMock(return_value=MOCK_SUBSCRIPTION_CANCELED),
        ),
    ):
        client.headers.update({"X-Tenant-ID": "test-tenant"})
        response = client.post("/api/v1/checkouts/cancel", json={"subscription_id": 2})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "canceled"


def test_cancel_subscription_implicit_lookup(client: TestClient):
    """POST /checkouts/cancel - finds active subscription when no ID provided."""
    with (
        patch(
            "src.app.modules.billing.service.UserSubscriptionService.get_user_subscriptions",
            new=AsyncMock(return_value={"data": [{**MOCK_SUBSCRIPTION, "user_id": 1}]}),
        ),
        patch(
            "src.app.modules.billing.service.UserSubscriptionService.get_subscription",
            new=AsyncMock(return_value={**MOCK_SUBSCRIPTION, "user_id": 1}),
        ),
        patch(
            "src.app.modules.billing.service.UserSubscriptionService.cancel_subscription",
            new=AsyncMock(return_value=MOCK_SUBSCRIPTION_CANCELED),
        ),
    ):
        client.headers.update({"X-Tenant-ID": "test-tenant"})
        response = client.post("/api/v1/checkouts/cancel", json={})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "canceled"


def test_cancel_subscription_no_active_returns_404(client: TestClient):
    """POST /checkouts/cancel - returns 404 when no active subscription found."""
    with patch(
        "src.app.modules.billing.service.UserSubscriptionService.get_user_subscriptions",
        new=AsyncMock(return_value={"data": []}),
    ):
        client.headers.update({"X-Tenant-ID": "test-tenant"})
        response = client.post("/api/v1/checkouts/cancel", json={})

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "No active subscription found"


def test_cancel_subscription_forbidden(client: TestClient):
    """POST /checkouts/cancel - returns 403 when subscription belongs to another user."""
    with patch(
        "src.app.modules.billing.service.UserSubscriptionService.get_subscription",
        new=AsyncMock(return_value={**MOCK_SUBSCRIPTION, "user_id": 999}),
    ):
        client.headers.update({"X-Tenant-ID": "test-tenant"})
        response = client.post("/api/v1/checkouts/cancel", json={"subscription_id": 2})

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Not authorized to cancel this subscription"
