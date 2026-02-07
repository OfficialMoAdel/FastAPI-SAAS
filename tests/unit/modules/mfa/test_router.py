"""
MFA Router Tests

Unit tests for the MFA router endpoints, including rate limiting.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest


class TestMFARateLimitingFunction:
    """Test MFA verification rate limiting function."""

    @pytest.mark.asyncio
    async def test_check_mfa_rate_limit_not_exceeded(self, mock_redis):
        """Test that rate limit is not exceeded when under limit."""
        from fastapi import Request

        from src.app.core.utils.rate_limit import rate_limiter
        from src.app.modules.mfa.router import check_mfa_rate_limit

        # Mock request object
        request = Mock(spec=Request)
        request.headers = {}
        request.client = Mock(host="192.168.1.1")

        # Mock rate limiter to return not rate limited
        with patch.object(rate_limiter, "check_rate_limit", new_callable=AsyncMock) as mock_rate_check:
            mock_rate_check.return_value = (False, 1)

            # Should not raise exception
            await check_mfa_rate_limit(request, identifier="user_123")

            mock_rate_check.assert_called_once_with("mfa_verify:user_123", limit=5, period=60)

    @pytest.mark.asyncio
    async def test_check_mfa_rate_limit_exceeded(self, mock_redis):
        """Test that rate limit exception is raised when over limit."""
        from fastapi import Request

        from src.app.core.exceptions.http_exceptions import RateLimitException
        from src.app.core.utils.rate_limit import rate_limiter
        from src.app.modules.mfa.router import MFA_VERIFY_PERIOD, check_mfa_rate_limit

        # Mock request object
        request = Mock(spec=Request)
        request.headers = {}
        request.client = Mock(host="192.168.1.1")

        # Mock rate limiter to return rate limited
        with patch.object(rate_limiter, "check_rate_limit", new_callable=AsyncMock) as mock_rate_check:
            mock_rate_check.return_value = (True, 6)

            # Should raise RateLimitException
            with pytest.raises(RateLimitException) as exc_info:
                await check_mfa_rate_limit(request, identifier="user_123")

            assert "Too many MFA verification attempts" in str(exc_info.value)
            assert str(MFA_VERIFY_PERIOD) in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_check_mfa_rate_limit_uses_client_ip(self, mock_redis):
        """Test that rate limit uses client IP when no identifier provided."""
        from fastapi import Request

        from src.app.core.utils.rate_limit import rate_limiter
        from src.app.modules.mfa.router import check_mfa_rate_limit

        # Mock request object with X-Forwarded-For header
        request = Mock(spec=Request)
        request.headers = {"X-Forwarded-For": "10.0.0.1, 10.0.0.2"}
        request.client = Mock(host="192.168.1.1")

        # Mock rate limiter
        with patch.object(rate_limiter, "check_rate_limit", new_callable=AsyncMock) as mock_rate_check:
            mock_rate_check.return_value = (False, 1)

            # Should use first IP from X-Forwarded-For
            await check_mfa_rate_limit(request)

            mock_rate_check.assert_called_once_with("mfa_verify:10.0.0.1", limit=5, period=60)

    @pytest.mark.asyncio
    async def test_check_mfa_rate_limit_uses_client_host(self, mock_redis):
        """Test that rate limit uses client host when no X-Forwarded-For."""
        from fastapi import Request

        from src.app.core.utils.rate_limit import rate_limiter
        from src.app.modules.mfa.router import check_mfa_rate_limit

        # Mock request object without X-Forwarded-For header
        request = Mock(spec=Request)
        request.headers = {}
        request.client = Mock(host="192.168.1.100")

        # Mock rate limiter
        with patch.object(rate_limiter, "check_rate_limit", new_callable=AsyncMock) as mock_rate_check:
            mock_rate_check.return_value = (False, 1)

            # Should use client host
            await check_mfa_rate_limit(request)

            mock_rate_check.assert_called_once_with("mfa_verify:192.168.1.100", limit=5, period=60)


class TestMFARouterEndpoints:
    """Test MFA router endpoint registration and basic functionality."""

    def test_mfa_router_registered(self, client):
        """MFA router should be registered in OpenAPI spec."""
        response = client.get("/openapi.json")
        assert response.status_code == 200

        spec = response.json()
        paths = spec["paths"]

        # Check that MFA endpoints exist
        assert "/api/v1/mfa/setup" in paths
        assert "/api/v1/mfa/enable" in paths
        assert "/api/v1/mfa/verify" in paths
        assert "/api/v1/mfa/disable" in paths
        assert "/api/v1/mfa/status" in paths
        assert "/api/v1/mfa/backup-codes" in paths
        assert "/api/v1/mfa/complete-login" in paths

    def test_mfa_endpoint_tags(self, client):
        """MFA endpoints should have correct tags."""
        response = client.get("/openapi.json")
        spec = response.json()

        # Check tags
        setup_endpoint = spec["paths"]["/api/v1/mfa/setup"]["post"]
        assert "mfa" in setup_endpoint["tags"]

        verify_endpoint = spec["paths"]["/api/v1/mfa/verify"]["post"]
        assert "mfa" in verify_endpoint["tags"]
