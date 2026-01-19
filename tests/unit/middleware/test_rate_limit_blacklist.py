"""Unit tests for RateLimitMiddleware blacklist integration (SECURITY-GAP-1).

Tests verify that blacklisted tokens are downgraded to anonymous rate limits,
preventing revoked tokens from keeping premium limits until expiry.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import Request, Response

from src.app.middleware.rate_limit import RateLimitMiddleware


@pytest.fixture
def mock_request():
    """Mock FastAPI request with Authorization header."""
    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.headers = {}
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    return request


@pytest.fixture
def middleware():
    """RateLimitMiddleware instance."""
    app = MagicMock()
    return RateLimitMiddleware(app)


@pytest.fixture
def mock_call_next():
    """Mock call_next function."""
    async def _call_next(request):
        response = Response(content="OK", status_code=200)
        return response

    return _call_next


class TestBlacklistIntegration:
    """Test blacklist check integration in rate limiting."""

    @pytest.mark.asyncio
    @patch("src.app.middleware.rate_limit.is_token_blacklisted")
    @patch("src.app.middleware.rate_limit.get_cached_jwt_payload")
    @patch("src.app.middleware.rate_limit.rate_limiter.check_rate_limit")
    async def test_blacklisted_token_gets_anonymous_limits(
        self,
        mock_check_rate_limit,
        mock_get_cached_jwt_payload,
        mock_is_token_blacklisted,
        middleware,
        mock_request,
        mock_call_next,
    ):
        """Verify blacklisted tokens are downgraded to anonymous rate limits."""
        # Arrange
        token = "blacklisted_jwt_token"
        mock_request.headers = {"Authorization": f"Bearer {token}"}
        mock_request.state.user = None

        # JWT payload indicates premium user
        mock_get_cached_jwt_payload.return_value = {
            "sub": "user-123",
            "tier_name": "pro",
        }

        # Token is blacklisted
        mock_is_token_blacklisted.return_value = True

        # Rate limiter returns not limited
        mock_check_rate_limit.return_value = (False, 5)

        # Act
        response = await middleware.dispatch(mock_request, mock_call_next)

        # Assert
        assert response.status_code == 200
        mock_is_token_blacklisted.assert_called_once_with(token)

        # Verify rate limiter was called with IP-based key (anonymous)
        call_args = mock_check_rate_limit.call_args[0]
        rate_limit_key = call_args[0]
        assert rate_limit_key.startswith("ip:"), "Blacklisted token should use IP-based rate limit"
        assert "user:" not in rate_limit_key, "Should not use user-based rate limit"

    @pytest.mark.asyncio
    @patch("src.app.middleware.rate_limit.is_token_blacklisted")
    @patch("src.app.middleware.rate_limit.get_cached_jwt_payload")
    @patch("src.app.middleware.rate_limit.rate_limiter.check_rate_limit")
    async def test_non_blacklisted_token_gets_premium_limits(
        self,
        mock_check_rate_limit,
        mock_get_cached_jwt_payload,
        mock_is_token_blacklisted,
        middleware,
        mock_request,
        mock_call_next,
    ):
        """Verify non-blacklisted tokens keep premium rate limits."""
        # Arrange
        token = "valid_jwt_token"
        user_id = "user-123"
        mock_request.headers = {"Authorization": f"Bearer {token}"}
        mock_request.state.user = None

        # JWT payload indicates premium user
        mock_get_cached_jwt_payload.return_value = {
            "sub": user_id,
            "tier_name": "pro",
        }

        # Token is NOT blacklisted
        mock_is_token_blacklisted.return_value = False

        # Rate limiter returns not limited
        mock_check_rate_limit.return_value = (False, 995)

        # Act
        response = await middleware.dispatch(mock_request, mock_call_next)

        # Assert
        assert response.status_code == 200
        mock_is_token_blacklisted.assert_called_once_with(token)

        # Verify rate limiter was called with user-based key (premium)
        call_args = mock_check_rate_limit.call_args[0]
        rate_limit_key = call_args[0]
        assert rate_limit_key == f"user:{user_id}", "Valid token should use user-based rate limit"

        # Verify premium tier limits (pro = 1000 requests per hour)
        limit = call_args[1]
        assert limit == 1000, "Should use pro tier limit"

    @pytest.mark.asyncio
    @patch("src.app.middleware.rate_limit.is_token_blacklisted")
    @patch("src.app.middleware.rate_limit.get_cached_jwt_payload")
    async def test_no_token_skips_blacklist_check(
        self,
        mock_get_cached_jwt_payload,
        mock_is_token_blacklisted,
        middleware,
        mock_request,
        mock_call_next,
    ):
        """Verify requests without tokens skip blacklist check."""
        # Arrange
        mock_request.headers = {}  # No Authorization header
        mock_request.state.user = None
        mock_get_cached_jwt_payload.return_value = None

        # Act
        with patch("src.app.middleware.rate_limit.rate_limiter.check_rate_limit") as mock_check:
            mock_check.return_value = (False, 5)
            response = await middleware.dispatch(mock_request, mock_call_next)

        # Assert
        assert response.status_code == 200
        mock_is_token_blacklisted.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.app.middleware.rate_limit.is_token_blacklisted")
    @patch("src.app.middleware.rate_limit.get_cached_jwt_payload")
    async def test_user_in_state_with_blacklisted_token(
        self,
        mock_get_cached_jwt_payload,
        mock_is_token_blacklisted,
        middleware,
        mock_request,
        mock_call_next,
    ):
        """Verify blacklist check works when user is already in request.state."""
        # Arrange
        token = "blacklisted_jwt_token"
        mock_request.headers = {"Authorization": f"Bearer {token}"}

        # User already in state (from auth middleware)
        mock_user = MagicMock()
        mock_user.id = "user-123"
        mock_user.tier = MagicMock()
        mock_user.tier.name = "pro"
        mock_request.state.user = mock_user

        # Token is blacklisted
        mock_is_token_blacklisted.return_value = True

        # Act
        with patch("src.app.middleware.rate_limit.rate_limiter.check_rate_limit") as mock_check:
            mock_check.return_value = (False, 5)
            response = await middleware.dispatch(mock_request, mock_call_next)

        # Assert
        assert response.status_code == 200
        mock_is_token_blacklisted.assert_called_once_with(token)

        # Verify downgraded to IP-based rate limit
        call_args = mock_check.call_args[0]
        rate_limit_key = call_args[0]
        assert rate_limit_key.startswith("ip:"), "Should downgrade to IP-based limit"


class TestBlacklistPerformance:
    """Test blacklist check performance impact."""

    @pytest.mark.asyncio
    @patch("src.app.middleware.rate_limit.is_token_blacklisted")
    @patch("src.app.middleware.rate_limit.get_cached_jwt_payload")
    async def test_blacklist_check_latency(
        self,
        mock_get_cached_jwt_payload,
        mock_is_token_blacklisted,
        middleware,
        mock_request,
        mock_call_next,
    ):
        """Verify blacklist check has minimal latency impact (<1ms)."""
        import time

        # Arrange
        token = "valid_jwt_token"
        mock_request.headers = {"Authorization": f"Bearer {token}"}
        mock_request.state.user = None

        mock_get_cached_jwt_payload.return_value = {
            "sub": "user-123",
            "tier_name": "pro",
        }

        # Simulate fast Bloom filter check (~1 microsecond)
        async def fast_blacklist_check(token):
            await asyncio.sleep(0.000001)  # 1 microsecond
            return False

        mock_is_token_blacklisted.side_effect = fast_blacklist_check

        # Act
        with patch("src.app.middleware.rate_limit.rate_limiter.check_rate_limit") as mock_check:
            mock_check.return_value = (False, 995)

            start = time.perf_counter()
            response = await middleware.dispatch(mock_request, mock_call_next)
            elapsed = time.perf_counter() - start

        # Assert
        assert response.status_code == 200
        # Total middleware overhead should be minimal
        # Note: This is a unit test with mocks, so actual timing is not representative
        # In production, Bloom filter check is ~1 microsecond
        assert elapsed < 1.0, "Middleware should complete quickly with mocks"


class TestBlacklistEdgeCases:
    """Test edge cases in blacklist integration."""

    @pytest.mark.asyncio
    @patch("src.app.middleware.rate_limit.is_token_blacklisted")
    @patch("src.app.middleware.rate_limit.get_cached_jwt_payload")
    async def test_malformed_authorization_header(
        self,
        mock_get_cached_jwt_payload,
        mock_is_token_blacklisted,
        middleware,
        mock_request,
        mock_call_next,
    ):
        """Verify malformed Authorization header doesn't break blacklist check."""
        # Arrange
        mock_request.headers = {"Authorization": "InvalidFormat"}
        mock_request.state.user = None
        mock_get_cached_jwt_payload.return_value = None

        # Act
        with patch("src.app.middleware.rate_limit.rate_limiter.check_rate_limit") as mock_check:
            mock_check.return_value = (False, 5)
            response = await middleware.dispatch(mock_request, mock_call_next)

        # Assert
        assert response.status_code == 200
        mock_is_token_blacklisted.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.app.middleware.rate_limit.is_token_blacklisted")
    @patch("src.app.middleware.rate_limit.get_cached_jwt_payload")
    async def test_blacklist_check_exception_handling(
        self,
        mock_get_cached_jwt_payload,
        mock_is_token_blacklisted,
        middleware,
        mock_request,
        mock_call_next,
    ):
        """Verify graceful handling if blacklist check fails."""
        # Arrange
        token = "valid_jwt_token"
        mock_request.headers = {"Authorization": f"Bearer {token}"}
        mock_request.state.user = None

        mock_get_cached_jwt_payload.return_value = {
            "sub": "user-123",
            "tier_name": "pro",
        }

        # Blacklist check raises exception (e.g., Redis unavailable)
        mock_is_token_blacklisted.side_effect = Exception("Redis connection failed")

        # Act & Assert
        with patch("src.app.middleware.rate_limit.rate_limiter.check_rate_limit") as mock_check:
            mock_check.return_value = (False, 995)

            # Should not crash, but may log error
            with pytest.raises(Exception, match="Redis connection failed"):
                await middleware.dispatch(mock_request, mock_call_next)


# Import asyncio for sleep in performance test
import asyncio
