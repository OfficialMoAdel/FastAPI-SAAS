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
from src.app.middleware.ip_filter import IPFilterMiddleware, _get_bad_bot_regex, _reset_bad_bot_regex
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


async def call_middleware(middleware, mock_request, mock_response=None):
    scope = {
        "type": "http",
        "method": "GET",
        "path": mock_request.url.path,
        "headers": [(k.encode(), v.encode()) for k, v in mock_request.headers.items()],
        "state": {}
    }
    if getattr(mock_request.client, "host", None):
        scope["client"] = (mock_request.client.host, 1234)
    else:
        scope["client"] = None

    app_called = False
    async def mock_app(scope, receive, send):
        nonlocal app_called
        app_called = True
        mock_request.state.ip_whitelisted = scope.get("state", {}).get("ip_whitelisted", False)
        if mock_response:
            await mock_response(scope, receive, send)

    middleware.app = mock_app

    sent_messages = []
    async def mock_send(message):
        sent_messages.append(message)

    class AsyncMockWithCallArgs:
        async def __call__(self, *args, **kwargs):
            pass

    await middleware(scope, AsyncMockWithCallArgs(), mock_send)

    class DummyResponse:
        def __init__(self, msgs):
            self.status_code = msgs[0]["status"] if msgs and msgs[0].get("type") == "http.response.start" else 500
            self.body = b"".join(m.get("body", b"") for m in msgs if m.get("type") == "http.response.body")

    response = DummyResponse(sent_messages)

    call_next = AsyncMock()
    if app_called:
        call_next(mock_request)

    return response, call_next


@pytest.fixture
def middleware():
    """Create IP filter middleware instance."""
    app = MagicMock()
    return IPFilterMiddleware(app)


import ipaddress

import src.app.core.utils.ip_utils as _ip_utils


@pytest.fixture(autouse=True)
def _opt_into_rfc1918_trust(monkeypatch):
    """S-3: production trusts nothing network-level by default; these tests
    exercise private-proxy chains, so opt in explicitly and clear the LRU."""
    monkeypatch.setattr(
        _ip_utils,
        "TRUSTED_PROXY_NETWORKS",
        tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")),
    )
    _ip_utils._is_ip_in_trusted_networks.cache_clear()
    yield
    _ip_utils._is_ip_in_trusted_networks.cache_clear()


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

    def test_metrics_path_not_exempt(self, middleware):
        """H-1: /metrics must NOT bypass IP filtering — the real metrics
        endpoints are superuser-gated under /api/metrics and bare /metrics
        probes are scraper noise that should hit every middleware."""
        assert middleware._is_exempt_path("/metrics") is False

    def test_non_exempt_paths(self, middleware):
        """Test that API paths are not exempt."""
        assert middleware._is_exempt_path("/api/v1/users") is False
        assert middleware._is_exempt_path("/api/v1/auth/login") is False

    def test_exempt_with_prefix(self, middleware):
        """Test that paths starting with exempt prefixes are exempt."""
        assert middleware._is_exempt_path("/health/custom") is True


class TestWhitelistEnforcement:
    """Test IP whitelist enforcement."""

    @pytest.mark.asyncio
    async def test_whitelisted_ip_allowed(self, middleware, mock_request, mock_response):
        """Test that whitelisted IPs are allowed through."""
        mock_resp = mock_response

        # Replace MagicMock state with a SimpleNamespace to allow normal attribute assignment
        state_obj = type("State", (), {})()
        mock_request.state = state_obj

        with (
            patch.object(middleware._filter_service, "check_ip_status", return_value=(False, True)),
            patch_setting("IP_WHITELIST_ENABLED", True, "IP_BLACKLIST_ENABLED", False),
        ):
            response, call_next = await call_middleware(middleware, mock_request, mock_resp)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)
            assert mock_request.state.ip_whitelisted is True

    @pytest.mark.asyncio
    async def test_non_whitelisted_ip_falls_through_to_blacklist(self, middleware, mock_request, mock_response):
        """Test that non-whitelisted IPs fall through to blacklist checks when whitelist is enabled."""
        mock_resp = mock_response
        with (
            patch.object(middleware._filter_service, "check_ip_status", return_value=(False, False)),
            patch_setting("IP_WHITELIST_ENABLED", True, "IP_BLACKLIST_ENABLED", False),
        ):
            response, call_next = await call_middleware(middleware, mock_request, mock_resp)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_whitelist_disabled_allows_all(self, middleware, mock_request, mock_response):
        """Test that when whitelist is disabled, all IPs are allowed."""
        mock_resp = mock_response
        with patch_setting("IP_WHITELIST_ENABLED", False, "IP_BLACKLIST_ENABLED", False):
            response, call_next = await call_middleware(middleware, mock_request, mock_resp)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)


class TestBlacklistEnforcement:
    """Test IP blacklist enforcement."""

    @pytest.mark.asyncio
    async def test_blacklisted_ip_blocked(self, middleware, mock_request):
        """Test that blacklisted IPs are blocked."""
        mock_resp = None
        with (
            patch.object(middleware._filter_service, "check_ip_status", return_value=(True, False)),
            patch_setting("IP_WHITELIST_ENABLED", False, "IP_BLACKLIST_ENABLED", True),
            patch("src.app.middleware.ip_filter.log_security_event"),
        ):
            response, call_next = await call_middleware(middleware, mock_request, mock_resp)

            assert response.status_code == 403
            assert b"blacklisted" in response.body
            call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_blacklisted_ip_allowed(self, middleware, mock_request, mock_response):
        """Test that non-blacklisted IPs are allowed."""
        mock_resp = mock_response
        with (
            patch.object(middleware._filter_service, "check_ip_status", return_value=(False, False)),
            patch_setting("IP_WHITELIST_ENABLED", False, "IP_BLACKLIST_ENABLED", True),
        ):
            response, call_next = await call_middleware(middleware, mock_request, mock_resp)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_blacklist_disabled_allows_all(self, middleware, mock_request, mock_response):
        """Test that when blacklist is disabled, all IPs are allowed."""
        mock_resp = mock_response
        with patch_setting("IP_WHITELIST_ENABLED", False, "IP_BLACKLIST_ENABLED", False):
            response, call_next = await call_middleware(middleware, mock_request, mock_resp)

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
        mock_resp = None
        with (
            patch_setting("BAD_BOT_FILTER_ENABLED", True),
            patch("src.app.middleware.ip_filter.log_security_event"),
        ):
            response, call_next = await call_middleware(middleware, mock_request, mock_resp)

            assert response.status_code == 403
            assert b"Bad bot detected" in response.body
            call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_nikto_blocked(self, middleware, mock_request):
        """Test that Nikto user agent is blocked."""
        mock_request.headers = Headers({"user-agent": "Mozilla/5.0 (Nikto/2.1.6)"})
        mock_resp = None
        with (
            patch_setting("BAD_BOT_FILTER_ENABLED", True),
            patch("src.app.middleware.ip_filter.log_security_event"),
        ):
            response, call_next = await call_middleware(middleware, mock_request, mock_resp)

            assert response.status_code == 403
            assert b"Bad bot detected" in response.body
            call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_normal_user_agent_allowed(self, middleware, mock_request, mock_response):
        """Test that normal browsers are allowed."""
        mock_request.headers = Headers({"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        mock_resp = mock_response
        with patch_setting(
            "BAD_BOT_FILTER_ENABLED", True, "IP_WHITELIST_ENABLED", False, "IP_BLACKLIST_ENABLED", False
        ):
            response, call_next = await call_middleware(middleware, mock_request, mock_resp)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_bad_bot_filter_disabled(self, middleware, mock_request, mock_response):
        """Test that bad bot filter can be disabled."""
        mock_request.headers = Headers({"user-agent": "sqlmap/1.0"})
        mock_resp = mock_response
        with patch_setting(
            "BAD_BOT_FILTER_ENABLED", False, "IP_WHITELIST_ENABLED", False, "IP_BLACKLIST_ENABLED", False
        ):
            response, call_next = await call_middleware(middleware, mock_request, mock_resp)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_nmap_blocked(self, middleware, mock_request):
        """Test that Nmap user agent is blocked."""
        mock_request.headers = Headers({"user-agent": "Nmap Scripting Engine"})
        mock_resp = None
        with (
            patch_setting("BAD_BOT_FILTER_ENABLED", True),
            patch("src.app.middleware.ip_filter.log_security_event"),
        ):
            response, call_next = await call_middleware(middleware, mock_request, mock_resp)

            assert response.status_code == 403
            call_next.assert_not_called()


class TestSecurityEventLogging:
    """Test security event logging integration."""

    @pytest.mark.asyncio
    async def test_whitelist_access_logged(self, middleware, mock_request, mock_response):
        """Test that whitelist access is logged."""
        mock_resp = mock_response
        with (
            patch.object(middleware._filter_service, "check_ip_status", return_value=(False, True)),
            patch_setting("IP_WHITELIST_ENABLED", True, "IP_BLACKLIST_ENABLED", False),
            patch("src.app.middleware.ip_filter.log_security_event") as mock_log,
        ):
            _, call_next = await call_middleware(middleware, mock_request, mock_resp)

            mock_log.assert_called_once()
            event = mock_log.call_args[0][0]
            assert event.event_type.name == "IP_WHITELISTED"

    @pytest.mark.asyncio
    async def test_blacklist_block_logged(self, middleware, mock_request):
        """Test that blacklist block is logged."""
        mock_resp = None

        with (
            patch.object(middleware._filter_service, "check_ip_status", return_value=(True, False)),
            patch_setting("IP_WHITELIST_ENABLED", False, "IP_BLACKLIST_ENABLED", True),
            patch("src.app.middleware.ip_filter.log_security_event") as mock_log,
        ):
            _, call_next = await call_middleware(middleware, mock_request, mock_resp)

            mock_log.assert_called_once()
            event = mock_log.call_args[0][0]
            assert event.event_type.name == "IP_BLOCKED"
            assert "blacklisted" in event.reason.lower()

    @pytest.mark.asyncio
    async def test_bad_bot_logged(self, middleware, mock_request):
        """Test that bad bot detection is logged."""
        mock_request.headers = Headers({"user-agent": "sqlmap/1.0"})
        mock_resp = None

        with (
            patch_setting("BAD_BOT_FILTER_ENABLED", True),
            patch("src.app.middleware.ip_filter.log_security_event") as mock_log,
        ):
            _, call_next = await call_middleware(middleware, mock_request, mock_resp)

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
        mock_resp = mock_response

        with patch_setting("IP_WHITELIST_ENABLED", True, "BAD_BOT_FILTER_ENABLED", True):
            response, call_next = await call_middleware(middleware, mock_request, mock_resp)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_exempt_path_does_not_mark_whitelisted(self, middleware, mock_request, mock_response):
        """P2: a health probe is not a trust signal — the exempt early return
        must leave ip_whitelisted False instead of leaking a True into
        request.state."""
        mock_request.url.path = "/health/live"
        mock_resp = mock_response

        with patch_setting("IP_WHITELIST_ENABLED", True, "BAD_BOT_FILTER_ENABLED", True):
            response, call_next = await call_middleware(middleware, mock_request, mock_resp)

            assert response.status_code == 200
            assert mock_request.state.ip_whitelisted is False

    @pytest.mark.asyncio
    async def test_exempt_path_response_carries_request_id(self, middleware, mock_request):
        """P2: ip_filter runs outside RequestIDMiddleware — even exempt-path
        pass-throughs must echo X-Request-ID (incoming ID propagated)."""
        sent: list[dict] = []

        async def collect(message: dict) -> None:
            sent.append(message)

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        middleware.app = app
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/health/live",
            "headers": [(b"x-request-id", b"trace-ipf-1")],
            "state": {},
            "client": ("192.168.1.100", 1234),
        }
        await middleware(scope, AsyncMock(), collect)

        start_headers = sent[0]["headers"]
        assert (b"x-request-id", b"trace-ipf-1") in start_headers

    @pytest.mark.asyncio
    async def test_blacklist_reject_carries_request_id(self, middleware, mock_request):
        """P2: 403 short-circuits (outermost guard) must not lack X-Request-ID;
        a fresh UUID is minted when the client sends none."""
        sent: list[dict] = []

        async def collect(message: dict) -> None:
            sent.append(message)

        mock_request.url.path = "/metrics"
        mock_request.client.host = "10.0.0.1"

        class DownstreamNever:
            def __call__(self, scope, receive, send):  # pragma: no cover - must not run
                raise AssertionError("downstream must not be reached on blacklist reject")

        middleware.app = DownstreamNever()
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/metrics",
            "headers": [],
            "state": {},
            "client": ("10.0.0.1", 1234),
        }
        with (
            patch.object(middleware._filter_service, "check_ip_status", return_value=(True, False)),
            patch_setting("IP_BLACKLIST_ENABLED", True, "BAD_BOT_FILTER_ENABLED", True),
        ):
            await middleware(scope, AsyncMock(), collect)

        start = sent[0]
        assert start["status"] == 403
        request_id_headers = [(k, v) for k, v in start["headers"] if k == b"x-request-id"]
        assert len(request_id_headers) == 1
        assert request_id_headers[0][1]  # minted UUID, non-empty

    @pytest.mark.asyncio
    async def test_exempt_path_with_blacklisted_ip(self, middleware, mock_request, mock_response):
        """Test that exempt (health) paths allow blacklisted IPs.

        H-1: /metrics is no longer exempt, so this test uses a health path —
        the only paths that legitimately bypass IP filtering."""
        mock_request.url.path = "/health/live"
        mock_request.client.host = "10.0.0.1"  # Blacklisted IP
        mock_resp = mock_response
        with (
            patch.object(middleware._filter_service, "check_ip_status", return_value=(True, False)),
            patch_setting("IP_BLACKLIST_ENABLED", True, "BAD_BOT_FILTER_ENABLED", True),
        ):
            response, call_next = await call_middleware(middleware, mock_request, mock_resp)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_metrics_path_with_blacklisted_ip_is_blocked(self, middleware, mock_request, mock_response):
        """H-1: /metrics must NOT bypass the IP blacklist — scraper IPs get 403."""
        mock_request.url.path = "/metrics"
        mock_request.client.host = "10.0.0.1"
        mock_resp = mock_response
        with (
            patch.object(middleware._filter_service, "check_ip_status", return_value=(True, False)),
            patch_setting("IP_BLACKLIST_ENABLED", True, "BAD_BOT_FILTER_ENABLED", True),
        ):
            response, call_next = await call_middleware(middleware, mock_request, mock_resp)

            assert response.status_code == 403


class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_whitelist_takes_priority_over_blacklist(self, middleware, mock_request, mock_response):
        """Test that whitelist takes priority when both are enabled."""
        mock_resp = mock_response
        with (
            patch.object(middleware._filter_service, "check_ip_status", return_value=(False, True)),
            patch_setting("IP_WHITELIST_ENABLED", True, "IP_BLACKLIST_ENABLED", True),
        ):
            response, call_next = await call_middleware(middleware, mock_request, mock_resp)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_empty_user_agent_allowed(self, middleware, mock_request, mock_response):
        """Test that requests without user-agent are allowed."""
        mock_request.headers = Headers()
        mock_resp = mock_response
        with patch_setting(
            "BAD_BOT_FILTER_ENABLED", True, "IP_WHITELIST_ENABLED", False, "IP_BLACKLIST_ENABLED", False
        ):
            response, call_next = await call_middleware(middleware, mock_request, mock_resp)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_ipv6_address_handling(self, middleware, mock_request, mock_response):
        """Test handling of IPv6 addresses."""
        mock_request.client.host = "2001:db8::1"
        mock_resp = mock_response
        with (
            patch.object(middleware._filter_service, "check_ip_status", return_value=(False, True)),
            patch_setting("IP_WHITELIST_ENABLED", True, "IP_BLACKLIST_ENABLED", False),
        ):
            response, call_next = await call_middleware(middleware, mock_request, mock_resp)

            assert response.status_code == 200
            call_next.assert_called_once_with(mock_request)


class TestBadBotRegexMemo:
    """P2 micro: bad-bot regex recompiles only when the bot list changes."""

    @pytest.fixture(autouse=True)
    def reset_bad_bot_regex(self):
        _reset_bad_bot_regex()
        yield
        _reset_bad_bot_regex()

    def test_same_settings_returns_identical_object(self):
        """Hot path skips re.escape + join + .pattern compare (tuple memo)."""
        first = _get_bad_bot_regex()
        if first is None:
            pytest.skip("no bad-bot agents configured in test env")
        assert _get_bad_bot_regex() is first

    def test_settings_change_recompiles(self):
        """A changed bot list rebuilds the pattern; new bot matches."""
        import src.app.middleware.ip_filter as ip_filter_mod
        from src.app.core.config import settings

        original = list(settings.security.BAD_BOT_USER_AGENTS or [])
        before = _get_bad_bot_regex()
        assert before is not None
        with patch_setting("BAD_BOT_USER_AGENTS", [*original, "definitely-not-a-real-bot-xyz"]):
            _reset_bad_bot_regex()
            recompiled = _get_bad_bot_regex()
        assert recompiled is not None
        assert recompiled is not before
        assert recompiled.search("definitely-not-a-real-bot-xyz scanner") is not None
        assert recompiled.search("sqlmap/1.0") is not None
        assert ip_filter_mod._BAD_BOT_KEY == (*original, "definitely-not-a-real-bot-xyz")

    def test_empty_list_returns_none(self):
        """Empty bot list disables the check without compiling."""
        with patch_setting("BAD_BOT_USER_AGENTS", []):
            _reset_bad_bot_regex()
            assert _get_bad_bot_regex() is None
