"""Unit tests for the /account/register endpoint."""

from unittest.mock import AsyncMock
from uuid6 import uuid7

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import MockUser


@pytest.fixture
def app():
    """Create a FastAPI app instance with the account router."""
    # Lazy import to avoid circular import at collection time
    from src.app.modules.auth.router import router as account_router

    app = FastAPI()
    app.include_router(account_router, prefix="/api/v1")
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def account_router():
    """Lazily import the account router."""
    from src.app.modules.auth.router import router as account_router

    return account_router


@pytest.fixture
def get_user_service_dep():
    """Lazily import _get_user_service dependency."""
    from src.app.modules.auth.router import _get_user_service

    return _get_user_service


@pytest.fixture
def mock_user_service():
    """Mock UserService."""
    service = AsyncMock()
    service.create = AsyncMock(
        return_value={
            "id": 1,
            "username": "testuser",
            "email": "test@example.com",
            "is_active": True,
            "is_superuser": False,
            "tier_id": None,
            "tier_name": None,
        }
    )
    return service


def test_register_endpoint_exists(client, app, get_user_service_dep):
    """Test that /register endpoint exists on /account router."""
    # Create a user registration payload
    user_data = {
        "name": "Test User",
        "username": "testuser",
        "email": "test@example.com",
        "password": "StrongPassword123!",
    }

    # Mock the UserService dependency using dependency_overrides
    mock_service = AsyncMock()
    mock_service.create = AsyncMock(
        return_value={
            "id": 1,
            "name": "Test User",
            "username": "testuser",
            "email": "test@example.com",
            "is_active": True,
            "is_superuser": False,
            "tier_id": None,
            "tier_name": None,
            "tenant_id": MockUser.DEFAULT_TENANT_ID,  # Use UUID
            "created_at": "2024-01-01T00:00:00Z",
            "profile_image_url": "https://profileimageurl.com",
        }
    )

    app.dependency_overrides[get_user_service_dep] = lambda: mock_service

    try:
        # Make the request
        response = client.post("/api/v1/account/register", json=user_data)

        # Assert the response
        assert response.status_code == 201
        assert response.json()["username"] == "testuser"
        assert response.json()["email"] == "test@example.com"
    finally:
        app.dependency_overrides.clear()


def test_account_prefix_changed(account_router):
    """Test that the router uses /account prefix instead of /auth."""
    assert account_router.prefix == "/account"
    assert "account" in account_router.tags


def test_all_endpoints_on_account_prefix(client, account_router):
    """Test that all auth endpoints are now under /account."""
    # Get all routes from the router
    routes = [route.path for route in account_router.routes]

    # Expected routes (paths include prefix)
    expected_routes = [
        "/account/login",
        "/account/register",
        "/account/refresh",
        "/account/logout",
        "/account/password",
        "/account/forgot-password",
        "/account/reset-password",
        "/account/send-verification",
        "/account/verify-email",
    ]

    for expected_route in expected_routes:
        assert expected_route in routes, f"Route {expected_route} not found in {routes}"