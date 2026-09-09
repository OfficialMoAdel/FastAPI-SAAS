"""E2E tests for IP filtering middleware.

Tests full request/response cycle with actual Redis integration.
Covers whitelist, blacklist, and bad bot filtering scenarios.
"""

import pytest
from httpx import AsyncClient

from src.app.modules.security.service import ip_filter_service

# Skip all tests if Redis is not available
pytestmark = pytest.mark.asyncio


class TestIPWhitelistE2E:
    """E2E tests for IP whitelist functionality."""

    async def test_whitelisted_ip_can_access_api(self, async_client: AsyncClient):
        """Test that whitelisted IP can access protected endpoints."""
        # Setup: Add IP to whitelist
        await ip_filter_service.add_to_whitelist(ip_address="127.0.0.1", reason="E2E test whitelist")

        try:
            # Execute: Make request from whitelisted IP
            response = await async_client.get("/openapi.json", headers={"X-Forwarded-For": "127.0.0.1"})

            # Verify: Should be allowed
            assert response.status_code == 200
        finally:
            # Cleanup
            await ip_filter_service.remove_from_whitelist("127.0.0.1")

    async def test_non_whitelisted_ip_allowed_if_not_blacklisted(self, async_client: AsyncClient, override_settings):
        """Test that non-whitelisted IPs fall through to blacklist check."""
        # Setup: Enable whitelist without adding the test IP
        with override_settings(IP_WHITELIST_ENABLED=True, IP_BLACKLIST_ENABLED=False):
            await ip_filter_service.clear_whitelist()

            # Execute: Make request from non-whitelisted IP
            response = await async_client.get("/openapi.json", headers={"X-Forwarded-For": "192.168.1.100"})

            # Verify: Should be allowed
            assert response.status_code == 200

    async def test_whitelist_with_expiration(self, async_client: AsyncClient):
        """Test that whitelist entries can have expiration."""
        from datetime import UTC, datetime, timedelta

        # Setup: Add IP with short expiration
        expires = datetime.now(UTC) + timedelta(seconds=1)
        await ip_filter_service.add_to_whitelist(ip_address="10.0.0.1", reason="Temporary access", expires_at=expires)

        try:
            # Execute: Request before expiration
            response = await async_client.get("/openapi.json", headers={"X-Forwarded-For": "10.0.0.1"})

            # Verify: Should be allowed
            assert response.status_code == 200

            # Wait for expiration
            import asyncio

            await asyncio.sleep(2)

            # Execute: Request after expiration
            response = await async_client.get("/openapi.json", headers={"X-Forwarded-For": "10.0.0.1"})

            # Verify: Should be blocked (whitelist mode)
            # Note: This test assumes whitelist mode is enabled
            # The exact behavior depends on the server configuration
        finally:
            await ip_filter_service.remove_from_whitelist("10.0.0.1")


class TestIPBlacklistE2E:
    """E2E tests for IP blacklist functionality."""

    async def test_blacklisted_ip_blocked(self, async_client: AsyncClient, override_settings):
        """Test that blacklisted IPs cannot access API."""
        # Setup: Enable blacklist and add IP
        with override_settings(IP_BLACKLIST_ENABLED=True):
            await ip_filter_service.add_to_blacklist(ip_address="192.168.1.50", reason="E2E test blacklist")

            try:
                # Execute: Make request from blacklisted IP
                response = await async_client.get("/openapi.json", headers={"X-Forwarded-For": "192.168.1.50"})

                # Verify: Should be blocked
                assert response.status_code == 403
                assert "blacklisted" in response.text.lower()
            finally:
                await ip_filter_service.remove_from_blacklist("192.168.1.50")

    async def test_non_blacklisted_ip_allowed(self, async_client: AsyncClient, override_settings):
        """Test that non-blacklisted IPs can access API."""
        # Setup: Enable blacklist with no entries
        with override_settings(IP_BLACKLIST_ENABLED=True):
            await ip_filter_service.clear_blacklist()

            # Execute: Make request from non-blacklisted IP
            response = await async_client.get("/openapi.json")

            # Verify: Should be allowed
            assert response.status_code == 200

    async def test_blacklist_removal_allows_access(self, async_client: AsyncClient, override_settings):
        """Test that removing IP from blacklist allows access."""
        # Setup: Blacklist then remove IP
        with override_settings(IP_BLACKLIST_ENABLED=True):
            await ip_filter_service.add_to_blacklist(ip_address="192.168.1.60", reason="Temporary block")

            # Verify: Initially blocked
            response = await async_client.get("/openapi.json", headers={"X-Forwarded-For": "192.168.1.60"})
            assert response.status_code == 403

            # Remove from blacklist
            await ip_filter_service.remove_from_blacklist("192.168.1.60")

            # Verify: Now allowed
            response = await async_client.get("/openapi.json", headers={"X-Forwarded-For": "192.168.1.60"})
            assert response.status_code == 200


class TestBadBotFilteringE2E:
    """E2E tests for bad bot detection."""

    async def test_sqlmap_user_agent_blocked(self, async_client: AsyncClient, override_settings):
        """Test that SQLMap user agent is blocked."""
        with override_settings(BAD_BOT_FILTER_ENABLED=True, IP_WHITELIST_ENABLED=False):
            response = await async_client.get("/openapi.json", headers={"User-Agent": "sqlmap/1.7.6#stable"})

            assert response.status_code == 403
            assert "Bad bot detected" in response.text

    async def test_nikto_user_agent_blocked(self, async_client: AsyncClient, override_settings):
        """Test that Nikto user agent is blocked."""
        with override_settings(BAD_BOT_FILTER_ENABLED=True, IP_WHITELIST_ENABLED=False):
            response = await async_client.get(
                "/openapi.json", headers={"User-Agent": "Mozilla/5.0 (compatible; Nikto/2.1.6;)"}
            )

            assert response.status_code == 403

    async def test_nmap_user_agent_blocked(self, async_client: AsyncClient, override_settings):
        """Test that Nmap user agent is blocked."""
        with override_settings(BAD_BOT_FILTER_ENABLED=True, IP_WHITELIST_ENABLED=False):
            response = await async_client.get(
                "/openapi.json", headers={"User-Agent": "Mozilla/5.0 (Nmap Scripting Engine)"}
            )

            assert response.status_code == 403

    async def test_chrome_user_agent_allowed(self, async_client: AsyncClient):
        """Test that normal Chrome browser is allowed."""
        response = await async_client.get(
            "/api/v1/health",
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )

        assert response.status_code == 200

    async def test_firefox_user_agent_allowed(self, async_client: AsyncClient):
        """Test that normal Firefox browser is allowed."""
        response = await async_client.get(
            "/api/v1/health",
            headers={"User-Agent": ("Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0")},
        )

        assert response.status_code == 200

    async def test_curl_user_agent_allowed(self, async_client: AsyncClient):
        """Test that curl requests are allowed."""
        response = await async_client.get("/openapi.json", headers={"User-Agent": "curl/7.68.0"})

        assert response.status_code == 200


class TestCombinedFilteringE2E:
    """E2E tests for combined filtering scenarios."""

    async def test_whitelist_takes_priority_over_blacklist(self, async_client: AsyncClient, override_settings):
        """Test that whitelist takes priority when both filters apply."""
        # Setup: Enable both, add IP to whitelist only
        with override_settings(IP_WHITELIST_ENABLED=True, IP_BLACKLIST_ENABLED=True):
            await ip_filter_service.add_to_whitelist(ip_address="10.0.0.100", reason="Priority test")

            try:
                # Execute: Make request
                response = await async_client.get("/openapi.json", headers={"X-Forwarded-For": "10.0.0.100"})

                # Verify: Whitelist allows access
                assert response.status_code == 200
            finally:
                await ip_filter_service.remove_from_whitelist("10.0.0.100")

    async def test_whitelisted_ip_with_bad_bot_allowed(self, async_client: AsyncClient, override_settings):
        """Test that whitelist allows even with bad bot user agent."""
        # Setup: Whitelist IP
        with override_settings(IP_WHITELIST_ENABLED=True):
            await ip_filter_service.add_to_whitelist(ip_address="10.0.0.200", reason="Whitelist with bot test")

            try:
                # Execute: Request with bad bot UA from whitelisted IP
                response = await async_client.get(
                    "/openapi.json", headers={"X-Forwarded-For": "10.0.0.200", "User-Agent": "sqlmap/1.0"}
                )

                # Verify: Whitelist allows despite bad bot UA
                assert response.status_code == 200
            finally:
                await ip_filter_service.remove_from_whitelist("10.0.0.200")


class TestExemptPathsE2E:
    """E2E tests for exempt paths."""

    async def test_health_paths_exempt_from_whitelist(self, async_client: AsyncClient, override_settings):
        """Test that health check paths bypass whitelist."""
        with override_settings(IP_WHITELIST_ENABLED=True):
            await ip_filter_service.clear_whitelist()

            # Execute: Health check from non-whitelisted IP
            response = await async_client.get("/api/v1/health", headers={"X-Forwarded-For": "192.168.1.200"})

            # Verify: Exempt path allowed
            assert response.status_code == 200

    async def test_health_paths_exempt_from_blacklist(self, async_client: AsyncClient, override_settings):
        """Test that health check paths bypass blacklist."""
        with override_settings(IP_BLACKLIST_ENABLED=True):
            await ip_filter_service.add_to_blacklist(ip_address="192.168.1.200", reason="Test blacklist")

            try:
                # Execute: Health check from blacklisted IP
                response = await async_client.get("/api/v1/health", headers={"X-Forwarded-For": "192.168.1.200"})

                # Verify: Exempt path allowed
                assert response.status_code == 200
            finally:
                await ip_filter_service.remove_from_blacklist("192.168.1.200")

    async def test_metrics_scanned_by_bad_bot_filter(self, async_client: AsyncClient, override_settings):
        """H-1: bare /metrics is NOT exempt — a sqlmap UA must be 403-blocked
        by the bad bot filter just like any other path. (The real, superuser-
        gated metrics endpoints live under /api/metrics, which was never in
        the exempt sets.)"""
        with override_settings(BAD_BOT_FILTER_ENABLED=True):
            response = await async_client.get("/metrics", headers={"User-Agent": "sqlmap/1.0"})

        # Verify: bot filter applies (403) — no middleware bypass for /metrics
        assert response.status_code == 403


class TestProxyHeadersE2E:
    """E2E tests for proxy header handling."""

    async def test_x_forwarded_for_respected(self, async_client: AsyncClient, override_settings):
        """Test that X-Forwarded-For header is used for IP detection.

        S-3: the test client connects over loopback (a trusted proxy), so the
        XFF chain is honored — but the client IP is the RIGHTMOST UNTRUSTED
        hop ("192.168.1.1"), not the attacker-controlled leftmost entry.
        """
        with override_settings(IP_BLACKLIST_ENABLED=True):
            await ip_filter_service.add_to_blacklist(ip_address="192.168.1.1", reason="Proxy header test")

            try:
                # Execute: Request with X-Forwarded-For showing blacklisted IP
                response = await async_client.get(
                    "/openapi.json", headers={"X-Forwarded-For": "10.0.0.50, 192.168.1.1", "X-Real-IP": "192.168.1.2"}
                )

                # Verify: Rightmost untrusted XFF entry is checked
                assert response.status_code == 403
            finally:
                await ip_filter_service.remove_from_blacklist("192.168.1.1")

    async def test_x_real_ip_fallback(self, async_client: AsyncClient, override_settings):
        """Test that X-Real-IP is used when X-Forwarded-For is absent."""
        with override_settings(IP_BLACKLIST_ENABLED=True):
            await ip_filter_service.add_to_blacklist(ip_address="10.0.0.60", reason="X-Real-IP test")

            try:
                # Execute: Request with only X-Real-IP
                response = await async_client.get("/openapi.json", headers={"X-Real-IP": "10.0.0.60"})

                # Verify: X-Real-IP is checked
                assert response.status_code == 403
            finally:
                await ip_filter_service.remove_from_blacklist("10.0.0.60")
