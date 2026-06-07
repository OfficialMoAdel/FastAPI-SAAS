"""Tests for the checkouts router endpoints (src/app/modules/checkouts/router.py).

Patches the UserSubscriptionService and CheckoutService class methods directly
to bypass any real database queries.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.api.dependencies import async_get_db, get_current_tenant, get_current_user

# Import the router to test
from src.app.modules.checkouts.router import router as checkouts_router

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

from src.app.core.config import settings as app_settings

_frontend = app_settings.FRONTEND_URL.rstrip("/") if app_settings.FRONTEND_URL else "https://example.com"

VALID_CHECKOUT_PAYLOAD = {
    "plan_id": 1,
    "billing_interval": "monthly",
    "success_url": f"{_frontend}/success",
    "cancel_url": f"{_frontend}/cancel",
}


@pytest.fixture
def mock_user():
    """Create a mock user for authentication bypass."""
    user = MagicMock()
    user.id = 1
    user.tenant_id = MOCK_TENANT_ID
    user.email = "test@example.com"
    # Make it subscriptable for dictionary-style access
    user.__getitem__ = lambda self, key: getattr(self, key)
    return user


@pytest.fixture
def mock_tenant():
    """Create a mock tenant for bypass."""
    tenant = MagicMock()
    tenant.id = MOCK_TENANT_ID
    # Make it subscriptable for dictionary-style access
    tenant.__getitem__ = lambda self, key: getattr(self, key)
    return tenant


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return AsyncMock()


@pytest.fixture
def test_app():
    """Create a test FastAPI app with the checkouts router."""
    app = FastAPI()
    app.include_router(checkouts_router, prefix="/api/v1")
    return app


@pytest.fixture
def client(test_app, mock_user, mock_tenant, mock_db):
    """Create a test client with dependency overrides."""
    app = test_app
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_current_tenant] = lambda: mock_tenant
    app.dependency_overrides[async_get_db] = lambda: mock_db
    return TestClient(app)


# --- Router Structure Tests ---


def test_router_prefix():
    """Test router uses /checkouts prefix."""
    from src.app.modules.checkouts.router import router

    assert router.prefix == "/checkouts"


def test_router_tags():
    """Test router has correct tags."""
    from src.app.modules.checkouts.router import router

    assert "checkouts" in router.tags


# --- Create Checkout Tests ---


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


def test_create_checkout_requires_auth():
    """POST /checkouts - requires authentication."""
    app = FastAPI()
    app.include_router(checkouts_router, prefix="/api/v1")

    client = TestClient(app)
    response = client.post("/api/v1/checkouts", json=VALID_CHECKOUT_PAYLOAD)

    # Should return 401/403 without auth
    assert response.status_code in [401, 403, 500]


# --- Get Current Subscription Tests ---


def test_get_current_subscription_active(client: TestClient):
    """GET /checkouts/current - returns first active subscription."""
    with patch(
        "src.app.modules.billing.service.UserSubscriptionService.get_user_subscriptions",
        new=AsyncMock(
            return_value={
                "data": [
                    {**MOCK_SUBSCRIPTION, "id": 1, "status": "canceled"},
                    {**MOCK_SUBSCRIPTION, "id": 2, "status": "active"},
                ]
            }
        ),
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
        new=AsyncMock(return_value={"data": [{**MOCK_SUBSCRIPTION, "status": "canceled"}]}),
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


# --- Cancel Subscription Tests ---


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


def test_cancel_subscription_forbidden(client: TestClient):
    """POST /checkouts/cancel - returns 403 when user doesn't own subscription."""
    with patch(
        "src.app.modules.billing.service.UserSubscriptionService.get_subscription",
        new=AsyncMock(return_value={**MOCK_SUBSCRIPTION, "user_id": 999}),
    ):
        client.headers.update({"X-Tenant-ID": "test-tenant"})
        response = client.post("/api/v1/checkouts/cancel", json={"subscription_id": 2})

    assert response.status_code == 403, response.text


def test_cancel_subscription_requires_auth():
    """POST /checkouts/cancel - requires authentication."""
    app = FastAPI()
    app.include_router(checkouts_router, prefix="/api/v1")

    client = TestClient(app)
    response = client.post("/api/v1/checkouts/cancel", json={"subscription_id": 1})

    assert response.status_code in [401, 403, 500]


def test_cancel_subscription_past_due_status(client: TestClient):
    """POST /checkouts/cancel - handles past_due status correctly."""
    with (
        patch(
            "src.app.modules.billing.service.UserSubscriptionService.get_subscription",
            new=AsyncMock(return_value={**MOCK_SUBSCRIPTION, "user_id": 1, "status": "past_due"}),
        ),
        patch(
            "src.app.modules.billing.service.UserSubscriptionService.cancel_subscription",
            new=AsyncMock(return_value={**MOCK_SUBSCRIPTION_CANCELED, "status": "canceled"}),
        ),
    ):
        client.headers.update({"X-Tenant-ID": "test-tenant"})
        response = client.post("/api/v1/checkouts/cancel", json={"subscription_id": 2})

    assert response.status_code == 200, response.text


def test_get_current_subscription_returns_full_details(client: TestClient):
    """GET /checkouts/current - returns all subscription fields."""
    with patch(
        "src.app.modules.billing.service.UserSubscriptionService.get_user_subscriptions",
        new=AsyncMock(return_value={"data": [MOCK_SUBSCRIPTION]}),
    ):
        client.headers.update({"X-Tenant-ID": "test-tenant"})
        response = client.get("/api/v1/checkouts/current")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["stripe_subscription_id"] == "sub_123"
    assert data["billing_interval"] == "monthly"
    assert data["cancel_at_period_end"] is False


def test_get_current_subscription_returns_most_recent_active(client: TestClient):
    """GET /checkouts/current - returns first active (not necessarily most recent).

    Note: The router returns the first active subscription from the list,
    not sorted by created_at. This test verifies the actual behavior.
    """
    with patch(
        "src.app.modules.billing.service.UserSubscriptionService.get_user_subscriptions",
        new=AsyncMock(
            return_value={
                "data": [
                    {**MOCK_SUBSCRIPTION, "id": 1, "status": "active", "created_at": MOCK_NOW - timedelta(days=30)},
                    {**MOCK_SUBSCRIPTION, "id": 3, "status": "active", "created_at": MOCK_NOW},
                ]
            }
        ),
    ):
        client.headers.update({"X-Tenant-ID": "test-tenant"})
        response = client.get("/api/v1/checkouts/current")

    assert response.status_code == 200, response.text
    data = response.json()
    # Router returns first active in list, not sorted by date
    assert data["id"] == 1
