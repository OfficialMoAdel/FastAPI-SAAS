"""Unit tests for user API endpoints."""

from unittest.mock import AsyncMock, Mock

import pytest

from src.app.core.exceptions.http_exceptions import (
    DuplicateValueException,
    ForbiddenException,
    NotFoundException,
)
from src.app.main import app
from src.app.modules._base.exceptions import ModelNotFoundError
from src.app.modules.users import router as users_router
from src.app.modules.users.service import UserService

# Test tenant header for middleware
TEST_TENANT_HEADER = {"X-Tenant-ID": "1"}


def _create_mock_user_service(**method_returns):
    """Helper to create a mock UserService with specified method return values.

    Note: The router tries get_by_id first, then falls back to other methods.
    We make get_by_id raise ValueError by default to trigger the fallback path.
    """
    mock_service = Mock(spec=UserService)

    # Default: make get_by_id raise ValueError so router falls back to username-based methods
    mock_service.get_by_id = AsyncMock(side_effect=ValueError("Not an integer ID"))

    for method_name, return_value in method_returns.items():
        if isinstance(return_value, Exception):
            setattr(mock_service, method_name, AsyncMock(side_effect=return_value))
        else:
            setattr(mock_service, method_name, AsyncMock(return_value=return_value))
    return mock_service


@pytest.fixture
def mock_user_service_override():
    """Fixture to manage dependency override lifecycle."""
    original_overrides = app.dependency_overrides.copy()
    yield
    app.dependency_overrides = original_overrides


class TestWriteUser:
    """Test user creation endpoint."""

    def test_create_user_success(
        self, client, auth_headers, sample_user_data, sample_user_read, mock_user_service_override
    ):  # noqa: E501
        """Test successful user creation."""
        # Use sample_user_data to build expected result so values match
        expected_result = {
            **sample_user_read.model_dump(),
            "email": sample_user_data["email"],
            "username": sample_user_data["username"],
        }
        mock_service = _create_mock_user_service(create=expected_result)
        app.dependency_overrides[users_router._get_user_service] = lambda: mock_service

        headers = {**auth_headers, **TEST_TENANT_HEADER}
        response = client.post("/api/v1/users", json=sample_user_data, headers=headers)

        assert response.status_code == 201
        assert response.json()["email"] == sample_user_data["email"]

    def test_create_user_duplicate_email(self, client, auth_headers, sample_user_data, mock_user_service_override):
        """Test user creation with duplicate email."""
        mock_service = _create_mock_user_service(create=DuplicateValueException("Email is already registered"))
        app.dependency_overrides[users_router._get_user_service] = lambda: mock_service

        headers = {**auth_headers, **TEST_TENANT_HEADER}
        response = client.post("/api/v1/users", json=sample_user_data, headers=headers)

        # DuplicateValueException from fastcrud returns 422
        assert response.status_code == 422
        assert "already registered" in response.json()["detail"].lower()

    def test_create_user_duplicate_username(self, client, auth_headers, sample_user_data, mock_user_service_override):
        """Test user creation with duplicate username."""
        mock_service = _create_mock_user_service(create=DuplicateValueException("Username not available"))
        app.dependency_overrides[users_router._get_user_service] = lambda: mock_service

        headers = {**auth_headers, **TEST_TENANT_HEADER}
        response = client.post("/api/v1/users", json=sample_user_data, headers=headers)

        # DuplicateValueException from fastcrud returns 422
        assert response.status_code == 422
        assert "username" in response.json()["detail"].lower()


class TestReadUser:
    """Test user retrieval endpoint."""

    def test_read_user_success(self, client, auth_headers, sample_user_read, mock_user_service_override):
        """Test successful user retrieval."""
        expected_result = sample_user_read.model_dump()
        mock_service = _create_mock_user_service(get_profile=expected_result)
        app.dependency_overrides[users_router._get_user_service] = lambda: mock_service

        response = client.get("/api/v1/users/test_user", headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["username"] == expected_result["username"]

    def test_read_user_not_found(self, client, auth_headers, mock_user_service_override):
        """Test user retrieval when user doesn't exist."""
        mock_service = _create_mock_user_service(get_profile=ModelNotFoundError("User", "nonexistent_user"))
        app.dependency_overrides[users_router._get_user_service] = lambda: mock_service

        response = client.get("/api/v1/users/nonexistent_user", headers=auth_headers)

        assert response.status_code == 404


class TestReadUsers:
    """Test users list endpoint."""

    @pytest.mark.skip(reason="Requires superuser_headers fixture - needs integration test setup")
    def test_read_users_success(self, client, superuser_headers):
        """Test successful users list retrieval."""
        # This endpoint uses crud_users directly, not the service
        # Merge superuser_headers with tenant header
        headers = {**superuser_headers, **TEST_TENANT_HEADER}
        response = client.get("/api/v1/userss", headers=headers)

        # Should return 200 with pagination structure
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data


class TestPatchUser:
    """Test user update endpoint."""

    def test_patch_user_success(self, client, auth_headers, sample_user_read, mock_user_service_override):
        """Test successful user update."""
        mock_service = _create_mock_user_service(update={"message": "User updated"})
        app.dependency_overrides[users_router._get_user_service] = lambda: mock_service

        response = client.patch(
            "/api/v1/users/test_user",
            json={"name": "Updated Name"},
            headers=auth_headers,
        )

        assert response.status_code == 200

    def test_patch_user_forbidden(self, client, auth_headers, mock_user_service_override):
        """Test user update by non-owner/non-superuser."""
        mock_service = _create_mock_user_service(update=ForbiddenException("You can only update your own account"))
        app.dependency_overrides[users_router._get_user_service] = lambda: mock_service

        response = client.patch(
            "/api/v1/users/other_user",
            json={"name": "Hacked Name"},
            headers=auth_headers,
        )

        assert response.status_code == 403


class TestEraseUser:
    """Test user deletion endpoint."""

    def test_erase_user_success(self, client, auth_headers, mock_user_service_override):
        """Test successful user deletion."""
        mock_service = _create_mock_user_service(delete={"message": "User deleted successfully"})
        app.dependency_overrides[users_router._get_user_service] = lambda: mock_service

        response = client.delete("/api/v1/users/test_user", headers=auth_headers)

        assert response.status_code == 200

    def test_erase_user_not_found(self, client, auth_headers, mock_user_service_override):
        """Test deleting non-existent user."""
        mock_service = _create_mock_user_service(delete=NotFoundException("User not found"))
        app.dependency_overrides[users_router._get_user_service] = lambda: mock_service

        response = client.delete("/api/v1/users/nonexistent_user", headers=auth_headers)

        assert response.status_code == 404

    def test_erase_user_forbidden(self, client, auth_headers, mock_user_service_override):
        """Test user deletion by non-owner."""
        mock_service = _create_mock_user_service(delete=ForbiddenException("You can only delete your own account"))
        app.dependency_overrides[users_router._get_user_service] = lambda: mock_service

        response = client.delete("/api/v1/users/other_user", headers=auth_headers)

        assert response.status_code == 403
