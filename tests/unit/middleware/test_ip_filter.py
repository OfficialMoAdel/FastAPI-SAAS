"""Unit tests for IP filter middleware.

Tests cover:
- IP extraction with proxy headers
- Whitelist/blacklist enforcement
- Bad bot detection
- Security event logging
- Path exemption handling
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request, Response
from starlette.datastructures import Headers

from src.app.core.utils.ip_utils import get_client_ip
from src.app.middleware.ip_filter import IPFilterMiddleware, _reset_bad_bot_regex
from tests.conftest import patch_setting


@pytest.fixture
def mock_request():
    """Create a mock FastAPI request."""
    request = MagicMock(spec=Request)
    request.url.path = "/api/v1/test"
    request.headers = Headers()
    request.client = MagicMock()
    request.client.host = "192.168.1.100"
    request.state = MagicMock()
    return request


@pytest.fixture
def mock_response():
    """Create a mock response."""
    return Response(content='{"test":"ok"}', status_code=200)


@pytest.fixture
def middleware():
    """Create IP filter middleware instance."""
    app = MagicMock()
    return IPFilterMiddleware(app)


class TestIPExtraction:
    """Test client IP extraction with various header scenarios."""

    def test_get_client_ip_direct(self, middleware, mock_request):
        """Test extracting IP from direct connection (non-trusted proxy)."""
        mock_request.headers = Headers()
        mock_request.client.host = "203.0.113.42"  # Public IP, not a trusted proxy

        ip = get_client_ip(mock_request)

        assert ip == "203.0.113.42"

    def test_get_client_ip_x_forwarded_for(self, middleware, mock_request):
        """Test extracting IP from X-Forwarded-For header (secure: rightmost non-trusted IP).

        With secure parsing (right-to-left), when the direct connection is a private
        IP (trusted proxy), we look for the rightmost non-trusted IP in the XFF chain.
        All 10.x IPs are in the trusted network, so the leftmost is returned as fallback.
        """
        # Simulate request from internal proxy (192.168.1.100 is private/trusted)
        mock_request.client.host = "192.168.1.100"
        # XFF chain: client at 203.0.113.1 was forwarded via two trusted proxies
        mock_request.headers = Headers({"x-forwarded-for": "203.0.113.42, 10.0.0.1, 10.0.0.2"})

        ip = get_client_ip(mock_request)

        # The rightmost non-trusted IP is 203.0.113.42
        assert ip == "203.0.113.42"

    def test_get_client_ip_x_real_ip(self, middleware, mock_request):
        """Test extracting IP from X-Real-IP header."""
        # Forces fallback to X-Real-IP by not setting X-Forwarded-For
        mock_request.client.host = "192.168.1.100"  # Trusted proxy
        mock_request.headers = Headers({"x-real-ip": "203.0.113.55"})

        ip = get_client_ip(mock_request)

        assert ip == "203.0.113.55"

    def test_get_client_ip_x_forwarded_for_priority(self, middleware, mock_request):
        """Test that X-Forwarded-For takes priority over X-Real-IP."""
        mock_request.client.host = "192.168.1.100"  # Trusted proxy
        mock_request.headers = Headers({"x-forwarded-for": "203.0.113.10", "x-real-ip": "172.16.0.50"})

        ip = get_client_ip(mock_request)

        assert ip == "203.0.113.10"

    def test_get_client_ip_unknown(self, middleware, mock_request):
        """Test handling when no IP can be determined."""
        mock_request.headers = Headers()
        mock_request.client = None

        ip = get_client_ip(mock_request)

        assert ip == "unknown"


class TestPathExemption:
    """Test path exemption logic."""

    def test_exempt_health_paths(self, middleware):
        """Test that health check paths are exempt."""
        assert middleware._is_exempt_path("/health") is True
        assert middleware._is_exempt_path("/health/live") is True
        assert middleware._is_exempt_path("/health/ready") is True

    def test_exempt_metrics_path(self, middleware):
        """Test that metrics path is exempt."""
        assert middleware._is_exempt_path("/metrics") is True

    def test_non_exempt_paths(self, middleware):
        """Test that API paths are not exempt."""
        assert middleware._is_exempt_path("/api/v1/users") is False
        assert middleware._is_exempt_path("/api/v1/auth/login") is False

    def test_exempt_with_prefix(self, middleware):
        """Test that paths starting with exempt prefixes are exempt."""
        assert middleware._is_exempt_path("/health/custom") is True
        assert middleware._is_exempt_path("/metrics/detailed") is True


class TestWhitelistEnforcement:
    """Test IP whitelist enforcement."""

    @pytest.mark.asyncio
    async def test_whitelisted_ip_allowed(self, middleware, mock_request, mock_response):
        """Test that whitelisted IPs are allowed through."""
        call_next = AsyncMock(return_value=mock_response)

        # Replace MagicMock state with a SimpleNamespace to allow normal attribute assignment
        state_obj = type("State", (), {})()
        mock_request.state = state_obj

        with (
            patch.object(middleware._filter_service, "check_ip_status", return_value=(False, True)),
            patch_setting("IP_WHITELIST_ENABLED", True, "IP_BLACKLIST_ENABLED", False),
        ):
            response = await middleware.dispatch(mock_request, call_next)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)
            assert mock_request.state.ip_whitelisted is True

    @pytest.mark.asyncio
    async def test_non_whitelisted_ip_falls_through_to_blacklist(self, middleware, mock_request, mock_response):
        """Test that non-whitelisted IPs fall through to blacklist checks when whitelist is enabled."""
        call_next = AsyncMock(return_value=mock_response)

        with (
            patch.object(middleware._filter_service, "check_ip_status", return_value=(False, False)),
            patch_setting("IP_WHITELIST_ENABLED", True, "IP_BLACKLIST_ENABLED", False),
        ):
            response = await middleware.dispatch(mock_request, call_next)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_whitelist_disabled_allows_all(self, middleware, mock_request, mock_response):
        """Test that when whitelist is disabled, all IPs are allowed."""
        call_next = AsyncMock(return_value=mock_response)

        with patch_setting("IP_WHITELIST_ENABLED", False, "IP_BLACKLIST_ENABLED", False):
            response = await middleware.dispatch(mock_request, call_next)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)


class TestBlacklistEnforcement:
    """Test IP blacklist enforcement."""

    @pytest.mark.asyncio
    async def test_blacklisted_ip_blocked(self, middleware, mock_request):
        """Test that blacklisted IPs are blocked."""
        call_next = AsyncMock()

        with (
            patch.object(middleware._filter_service, "check_ip_status", return_value=(True, False)),
            patch_setting("IP_WHITELIST_ENABLED", False, "IP_BLACKLIST_ENABLED", True),
            patch("src.app.middleware.ip_filter.log_security_event"),
        ):
            response = await middleware.dispatch(mock_request, call_next)

            assert response.status_code == 403
            assert b"blacklisted" in response.body
            call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_blacklisted_ip_allowed(self, middleware, mock_request, mock_response):
        """Test that non-blacklisted IPs are allowed."""
        call_next = AsyncMock(return_value=mock_response)

        with (
            patch.object(middleware._filter_service, "check_ip_status", return_value=(False, False)),
            patch_setting("IP_WHITELIST_ENABLED", False, "IP_BLACKLIST_ENABLED", True),
        ):
            response = await middleware.dispatch(mock_request, call_next)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_blacklist_disabled_allows_all(self, middleware, mock_request, mock_response):
        """Test that when blacklist is disabled, all IPs are allowed."""
        call_next = AsyncMock(return_value=mock_response)

        with patch_setting("IP_WHITELIST_ENABLED", False, "IP_BLACKLIST_ENABLED", False):
            response = await middleware.dispatch(mock_request, call_next)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)


class TestBadBotDetection:
    """Test bad bot/user-agent filtering."""

    @pytest.fixture(autouse=True)
    def reset_bad_bot_regex(self):
        """Reset cached bad bot regex before and after each test for isolation."""
        _reset_bad_bot_regex()
        yield
        _reset_bad_bot_regex()

    @pytest.mark.asyncio
    async def test_sqlmap_blocked(self, middleware, mock_request):
        """Test that SQLMap user agent is blocked."""
        mock_request.headers = Headers({"user-agent": "sqlmap/1.0"})
        call_next = AsyncMock()

        with (
            patch_setting("BAD_BOT_FILTER_ENABLED", True),
            patch("src.app.middleware.ip_filter.log_security_event"),
        ):
            response = await middleware.dispatch(mock_request, call_next)

            assert response.status_code == 403
            assert b"Bad bot detected" in response.body
            call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_nikto_blocked(self, middleware, mock_request):
        """Test that Nikto user agent is blocked."""
        mock_request.headers = Headers({"user-agent": "Mozilla/5.0 (Nikto/2.1.6)"})
        call_next = AsyncMock()

        with (
            patch_setting("BAD_BOT_FILTER_ENABLED", True),
            patch("src.app.middleware.ip_filter.log_security_event"),
        ):
            response = await middleware.dispatch(mock_request, call_next)

            assert response.status_code == 403
            assert b"Bad bot detected" in response.body
            call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_normal_user_agent_allowed(self, middleware, mock_request, mock_response):
        """Test that normal browsers are allowed."""
        mock_request.headers = Headers({"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        call_next = AsyncMock(return_value=mock_response)

        with patch_setting(
            "BAD_BOT_FILTER_ENABLED", True, "IP_WHITELIST_ENABLED", False, "IP_BLACKLIST_ENABLED", False
        ):
            response = await middleware.dispatch(mock_request, call_next)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_bad_bot_filter_disabled(self, middleware, mock_request, mock_response):
        """Test that bad bot filter can be disabled."""
        mock_request.headers = Headers({"user-agent": "sqlmap/1.0"})
        call_next = AsyncMock(return_value=mock_response)

        with patch_setting(
            "BAD_BOT_FILTER_ENABLED", False, "IP_WHITELIST_ENABLED", False, "IP_BLACKLIST_ENABLED", False
        ):
            response = await middleware.dispatch(mock_request, call_next)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_nmap_blocked(self, middleware, mock_request):
        """Test that Nmap user agent is blocked."""
        mock_request.headers = Headers({"user-agent": "Nmap Scripting Engine"})
        call_next = AsyncMock()

        with (
            patch_setting("BAD_BOT_FILTER_ENABLED", True),
            patch("src.app.middleware.ip_filter.log_security_event"),
        ):
            response = await middleware.dispatch(mock_request, call_next)

            assert response.status_code == 403
            call_next.assert_not_called()


class TestSecurityEventLogging:
    """Test security event logging integration."""

    @pytest.mark.asyncio
    async def test_whitelist_access_logged(self, middleware, mock_request, mock_response):
        """Test that whitelist access is logged."""
        call_next = AsyncMock(return_value=mock_response)

        with (
            patch.object(middleware._filter_service, "check_ip_status", return_value=(False, True)),
            patch_setting("IP_WHITELIST_ENABLED", True, "IP_BLACKLIST_ENABLED", False),
            patch("src.app.middleware.ip_filter.log_security_event") as mock_log,
        ):
            await middleware.dispatch(mock_request, call_next)

            mock_log.assert_called_once()
            event = mock_log.call_args[0][0]
            assert event.event_type.name == "IP_WHITELISTED"

    @pytest.mark.asyncio
    async def test_blacklist_block_logged(self, middleware, mock_request):
        """Test that blacklist block is logged."""
        call_next = AsyncMock()

        with (
            patch.object(middleware._filter_service, "check_ip_status", return_value=(True, False)),
            patch_setting("IP_WHITELIST_ENABLED", False, "IP_BLACKLIST_ENABLED", True),
            patch("src.app.middleware.ip_filter.log_security_event") as mock_log,
        ):
            await middleware.dispatch(mock_request, call_next)

            mock_log.assert_called_once()
            event = mock_log.call_args[0][0]
            assert event.event_type.name == "IP_BLOCKED"
            assert "blacklisted" in event.reason.lower()

    @pytest.mark.asyncio
    async def test_bad_bot_logged(self, middleware, mock_request):
        """Test that bad bot detection is logged."""
        mock_request.headers = Headers({"user-agent": "sqlmap/1.0"})
        call_next = AsyncMock()

        with (
            patch_setting("BAD_BOT_FILTER_ENABLED", True),
            patch("src.app.middleware.ip_filter.log_security_event") as mock_log,
        ):
            await middleware.dispatch(mock_request, call_next)

            mock_log.assert_called_once()
            event = mock_log.call_args[0][0]
            assert event.event_type.name == "ATTACK_DETECTED"
            assert "sqlmap" in event.reason.lower()


class TestExemptPathBehavior:
    """Test behavior on exempt paths."""

    @pytest.mark.asyncio
    async def test_exempt_path_skips_filtering(self, middleware, mock_request, mock_response):
        """Test that exempt paths skip all filtering."""
        mock_request.url.path = "/health/live"
        mock_request.headers = Headers({"user-agent": "sqlmap/1.0"})  # Would normally be blocked
        call_next = AsyncMock(return_value=mock_response)

        with patch_setting("IP_WHITELIST_ENABLED", True, "BAD_BOT_FILTER_ENABLED", True):
            response = await middleware.dispatch(mock_request, call_next)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_exempt_path_with_blacklisted_ip(self, middleware, mock_request, mock_response):
        """Test that exempt paths allow blacklisted IPs."""
        mock_request.url.path = "/metrics"
        mock_request.client.host = "10.0.0.1"  # Blacklisted IP
        call_next = AsyncMock(return_value=mock_response)

        with (
            patch.object(middleware._filter_service, "check_ip_status", return_value=(True, False)),
            patch_setting("IP_BLACKLIST_ENABLED", True, "BAD_BOT_FILTER_ENABLED", True),
        ):
            response = await middleware.dispatch(mock_request, call_next)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)


class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_whitelist_takes_priority_over_blacklist(self, middleware, mock_request, mock_response):
        """Test that whitelist takes priority when both are enabled."""
        call_next = AsyncMock(return_value=mock_response)

        with (
            patch.object(middleware._filter_service, "check_ip_status", return_value=(False, True)),
            patch_setting("IP_WHITELIST_ENABLED", True, "IP_BLACKLIST_ENABLED", True),
        ):
            response = await middleware.dispatch(mock_request, call_next)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_empty_user_agent_allowed(self, middleware, mock_request, mock_response):
        """Test that requests without user-agent are allowed."""
        mock_request.headers = Headers()
        call_next = AsyncMock(return_value=mock_response)

        with patch_setting(
            "BAD_BOT_FILTER_ENABLED", True, "IP_WHITELIST_ENABLED", False, "IP_BLACKLIST_ENABLED", False
        ):
            response = await middleware.dispatch(mock_request, call_next)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_ipv6_address_handling(self, middleware, mock_request, mock_response):
        """Test handling of IPv6 addresses."""
        mock_request.client.host = "2001:db8::1"
        call_next = AsyncMock(return_value=mock_response)

        with (
            patch.object(middleware._filter_service, "check_ip_status", return_value=(False, True)),
            patch_setting("IP_WHITELIST_ENABLED", True, "IP_BLACKLIST_ENABLED", False),
        ):
            response = await middleware.dispatch(mock_request, call_next)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)
