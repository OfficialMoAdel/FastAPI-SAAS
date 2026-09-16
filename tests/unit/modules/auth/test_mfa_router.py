"""
MFA Router Tests

Unit tests for the MFA router endpoints, including rate limiting.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest


class TestMFARateLimitingFunction:
    """Test MFA verification rate limiting through the engine budget seam."""

    @pytest.mark.asyncio
    async def test_check_mfa_rate_limit_not_exceeded(self, mock_redis):
        """Test that rate limit is not exceeded when under limit."""
        from fastapi import Request

        from src.app.core.security.rate_policy import RateLimitVerdict
        from src.app.modules.auth.routers.mfa import check_mfa_rate_limit

        request = Mock(spec=Request)
        request.headers = {}
        request.client = Mock(host="192.168.1.1")

        verdict = RateLimitVerdict(
            is_limited=False,
            limit=5,
            period=60,
            remaining=4,
            retry_after=0,
            key="k",
            headers={},
            bypassed=False,
        )
        with patch(
            "src.app.modules.auth.routers.mfa.check_security_budget",
            new_callable=AsyncMock,
            return_value=verdict,
        ) as mock_budget:
            await check_mfa_rate_limit(request, identifier="user_123")

            mock_budget.assert_called_once_with("mfa_verify", "user_123")

    @pytest.mark.asyncio
    async def test_check_mfa_rate_limit_exceeded(self, mock_redis):
        """Test that rate limit exception is raised when over limit."""
        from fastapi import Request

        from src.app.core.security.rate_policy import RateLimitVerdict
        from src.app.modules.auth.routers.mfa import check_mfa_rate_limit
        from src.app.shared.exceptions import RateLimitExceededError as RateLimitException

        request = Mock(spec=Request)
        request.headers = {}
        request.client = Mock(host="192.168.1.1")

        verdict = RateLimitVerdict(
            is_limited=True,
            limit=5,
            period=60,
            remaining=0,
            retry_after=42,
            key="k",
            headers={},
            bypassed=False,
        )
        with patch(
            "src.app.modules.auth.routers.mfa.check_security_budget",
            new_callable=AsyncMock,
            return_value=verdict,
        ):
            with pytest.raises(RateLimitException) as exc_info:
                await check_mfa_rate_limit(request, identifier="user_123")

            assert "60" in str(exc_info.value)  # period in the message

    @pytest.mark.asyncio
    async def test_check_mfa_rate_limit_uses_client_ip(self, mock_redis, monkeypatch):
        """Test that the budget is keyed on the resolved client IP.

        SECURITY: get_client_ip parses X-Forwarded-For right-to-left and
        only trusts it when the direct peer is itself a trusted proxy —
        the raw leftmost XFF entry is attacker-controlled and must never
        be keyed directly.
        """
        from fastapi import Request

        from src.app.core.security.rate_policy import RateLimitVerdict
        from src.app.modules.auth.routers.mfa import check_mfa_rate_limit

        request = Mock(spec=Request)
        request.headers = {"X-Forwarded-For": "10.0.0.2, 127.0.0.1"}
        # Direct peer 127.0.0.1 IS a trusted proxy, so XFF is parsed and
        # the rightmost untrusted IP (10.0.0.2) wins.
        request.client = Mock(host="127.0.0.1")

        verdict = RateLimitVerdict(
            is_limited=False,
            limit=5,
            period=60,
            remaining=4,
            retry_after=0,
            key="k",
            headers={},
            bypassed=False,
        )
        with patch(
            "src.app.modules.auth.routers.mfa.check_security_budget",
            new_callable=AsyncMock,
            return_value=verdict,
        ) as mock_budget:
            await check_mfa_rate_limit(request)

            mock_budget.assert_called_once_with("mfa_verify", "10.0.0.2")

    @pytest.mark.asyncio
    async def test_check_mfa_mutate_uses_separate_budget(self, mock_redis):
        """P1-5: mutations cross a separate budget (mfa_mutate), keyed on user_id."""
        from fastapi import Request

        from src.app.core.security.rate_policy import RateLimitVerdict
        from src.app.modules.auth.routers.mfa import check_mfa_mutate_rate_limit

        request = Mock(spec=Request)

        verdict = RateLimitVerdict(
            is_limited=False,
            limit=5,
            period=60,
            remaining=4,
            retry_after=0,
            key="k",
            headers={},
            bypassed=False,
        )
        with patch(
            "src.app.modules.auth.routers.mfa.check_security_budget",
            new_callable=AsyncMock,
            return_value=verdict,
        ) as mock_budget:
            await check_mfa_mutate_rate_limit(request, user_id=42)

            mock_budget.assert_called_once_with("mfa_mutate", "42")


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
