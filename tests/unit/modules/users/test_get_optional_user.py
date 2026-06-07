"""Unit tests for get_optional_user dependency.

Tests the critical bug fix for get_optional_user function signature.
Ensures authenticated users are properly recognized for rate limiting.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import AuthenticatedUser, get_optional_user
from tests.conftest import MockUser


@pytest.fixture
def mock_request():
    """Mock FastAPI Request object."""
    request = MagicMock(spec=Request)
    request.headers = {}

    class MockState:
        pass

    request.state = MockState()
    return request


@pytest.fixture
def mock_db():
    """Mock database session."""
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def mock_user_dict():
    """Sample user dictionary returned by get_current_user."""
    return {
        "id": 123,
        "email": "test@example.com",
        "is_active": True,
        "is_superuser": False,
        "tenant_id": MockUser.DEFAULT_TENANT_ID,  # Use UUID
        "tier_id": 2,  # Premium tier
        "token_version": 1,
    }


class TestGetOptionalUserWithValidToken:
    """Tests for get_optional_user with valid authentication token."""

    @pytest.mark.asyncio
    async def test_returns_user_dict_with_valid_token(self, mock_request, mock_db, mock_user_dict):
        """Test that get_optional_user returns AuthenticatedUser when token is valid."""
        # Arrange
        mock_request.headers = {"Authorization": "Bearer valid_token_123"}

        # Mock tenant context
        mock_tenant = MagicMock()
        mock_tenant.id = MockUser.DEFAULT_TENANT_ID

        with patch("src.app.api.dependencies.verify_token") as mock_verify:
            with patch("src.app.api.dependencies.UserService") as MockUserService:
                with patch("src.app.api.dependencies.get_tenant_context") as mock_get_tenant:
                    mock_verify.return_value = MagicMock(username_or_email="test@example.com", token_version=1)
                    mock_get_tenant.return_value = mock_tenant

                    mock_user_service = AsyncMock()
                    mock_user_service.get_user_auth_info.return_value = mock_user_dict
                    MockUserService.return_value = mock_user_service

                    # Act
                    result = await get_optional_user(mock_request, mock_db)

                    # Assert
                    assert isinstance(result, AuthenticatedUser)
                    assert result.id == mock_user_dict["id"]
                    assert result.email == mock_user_dict["email"]
                    assert result["tier_id"] == mock_user_dict["tier_id"]
                    mock_verify.assert_called_once()

    @pytest.mark.asyncio
    async def test_passes_request_parameter_correctly(self, mock_request, mock_db, mock_user_dict):
        """Test that request parameter is passed correctly."""
        # Arrange
        mock_request.headers = {"Authorization": "Bearer token_abc"}

        mock_tenant = MagicMock()
        mock_tenant.id = MockUser.DEFAULT_TENANT_ID

        with patch("src.app.api.dependencies.verify_token") as mock_verify:
            with patch("src.app.api.dependencies.UserService") as MockUserService:
                with patch("src.app.api.dependencies.get_tenant_context") as mock_get_tenant:
                    mock_verify.return_value = MagicMock(username_or_email="user@test.com", token_version=1)
                    mock_get_tenant.return_value = mock_tenant

                    mock_user_service = AsyncMock()
                    mock_user_service.get_user_auth_info.return_value = mock_user_dict
                    MockUserService.return_value = mock_user_service

                    # Act
                    result = await get_optional_user(mock_request, mock_db)

                    # Assert
                    assert isinstance(result, AuthenticatedUser)
                    assert result.email == "test@example.com"


class TestGetOptionalUserWithNoToken:
    """Tests for get_optional_user with no authentication token."""

    @pytest.mark.asyncio
    async def test_returns_none_with_no_auth_header(self, mock_request, mock_db):
        """Test that get_optional_user returns None when no Authorization header."""
        # Arrange
        mock_request.headers = {}

        # Act
        result = await get_optional_user(mock_request, mock_db)

        # Assert
        assert result is None


class TestGetOptionalUserWithInvalidToken:
    """Tests for get_optional_user with invalid authentication token."""

    @pytest.mark.asyncio
    async def test_returns_none_with_invalid_token(self, mock_request, mock_db):
        """Test that get_optional_user returns None when token verification fails."""
        # Arrange
        mock_request.headers = {"Authorization": "Bearer invalid_token"}

        with patch("src.app.api.dependencies.verify_token") as mock_verify:
            mock_verify.return_value = None

            # Act
            result = await get_optional_user(mock_request, mock_db)

            # Assert
            assert result is None


class TestGetOptionalUserRateLimitIntegration:
    """Tests for rate limit integration with get_optional_user."""

    @pytest.mark.asyncio
    async def test_premium_user_identified_for_rate_limiting(self, mock_request, mock_db, mock_user_dict):
        """Test that premium users are correctly identified for rate limit tier."""
        # Arrange
        mock_request.headers = {"Authorization": "Bearer premium_user_token"}
        mock_user_dict["tier_id"] = 2  # Premium tier

        mock_tenant = MagicMock()
        mock_tenant.id = MockUser.DEFAULT_TENANT_ID

        with patch("src.app.api.dependencies.verify_token") as mock_verify:
            with patch("src.app.api.dependencies.UserService") as MockUserService:
                with patch("src.app.api.dependencies.get_tenant_context") as mock_get_tenant:
                    mock_verify.return_value = MagicMock(username_or_email="premium@example.com", token_version=1)
                    mock_get_tenant.return_value = mock_tenant

                    mock_user_service = AsyncMock()
                    mock_user_service.get_user_auth_info.return_value = mock_user_dict
                    MockUserService.return_value = mock_user_service

                    # Act
                    result = await get_optional_user(mock_request, mock_db)

                    # Assert
                    assert result is not None
                    assert result["tier_id"] == 2  # Premium tier preserved
                    assert result["id"] == 123  # User ID available for rate limiting
