"""Unit tests for AI rate limiting middleware dependency.

Tests T099: Rate limiting middleware (60 req/user/min)
Tests T100: HTTP 429 RateLimitError response
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


class TestRateLimitDependency:
    """Test suite for rate limiting FastAPI dependency."""

    @pytest.fixture
    def mock_response(self):
        """Create a mock FastAPI response object."""
        response = MagicMock()
        response.headers = {}
        return response

    @pytest.fixture
    def mock_current_user(self):
        """Create a mock current user."""
        return {"id": 1, "email": "test@example.com"}

    @pytest.mark.asyncio
    async def test_rate_limit_allows_request_within_limit(self, mock_response, mock_current_user):
        """Test that requests within limit are allowed."""
        from src.app.modules.ai.middleware import check_rate_limit_dependency

        with patch(
            "src.app.modules.ai.middleware.check_ai_rate_limit",
            new_callable=AsyncMock,
        ) as mock_check:
            mock_check.return_value = (True, 59, 60)  # is_allowed, remaining, retry_after

            result = await check_rate_limit_dependency(mock_response, mock_current_user)

            assert result == mock_current_user
            assert mock_response.headers["X-RateLimit-Limit"] == "60"
            assert mock_response.headers["X-RateLimit-Remaining"] == "59"
            assert mock_response.headers["X-RateLimit-Reset"] == "60"

    @pytest.mark.asyncio
    async def test_rate_limit_adds_headers_on_success(self, mock_response, mock_current_user):
        """Test that rate limit headers are added to response."""
        from src.app.modules.ai.middleware import check_rate_limit_dependency

        with patch(
            "src.app.modules.ai.middleware.check_ai_rate_limit",
            new_callable=AsyncMock,
        ) as mock_check:
            mock_check.return_value = (True, 30, 45)

            await check_rate_limit_dependency(mock_response, mock_current_user)

            assert "X-RateLimit-Limit" in mock_response.headers
            assert "X-RateLimit-Remaining" in mock_response.headers
            assert "X-RateLimit-Reset" in mock_response.headers
            assert mock_response.headers["X-RateLimit-Remaining"] == "30"
            assert mock_response.headers["X-RateLimit-Reset"] == "45"

    @pytest.mark.asyncio
    async def test_rate_limit_raises_429_when_exceeded(self, mock_response, mock_current_user):
        """Test that HTTP 429 is raised when rate limit is exceeded."""
        from src.app.modules.ai.middleware import check_rate_limit_dependency
        from src.app.modules.ai.rate_limiter import RateLimitError

        with patch(
            "src.app.modules.ai.middleware.check_ai_rate_limit",
            new_callable=AsyncMock,
        ) as mock_check:
            mock_check.side_effect = RateLimitError(retry_after=30)

            with pytest.raises(HTTPException) as exc_info:
                await check_rate_limit_dependency(mock_response, mock_current_user)

            assert exc_info.value.status_code == 429
            assert exc_info.value.detail["error"] == "rate_limit_exceeded"
            assert exc_info.value.detail["retry_after"] == 30

    @pytest.mark.asyncio
    async def test_rate_limit_429_includes_retry_after_header(self, mock_response, mock_current_user):
        """Test that 429 response includes Retry-After header."""
        from src.app.modules.ai.middleware import check_rate_limit_dependency
        from src.app.modules.ai.rate_limiter import RateLimitError

        with patch(
            "src.app.modules.ai.middleware.check_ai_rate_limit",
            new_callable=AsyncMock,
        ) as mock_check:
            mock_check.side_effect = RateLimitError(retry_after=45)

            with pytest.raises(HTTPException) as exc_info:
                await check_rate_limit_dependency(mock_response, mock_current_user)

            assert "Retry-After" in exc_info.value.headers
            assert exc_info.value.headers["Retry-After"] == "45"

    @pytest.mark.asyncio
    async def test_rate_limit_429_includes_rate_limit_headers(self, mock_response, mock_current_user):
        """Test that 429 response includes all rate limit headers."""
        from src.app.modules.ai.middleware import check_rate_limit_dependency
        from src.app.modules.ai.rate_limiter import RateLimitError

        with patch(
            "src.app.modules.ai.middleware.check_ai_rate_limit",
            new_callable=AsyncMock,
        ) as mock_check:
            mock_check.side_effect = RateLimitError(retry_after=60)

            with pytest.raises(HTTPException) as exc_info:
                await check_rate_limit_dependency(mock_response, mock_current_user)

            headers = exc_info.value.headers
            assert headers["X-RateLimit-Limit"] == "60"
            assert headers["X-RateLimit-Remaining"] == "0"
            assert headers["X-RateLimit-Reset"] == "60"

    @pytest.mark.asyncio
    async def test_rate_limit_uses_correct_user_id(self, mock_response, mock_current_user):
        """Test that rate limit check uses the correct user ID."""
        from src.app.modules.ai.middleware import check_rate_limit_dependency

        with patch(
            "src.app.modules.ai.middleware.check_ai_rate_limit",
            new_callable=AsyncMock,
        ) as mock_check:
            mock_check.return_value = (True, 59, 60)

            await check_rate_limit_dependency(mock_response, mock_current_user)

            mock_check.assert_called_once_with(mock_current_user["id"])

    @pytest.mark.asyncio
    async def test_rate_limit_with_zero_remaining(self, mock_response, mock_current_user):
        """Test response when at exact rate limit (0 remaining)."""
        from src.app.modules.ai.middleware import check_rate_limit_dependency

        with patch(
            "src.app.modules.ai.middleware.check_ai_rate_limit",
            new_callable=AsyncMock,
        ) as mock_check:
            mock_check.return_value = (True, 0, 30)  # Last allowed request

            result = await check_rate_limit_dependency(mock_response, mock_current_user)

            assert result == mock_current_user
            assert mock_response.headers["X-RateLimit-Remaining"] == "0"
