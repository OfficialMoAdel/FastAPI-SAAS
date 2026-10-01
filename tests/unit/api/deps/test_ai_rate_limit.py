"""Unit tests for the AI rate-limiting Depends adapter.

The adapter delegates its decision to the security-budget seam
("ai_chat" budget) and keeps its sanctioned HTTPException 429 shape
(T099: 60 req/user/min default; T100: HTTP 429 with Retry-After and
X-RateLimit headers). These tests pin that HTTP contract through the
engine seam.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.app.core.security.rate_policy import RateLimitVerdict


def _verdict(is_limited=False, limit=60, period=60, remaining=59, retry_after=60, key="k"):
    headers = {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Period": str(period),
    }
    if is_limited and retry_after > 0:
        headers["Retry-After"] = str(retry_after)
    return RateLimitVerdict(
        is_limited=is_limited,
        limit=limit,
        period=period,
        remaining=remaining,
        retry_after=retry_after,
        key=key,
        headers=headers,
        bypassed=False,
    )


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
        """Create a mock current user (Q-M2: real AuthenticatedUser)."""
        from src.app.api.deps.auth import AuthenticatedUser

        return AuthenticatedUser(id=1, email="test@example.com")

    @pytest.mark.asyncio
    async def test_rate_limit_allows_request_within_limit(self, mock_response, mock_current_user):
        """Test that requests within limit are allowed."""
        from src.app.api.deps.ai_rate_limit import check_rate_limit_dependency

        with patch(
            "src.app.api.deps.ai_rate_limit.probe",
            new_callable=AsyncMock,
            return_value=_verdict(remaining=59, retry_after=60),
        ):
            result = await check_rate_limit_dependency(mock_response, mock_current_user)

            assert result == mock_current_user
            assert mock_response.headers["X-RateLimit-Limit"] == "60"
            assert mock_response.headers["X-RateLimit-Remaining"] == "59"
            assert mock_response.headers["X-RateLimit-Reset"] == "60"

    @pytest.mark.asyncio
    async def test_rate_limit_adds_headers_on_success(self, mock_response, mock_current_user):
        """Test that rate limit headers are added to response."""
        from src.app.api.deps.ai_rate_limit import check_rate_limit_dependency

        with patch(
            "src.app.api.deps.ai_rate_limit.probe",
            new_callable=AsyncMock,
            return_value=_verdict(remaining=15, retry_after=45),
        ):
            result = await check_rate_limit_dependency(mock_response, mock_current_user)

            assert result == mock_current_user
            assert mock_response.headers["X-RateLimit-Limit"] == "60"
            assert mock_response.headers["X-RateLimit-Remaining"] == "15"
            assert mock_response.headers["X-RateLimit-Reset"] == "45"

    @pytest.mark.asyncio
    async def test_rate_limit_raises_429_when_exceeded(self, mock_response, mock_current_user):
        """Test that HTTP 429 is raised when rate limit is exceeded."""
        from src.app.api.deps.ai_rate_limit import check_rate_limit_dependency

        with patch(
            "src.app.api.deps.ai_rate_limit.probe",
            new_callable=AsyncMock,
            return_value=_verdict(is_limited=True, remaining=0, retry_after=30),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await check_rate_limit_dependency(mock_response, mock_current_user)

            assert exc_info.value.status_code == 429
            assert exc_info.value.detail["error"] == "rate_limit_exceeded"
            assert exc_info.value.detail["retry_after"] == 30

    @pytest.mark.asyncio
    async def test_rate_limit_429_includes_retry_after_header(self, mock_response, mock_current_user):
        """Test that 429 response includes Retry-After header."""
        from src.app.api.deps.ai_rate_limit import check_rate_limit_dependency

        with patch(
            "src.app.api.deps.ai_rate_limit.probe",
            new_callable=AsyncMock,
            return_value=_verdict(is_limited=True, remaining=0, retry_after=45),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await check_rate_limit_dependency(mock_response, mock_current_user)

            assert exc_info.value.headers["Retry-After"] == "45"

    @pytest.mark.asyncio
    async def test_rate_limit_429_includes_rate_limit_headers(self, mock_response, mock_current_user):
        """Test that 429 response includes all rate limit headers."""
        from src.app.api.deps.ai_rate_limit import check_rate_limit_dependency

        with patch(
            "src.app.api.deps.ai_rate_limit.probe",
            new_callable=AsyncMock,
            return_value=_verdict(is_limited=True, remaining=0, retry_after=60),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await check_rate_limit_dependency(mock_response, mock_current_user)

            headers = exc_info.value.headers
            assert headers["X-RateLimit-Limit"] == "60"
            assert headers["X-RateLimit-Remaining"] == "0"
            assert headers["X-RateLimit-Reset"] == "60"

    @pytest.mark.asyncio
    async def test_rate_limit_keys_on_user_id_via_the_budget_seam(self, mock_response, mock_current_user):
        """The adapter keys the budget on the authenticated user's id."""
        from src.app.api.deps.ai_rate_limit import check_rate_limit_dependency

        with patch(
            "src.app.api.deps.ai_rate_limit.probe",
            new_callable=AsyncMock,
            return_value=_verdict(),
        ) as mock_check:
            await check_rate_limit_dependency(mock_response, mock_current_user)

            from src.app.core.security.budgets import Identity, SecurityAction

        mock_check.assert_called_once_with(SecurityAction.AI_CHAT, Identity(user_id=1))

    @pytest.mark.asyncio
    async def test_rate_limit_with_zero_remaining(self, mock_response, mock_current_user):
        """Test response when at exact rate limit (0 remaining)."""
        from src.app.api.deps.ai_rate_limit import check_rate_limit_dependency

        with patch(
            "src.app.api.deps.ai_rate_limit.probe",
            new_callable=AsyncMock,
            return_value=_verdict(remaining=0, retry_after=30),
        ):
            result = await check_rate_limit_dependency(mock_response, mock_current_user)

            assert result == mock_current_user
            assert mock_response.headers["X-RateLimit-Remaining"] == "0"
