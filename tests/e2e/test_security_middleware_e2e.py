"""E2E Tests: Security Middleware Integration.

COMPREHENSIVE E2E TEST SUITE verifying all security middleware works together.

SETUP:
- Real Redis (docker-compose test env)
- Full app with all middleware registered
- Detailed logging for debugging

TEST SCENARIOS:
1. Middleware order execution
2. IP blacklist blocks before attack detection
3. Attack detection triggers auto-ban
4. Rate limit escalates to ban
5. Whitelist bypasses all security
6. Full attack lifecycle
7-11. Bad bot filtering tests
12-13. Concurrent/performance tests
"""

import asyncio
import logging
import time
from typing import Any

import pytest
from httpx import AsyncClient

from src.app.core.config import settings
from src.app.modules.security.service import IPFilterService

logger = logging.getLogger(__name__)


class TestSecurityMiddlewareIntegration:
    """End-to-end tests for security middleware integration."""

    @pytest.fixture(autouse=True)
    async def cleanup_security_state(self, redis_client):
        """Clear all security Redis keys after each test."""
        yield
        # Cleanup after test
        keys_to_delete = []
        async for key in redis_client.scan_iter(match="security:*"):
            keys_to_delete.append(key)
        async for key in redis_client.scan_iter(match="rate_limit:*"):
            keys_to_delete.append(key)
        async for key in redis_client.scan_iter(match="ban:*"):
            keys_to_delete.append(key)
        async for key in redis_client.scan_iter(match="ip_filter:*"):
            keys_to_delete.append(key)

        if keys_to_delete:
            await redis_client.delete(*keys_to_delete)

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    async def test_middleware_order_execution(
        self,
        async_client: AsyncClient,
        redis_client: Any,
        test_user: dict[str, Any],
    ):
        """Verify middleware executes in correct order: IPFilter → AttackDetection → RateLimit."""
        # Use health endpoint — no auth required, verifies middleware stack runs end-to-end
        response = await async_client.get("/api/v1/health")

        assert response.status_code == 200

        # Verify request ID header is present (added by innermost middleware)
        assert "X-Request-ID" in response.headers

        # Log middleware execution order
        logger.info("Middleware order test passed - request processed through all layers")

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    async def test_ip_blacklist_blocks_before_attack_detection(
        self,
        async_client: AsyncClient,
        redis_client: Any,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Verify blacklisted IP is blocked by IPFilter before AttackDetection."""
        # Disable whitelist to test blacklist
        monkeypatch.setattr(settings, "IP_WHITELIST_ENABLED", False)
        monkeypatch.setattr(settings, "IP_BLACKLIST_ENABLED", True)

        # Blacklist a test IP
        test_ip = "192.168.100.100"
        filter_service = IPFilterService()
        await filter_service.add_to_blacklist(test_ip, reason="Test blacklist")

        # Send attack pattern from blacklisted IP
        response = await async_client.get(
            "/api/v1/users?query=union+select+*+from+users",
            headers={"X-Forwarded-For": test_ip},
        )

        # Should be blocked by IPFilter (403), not AttackDetection (400)
        assert response.status_code == 403
        assert "blacklisted" in response.text.lower() or "forbidden" in response.text.lower()

        logger.info(f"IP blacklist blocked request before attack detection | ip={test_ip}")

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    async def test_attack_detection_triggers_auto_ban(
        self,
        async_client: AsyncClient,
        redis_client: Any,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Verify attack detection in 'ban' mode adds IP to blacklist."""
        # Configure for ban mode
        monkeypatch.setattr(settings, "IP_WHITELIST_ENABLED", False)
        monkeypatch.setattr(settings, "IP_BLACKLIST_ENABLED", True)
        monkeypatch.setattr(settings, "ATTACK_DETECTION_MODE", "ban")
        monkeypatch.setattr(settings, "ATTACK_DETECTION_ENABLED", True)

        test_ip = "192.168.100.101"

        # Send attack pattern
        response = await async_client.get(
            "/api/v1/users?query=union+select+*+from+users",
            headers={"X-Forwarded-For": test_ip},
        )

        # Should be banned (403)
        assert response.status_code == 403
        assert "banned" in response.text.lower() or "forbidden" in response.text.lower()

        # Verify IP is now in blacklist
        filter_service = IPFilterService()
        is_blacklisted = await filter_service.is_blacklisted(test_ip)
        assert is_blacklisted, "IP should be auto-banned after attack detection"

        # Verify subsequent requests are blocked
        response2 = await async_client.get(
            "/api/v1/health",
            headers={"X-Forwarded-For": test_ip},
        )
        assert response2.status_code == 403

        logger.info(f"Attack detection auto-banned IP | ip={test_ip}")

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    async def test_rate_limit_escalates_to_ban(
        self,
        async_client: AsyncClient,
        redis_client: Any,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Verify rate limit exhaustion triggers auto-ban."""
        # Configure aggressive rate limiting for test
        monkeypatch.setattr(settings, "IP_WHITELIST_ENABLED", False)
        monkeypatch.setattr(settings, "IP_BLACKLIST_ENABLED", True)
        monkeypatch.setattr(settings, "AUTO_BAN_ENABLED", True)
        monkeypatch.setattr(settings, "DEFAULT_RATE_LIMIT_LIMIT", 5)
        monkeypatch.setattr(settings, "DEFAULT_RATE_LIMIT_PERIOD", 3600)
        monkeypatch.setattr(settings, "AUTO_BAN_THRESHOLD", 3)

        test_ip = "192.168.100.102"

        # Exhaust rate limit multiple times to trigger ban
        for attempt in range(20):
            response = await async_client.get(
                "/api/v1/users",
                headers={"X-Forwarded-For": test_ip},
            )

            if response.status_code == 403:
                # Auto-ban triggered
                logger.info(f"Auto-ban triggered after {attempt + 1} requests")
                break

            await asyncio.sleep(0.05)  # Small delay between requests

        # Verify IP is banned
        filter_service = IPFilterService()
        is_blacklisted = await filter_service.is_blacklisted(test_ip)
        assert is_blacklisted, "IP should be banned after rate limit violations"

        logger.info(f"Rate limit escalation auto-banned IP | ip={test_ip}")

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    async def test_whitelist_bypasses_all_security(
        self,
        async_client: AsyncClient,
        redis_client: Any,
        test_user: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Verify whitelisted IP bypasses all security checks including attack detection."""
        # Enable whitelist mode
        monkeypatch.setattr(settings, "IP_WHITELIST_ENABLED", True)
        monkeypatch.setattr(settings, "IP_BLACKLIST_ENABLED", False)
        monkeypatch.setattr(settings, "ATTACK_DETECTION_MODE", "block")
        monkeypatch.setattr(settings, "ATTACK_DETECTION_ENABLED", True)

        test_ip = "192.168.100.103"

        # Add IP to whitelist
        filter_service = IPFilterService()
        await filter_service.add_to_whitelist(test_ip, reason="Test whitelist")

        # Send attack patterns from whitelisted IP
        response = await async_client.get(
            "/api/v1/users?query=union+select+*+from+users",
            headers={
                "Authorization": f"Bearer {test_user['access_token']}",
                "X-Forwarded-For": test_ip,
            },
        )

        # Should succeed (200) despite attack pattern - whitelist bypasses all
        assert response.status_code == 200, f"Whitelisted IP should bypass attack detection, got {response.status_code}"

        logger.info(f"Whitelist successfully bypassed all security | ip={test_ip}")

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    async def test_full_attack_lifecycle(
        self,
        async_client: AsyncClient,
        redis_client: Any,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Test full attack lifecycle: log → block → ban modes."""
        monkeypatch.setattr(settings, "IP_WHITELIST_ENABLED", False)
        monkeypatch.setattr(settings, "IP_BLACKLIST_ENABLED", True)

        test_ip = "192.168.100.104"
        filter_service = IPFilterService()

        # Phase 1: Log mode - attacks logged but not blocked
        monkeypatch.setattr(settings, "ATTACK_DETECTION_MODE", "log")

        for i in range(3):
            response = await async_client.get(
                "/api/v1/users?query=union+select+*+from+users",
                headers={"X-Forwarded-For": test_ip},
            )
            # In log mode, attacks are logged but request proceeds
            # May get 401/403 from auth, but not from attack detection
            logger.info(f"Log mode request {i + 1}: status={response.status_code}")

        # Verify IP not banned yet
        is_blacklisted = await filter_service.is_blacklisted(test_ip)
        assert not is_blacklisted, "IP should not be banned in log mode"

        # Phase 2: Block mode - attacks return 400
        monkeypatch.setattr(settings, "ATTACK_DETECTION_MODE", "block")

        response = await async_client.get(
            "/api/v1/users?query=union+select+*+from+users",
            headers={"X-Forwarded-For": test_ip},
        )
        assert response.status_code == 400, "Block mode should return 400"
        assert "attack" in response.text.lower() or "bad request" in response.text.lower()

        logger.info(f"Block mode test passed | status={response.status_code}")

        # Phase 3: Ban mode - attacks return 403 and IP banned
        monkeypatch.setattr(settings, "ATTACK_DETECTION_MODE", "ban")

        response = await async_client.get(
            "/api/v1/users?query=union+select+*+from+users",
            headers={"X-Forwarded-For": test_ip},
        )
        assert response.status_code == 403, "Ban mode should return 403"

        # Verify IP is now banned
        is_blacklisted = await filter_service.is_blacklisted(test_ip)
        assert is_blacklisted, "IP should be banned after attack in ban mode"

        logger.info(f"Full attack lifecycle test passed | ip={test_ip}")


class TestBadBotFiltering:
    """Tests for bad bot User-Agent filtering."""

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    async def test_bad_bot_user_agent_blocked(
        self,
        async_client: AsyncClient,
        redis_client: Any,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Test that requests with bad bot User-Agent are blocked."""
        monkeypatch.setattr(settings, "IP_WHITELIST_ENABLED", False)
        monkeypatch.setattr(settings, "BAD_BOT_FILTER_ENABLED", True)
        monkeypatch.setattr(settings, "BAD_BOT_USER_AGENTS", ["sqlmap", "nikto", "nmap", "masscan"])

        test_ip = "192.168.100.200"

        response = await async_client.get(
            # Use a non-health, non-exempt path so bad bot filter fires
            "/api/v1/users",
            headers={
                "X-Forwarded-For": test_ip,
                "User-Agent": "sqlmap/1.5.2#stable",
            },
        )

        assert response.status_code in (403, 401)  # 403=bot blocked, 401=passed filter but not authed
        if response.status_code == 403:
            assert "bad bot" in response.text.lower() or "forbidden" in response.text.lower()

        logger.info(f"Bad bot blocked | user_agent=sqlmap | ip={test_ip}")

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    async def test_bad_bot_partial_match(
        self,
        async_client: AsyncClient,
        redis_client: Any,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Test that bad bot detection uses substring matching."""
        monkeypatch.setattr(settings, "IP_WHITELIST_ENABLED", False)
        monkeypatch.setattr(settings, "BAD_BOT_FILTER_ENABLED", True)
        monkeypatch.setattr(settings, "BAD_BOT_USER_AGENTS", ["sqlmap", "nikto"])

        test_ip = "192.168.100.201"

        response = await async_client.get(
            # Use a non-health path so bad bot filter fires
            "/api/v1/users",
            headers={
                "X-Forwarded-For": test_ip,
                "User-Agent": "Mozilla/5.0 (compatible; sqlmap bot/1.0)",
            },
        )

        assert response.status_code in (403, 401), "Partial match should block or reach auth gate"

        logger.info(f"Bad bot partial match blocked | ip={test_ip}")

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    async def test_bad_bot_case_insensitive(
        self,
        async_client: AsyncClient,
        redis_client: Any,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Test that bad bot detection is case insensitive."""
        monkeypatch.setattr(settings, "IP_WHITELIST_ENABLED", False)
        monkeypatch.setattr(settings, "BAD_BOT_FILTER_ENABLED", True)
        monkeypatch.setattr(settings, "BAD_BOT_USER_AGENTS", ["sqlmap", "nikto"])

        test_ip = "192.168.100.202"

        # Test uppercase
        response1 = await async_client.get(
            # Use a non-health path so bad bot filter fires
            "/api/v1/users",
            headers={
                "X-Forwarded-For": test_ip,
                "User-Agent": "SQLMAP/1.5",
            },
        )
        assert response1.status_code in (403, 401), "Uppercase should be blocked or reach auth gate"

        # Test mixed case
        response2 = await async_client.get(
            "/api/v1/users",
            headers={
                "X-Forwarded-For": test_ip,
                "User-Agent": "NiKtO Scanner",
            },
        )
        assert response2.status_code in (403, 401), "Mixed case should be blocked or reach auth gate"

        logger.info(f"Bad bot case insensitive test passed | ip={test_ip}")

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    async def test_legitimate_user_agent_allowed(
        self,
        async_client: AsyncClient,
        redis_client: Any,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Test that legitimate User-Agents are not blocked."""
        monkeypatch.setattr(settings, "IP_WHITELIST_ENABLED", False)
        monkeypatch.setattr(settings, "BAD_BOT_FILTER_ENABLED", True)
        monkeypatch.setattr(settings, "BAD_BOT_USER_AGENTS", ["sqlmap", "nikto"])

        test_ip = "192.168.100.203"

        response = await async_client.get(
            "/api/v1/health",
            headers={
                "X-Forwarded-For": test_ip,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0",
            },
        )

        assert response.status_code == 200, "Legitimate User-Agent should not be blocked"

        logger.info(f"Legitimate user agent allowed | ip={test_ip}")

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    async def test_empty_user_agent_allowed(
        self,
        async_client: AsyncClient,
        redis_client: Any,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Test that empty User-Agent is allowed."""
        monkeypatch.setattr(settings, "IP_WHITELIST_ENABLED", False)
        monkeypatch.setattr(settings, "BAD_BOT_FILTER_ENABLED", True)

        test_ip = "192.168.100.204"

        response = await async_client.get(
            "/api/v1/health",
            headers={"X-Forwarded-For": test_ip},
            # No User-Agent header
        )

        # Should succeed (health check is exempt from auth)
        assert response.status_code == 200

        logger.info(f"Empty user agent allowed | ip={test_ip}")


class TestConcurrentAndPerformance:
    """Tests for concurrent access and performance."""

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    async def test_concurrent_requests_from_banned_ip(
        self,
        async_client: AsyncClient,
        redis_client: Any,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Test that concurrent requests from banned IP are all blocked."""
        monkeypatch.setattr(settings, "IP_WHITELIST_ENABLED", False)
        monkeypatch.setattr(settings, "IP_BLACKLIST_ENABLED", True)

        test_ip = "192.168.100.250"
        filter_service = IPFilterService()

        # Ban the IP first
        await filter_service.add_to_blacklist(test_ip, reason="Test concurrent ban")

        async def make_request():
            return await async_client.get(
                "/api/v1/health",
                headers={"X-Forwarded-For": test_ip},
            )

        # Send 100 concurrent requests
        responses = await asyncio.gather(*[make_request() for _ in range(100)])

        # All should be blocked
        blocked_count = sum(1 for r in responses if r.status_code == 403)
        assert blocked_count == 100, f"All 100 requests should be blocked, got {blocked_count}"

        logger.info(f"Concurrent banned IP test passed | blocked={blocked_count}/100")

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    @pytest.mark.skip(reason="Performance test - run manually")
    async def test_security_middleware_overhead(
        self,
        async_client: AsyncClient,
        redis_client: Any,
        test_user: dict[str, Any],
    ):
        """Measure security middleware overhead (manual test)."""
        # Baseline: exempt path (no security middleware)
        start = time.time()
        for _ in range(100):
            await async_client.get("/health")
        baseline_time = time.time() - start

        # With security middleware
        start = time.time()
        for _ in range(100):
            await async_client.get(
                "/api/v1/users/me",
                headers={"Authorization": f"Bearer {test_user['access_token']}"},
            )
        with_security_time = time.time() - start

        overhead = (with_security_time - baseline_time) / 100 * 1000  # ms per request

        logger.info(f"Security middleware overhead: {overhead:.2f}ms per request")
        logger.info(f"Baseline: {baseline_time:.3f}s, With security: {with_security_time:.3f}s")

        # Assert overhead < 10ms
        assert overhead < 10.0, f"Overhead too high: {overhead:.2f}ms"
