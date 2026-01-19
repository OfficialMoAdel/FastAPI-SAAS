"""Unit tests for get_optional_user dependency.

Tests the critical bug fix for get_optional_user function signature.
Ensures authenticated users are properly recognized for rate limiting.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import get_optional_user


@pytest.fixture
def mock_request():
    """Mock FastAPI Request object."""
    request = MagicMock(spec=Request)
    request.headers = {}
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
        "tenant_id": 1,
        "tier_id": 2,  # Premium tier
    }


class TestGetOptionalUserWithValidToken:
    """Tests for get_optional_user with valid authentication token."""

    @pytest.mark.asyncio
    async def test_returns_user_dict_with_valid_token(
        self, mock_request, mock_db, mock_user_dict
    ):
        """Test that get_optional_user returns user dict when token is valid."""
        # Arrange
        mock_request.headers = {"Authorization": "Bearer valid_token_123"}

        with patch("src.app.api.dependencies.verify_token") as mock_verify:
            with patch("src.app.api.dependencies.get_current_user") as mock_get_user:
                mock_verify.return_value = MagicMock(username_or_email="test@example.com")
                mock_get_user.return_value = mock_user_dict

                # Act
                result = await get_optional_user(mock_request, mock_db)

                # Assert
                assert result == mock_user_dict
                mock_verify.assert_called_once()
                mock_get_user.assert_called_once_with(mock_request, "valid_token_123", db=mock_db)

    @pytest.mark.asyncio
    async def test_passes_request_parameter_correctly(self, mock_request, mock_db, mock_user_dict):
        """Test that request parameter is passed to get_current_user (bug fix verification)."""
        # Arrange
        mock_request.headers = {"Authorization": "Bearer token_abc"}

        with patch("src.app.api.dependencies.verify_token") as mock_verify:
            with patch("src.app.api.dependencies.get_current_user") as mock_get_user:
                mock_verify.return_value = MagicMock(username_or_email="user@test.com")
                mock_get_user.return_value = mock_user_dict

                # Act
                await get_optional_user(mock_request, mock_db)

                # Assert - verify request is first parameter
                call_args = mock_get_user.call_args
                assert call_args[0][0] == mock_request  # First positional arg is request
                assert call_args[0][1] == "token_abc"  # Second positional arg is token
                assert call_args[1]["db"] == mock_db  # db is keyword arg


class TestGetOptionalUserWithoutToken:
    """Tests for get_optional_user when no token is provided."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_authorization_header(self, mock_request, mock_db):
        """Test that get_optional_user returns None when Authorization header is missing."""
        # Arrange - no Authorization header
        mock_request.headers = {}

        # Act
        result = await get_optional_user(mock_request, mock_db)

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_authorization_header_empty(self, mock_request, mock_db):
        """Test that get_optional_user returns None when Authorization header is empty."""
        # Arrange
        mock_request.headers = {"Authorization": ""}

        # Act
        result = await get_optional_user(mock_request, mock_db)

        # Assert
        assert result is None


class TestGetOptionalUserWithInvalidToken:
    """Tests for get_optional_user with invalid or malformed tokens."""

    @pytest.mark.asyncio
    async def test_returns_none_when_token_type_not_bearer(self, mock_request, mock_db):
        """Test that get_optional_user returns None for non-Bearer token types."""
        # Arrange
        mock_request.headers = {"Authorization": "Basic dXNlcjpwYXNz"}

        # Act
        result = await get_optional_user(mock_request, mock_db)

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_token_value_missing(self, mock_request, mock_db):
        """Test that get_optional_user returns None when Bearer has no token value."""
        # Arrange
        mock_request.headers = {"Authorization": "Bearer "}

        # Act
        result = await get_optional_user(mock_request, mock_db)

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_verify_token_fails(self, mock_request, mock_db):
        """Test that get_optional_user returns None when token verification fails."""
        # Arrange
        mock_request.headers = {"Authorization": "Bearer invalid_token"}

        with patch("src.app.api.dependencies.verify_token") as mock_verify:
            mock_verify.return_value = None  # Token verification failed

            # Act
            result = await get_optional_user(mock_request, mock_db)

            # Assert
            assert result is None
            mock_verify.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_none_on_http_exception(self, mock_request, mock_db):
        """Test that get_optional_user returns None when HTTPException is raised."""
        from fastapi import HTTPException

        # Arrange
        mock_request.headers = {"Authorization": "Bearer expired_token"}

        with patch("src.app.api.dependencies.verify_token") as mock_verify:
            with patch("src.app.api.dependencies.get_current_user") as mock_get_user:
                mock_verify.return_value = MagicMock(username_or_email="test@example.com")
                mock_get_user.side_effect = HTTPException(status_code=401, detail="Token expired")

                # Act
                result = await get_optional_user(mock_request, mock_db)

                # Assert
                assert result is None  # Should not raise, should return None

    @pytest.mark.asyncio
    async def test_returns_none_on_generic_exception(self, mock_request, mock_db):
        """Test that get_optional_user returns None on unexpected exceptions."""
        # Arrange
        mock_request.headers = {"Authorization": "Bearer token_123"}

        with patch("src.app.api.dependencies.verify_token") as mock_verify:
            mock_verify.side_effect = Exception("Database connection error")

            # Act
            result = await get_optional_user(mock_request, mock_db)

            # Assert
            assert result is None  # Should not raise, should return None


class TestGetOptionalUserRateLimitIntegration:
    """Tests for rate limit integration with get_optional_user."""

    @pytest.mark.asyncio
    async def test_premium_user_identified_for_rate_limiting(
        self, mock_request, mock_db, mock_user_dict
    ):
        """Test that premium users are correctly identified for rate limit tier."""
        # Arrange
        mock_request.headers = {"Authorization": "Bearer premium_user_token"}
        mock_user_dict["tier_id"] = 2  # Premium tier

        with patch("src.app.api.dependencies.verify_token") as mock_verify:
            with patch("src.app.api.dependencies.get_current_user") as mock_get_user:
                mock_verify.return_value = MagicMock(username_or_email="premium@example.com")
                mock_get_user.return_value = mock_user_dict

                # Act
                result = await get_optional_user(mock_request, mock_db)

                # Assert
                assert result is not None
                assert result["tier_id"] == 2  # Premium tier preserved
                assert result["id"] == 123  # User ID available for rate limiting

    @pytest.mark.asyncio
    async def test_anonymous_user_returns_none_for_rate_limiting(self, mock_request, mock_db):
        """Test that anonymous users (no token) return None for anonymous rate limits."""
        # Arrange - no token
        mock_request.headers = {}

        # Act
        result = await get_optional_user(mock_request, mock_db)

        # Assert
        assert result is None  # Should trigger anonymous rate limits
