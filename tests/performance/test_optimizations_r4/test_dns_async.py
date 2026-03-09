"""P0-1: Async DNS Resolution Tests.

This module tests the async DNS resolution optimization in url_validator.
The optimization wraps blocking socket.getaddrinfo() in asyncio.to_thread()
to prevent event loop blocking during webhook URL validation.

Tests imported from: tests/unit/security/test_url_validator.py

Isomorphism Verification:
- Same URLs are allowed/blocked before and after
- DNS resolution errors are handled identically
- SSRF protection logic is unchanged
"""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import patch

import pytest

from src.app.core.security.url_validator import (
    SSRFProtectionError,
    is_ip_blocked,
    validate_webhook_url,
)


class TestP01AsyncDNS:
    """P0-1: Async DNS Resolution optimization tests."""

    @pytest.mark.asyncio
    async def test_async_dns_uses_to_thread(self) -> None:
        """Verify asyncio.to_thread is used for non-blocking DNS resolution."""
        mock_addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

        with patch("asyncio.to_thread", return_value=mock_addr_info) as mock_to_thread:
            await validate_webhook_url("https://example.com/webhook")

            # Verify asyncio.to_thread was called with socket.getaddrinfo
            mock_to_thread.assert_called_once()
            args = mock_to_thread.call_args[0]
            assert args[0] == socket.getaddrinfo
            assert args[1] == "example.com"

    @pytest.mark.asyncio
    async def test_concurrent_dns_resolutions(self) -> None:
        """Multiple concurrent DNS lookups should work without blocking."""
        mock_addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

        with patch("asyncio.to_thread", return_value=mock_addr_info):
            # Run 10 concurrent validations
            tasks = [validate_webhook_url(f"https://example{i}.com/webhook") for i in range(10)]
            # All should complete without blocking
            await asyncio.gather(*tasks)

    @pytest.mark.asyncio
    async def test_dns_failure_handling(self) -> None:
        """DNS resolution failures should raise SSRFProtectionError."""
        with patch("asyncio.to_thread", side_effect=socket.gaierror("DNS failed")):
            with pytest.raises(SSRFProtectionError, match="Could not resolve"):
                await validate_webhook_url("https://nonexistent.invalid/webhook")

    @pytest.mark.asyncio
    async def test_private_ip_blocked_after_resolution(self) -> None:
        """URLs resolving to private IPs should be blocked."""
        mock_addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.1", 0))]
        with patch("asyncio.to_thread", return_value=mock_addr_info):
            with pytest.raises(SSRFProtectionError, match="blocked IP"):
                await validate_webhook_url("https://internal.example.com/webhook")

    @pytest.mark.asyncio
    async def test_public_ip_allowed_after_resolution(self) -> None:
        """URLs resolving to public IPs should be allowed."""
        mock_addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]
        with patch("asyncio.to_thread", return_value=mock_addr_info):
            # Should not raise
            await validate_webhook_url("https://example.com/webhook")


class TestP01IPBlocking:
    """IP blocking logic tests for SSRF protection."""

    def test_blocks_loopback(self) -> None:
        """Loopback addresses should be blocked."""
        assert is_ip_blocked("127.0.0.1") is True
        assert is_ip_blocked("127.0.0.100") is True

    def test_blocks_private_10_range(self) -> None:
        """10.x.x.x addresses should be blocked."""
        assert is_ip_blocked("10.0.0.1") is True
        assert is_ip_blocked("10.255.255.255") is True

    def test_blocks_private_172_range(self) -> None:
        """172.16-31.x.x addresses should be blocked."""
        assert is_ip_blocked("172.16.0.1") is True
        assert is_ip_blocked("172.31.255.255") is True

    def test_blocks_private_192_range(self) -> None:
        """192.168.x.x addresses should be blocked."""
        assert is_ip_blocked("192.168.0.1") is True
        assert is_ip_blocked("192.168.255.255") is True

    def test_blocks_cloud_metadata(self) -> None:
        """Cloud metadata addresses should be blocked."""
        assert is_ip_blocked("169.254.169.254") is True

    def test_allows_public_ips(self) -> None:
        """Public IPs should be allowed."""
        assert is_ip_blocked("8.8.8.8") is False
        assert is_ip_blocked("1.1.1.1") is False
        assert is_ip_blocked("93.184.216.34") is False

    def test_blocks_invalid_ips(self) -> None:
        """Invalid IP formats should be blocked (fail-safe)."""
        assert is_ip_blocked("not-an-ip") is True
        assert is_ip_blocked("") is True
