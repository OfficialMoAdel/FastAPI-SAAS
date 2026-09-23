"""Unit tests for the /account/register endpoint."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.modules.users.service import UserService
from tests.conftest import MockUser


@pytest.fixture
def app():
    """Create a FastAPI app instance with the account router."""
    # Lazy import to avoid circular import at collection time
    from src.app.core.errors import setup_exception_handlers
    from src.app.modules.auth.router import router as account_router

    app = FastAPI()
    setup_exception_handlers(app)
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


class TestRegistrationGate:
    """S-M2: signup gate — with AUTH_OPEN_REGISTRATION=False (default),
    self-service registration only bootstraps a tenant's FIRST user."""

    user_data = {
        "name": "Test User",
        "username": "testuser",
        "email": "test@example.com",
        "password": "StrongPassword123!",
    }

    created_user = {
        "id": 1,
        "name": "Test User",
        "username": "testuser",
        "email": "test@example.com",
        "is_active": True,
        "is_superuser": False,
        "tier_id": None,
        "tier_name": None,
        "tenant_id": MockUser.DEFAULT_TENANT_ID,
        "created_at": "2024-01-01T00:00:00Z",
        "profile_image_url": "https://profileimageurl.com",
    }

    @staticmethod
    def _mock_tenant():
        from uuid import uuid4

        tenant = Mock()
        tenant.id = uuid4()
        tenant.slug = "gated-tenant"
        tenant.is_active = True
        return tenant

    @pytest.fixture
    def gate_closed(self):
        from src.app.core.config import settings

        original = settings.auth.AUTH_OPEN_REGISTRATION
        settings.auth.AUTH_OPEN_REGISTRATION = False
        yield
        settings.auth.AUTH_OPEN_REGISTRATION = original

    @pytest.fixture
    def gate_open(self):
        from src.app.core.config import settings

        original = settings.auth.AUTH_OPEN_REGISTRATION
        settings.auth.AUTH_OPEN_REGISTRATION = True
        yield
        settings.auth.AUTH_OPEN_REGISTRATION = original

    def _post_register(self, client, app, mock_service, monkeypatch, tenant_user_rows):
        """Shared driver: patch the service + DB lookup, set a tenant context."""
        from src.app.core.db.database import async_get_db

        mock_db = AsyncMock()
        mock_gate_count = Mock()
        mock_gate_count.scalar.return_value = tenant_user_rows
        mock_db.execute = AsyncMock(return_value=mock_gate_count)
        app.dependency_overrides[async_get_db] = lambda: mock_db
        monkeypatch.setattr(
            "src.app.modules.auth.services.registration_gate.get_current_tenant", lambda: self._mock_tenant()
        )

        try:
            with (
                patch.object(UserService, "create", new=AsyncMock(return_value=self.created_user)) as mock_create,
                patch("src.app.modules.auth.services.email_verification.make_email_verifier"),
            ):
                response = client.post("/api/v1/account/register", json=self.user_data)
                return response, mock_create
        finally:
            app.dependency_overrides.clear()

    def test_register_rejected_for_existing_tenant(self, client, app, gate_closed, monkeypatch):
        """Gate closed + tenant already has users -> 403, service NOT called."""
        mock_service = AsyncMock()

        response, mock_create = self._post_register(client, app, mock_service, monkeypatch, tenant_user_rows=1)

        assert response.status_code == 403
        assert "Registration is closed" in response.json()["detail"]
        mock_create.assert_not_awaited()

    def test_register_allowed_for_first_user_bootstrap(self, client, app, gate_closed, monkeypatch):
        """Gate closed but tenant has NO users -> bootstrap allowed (201)."""
        mock_service = AsyncMock()

        response, mock_create = self._post_register(client, app, mock_service, monkeypatch, tenant_user_rows=None)

        assert response.status_code == 201
        mock_create.assert_awaited_once()

    def test_register_allowed_when_gate_open(self, client, app, gate_open, monkeypatch):
        """AUTH_OPEN_REGISTRATION=True restores open signup (no gate check)."""
        mock_service = AsyncMock()

        # Even a populated tenant is accepted when the gate is open.
        response, mock_create = self._post_register(client, app, mock_service, monkeypatch, tenant_user_rows=1)

        assert response.status_code == 201
        mock_create.assert_awaited_once()
