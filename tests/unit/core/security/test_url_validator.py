"""Tests for URL validator with async DNS resolution (OPT P0-1)."""

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


class TestIsIPBlocked:
    """Tests for IP blocking logic."""

    def test_blocks_loopback(self) -> None:
        """Loopback addresses should be blocked."""
        assert is_ip_blocked("127.0.0.1") is True
        assert is_ip_blocked("127.0.0.100") is True

    def test_blocks_private_ranges(self) -> None:
        """Private IP ranges should be blocked."""
        assert is_ip_blocked("10.0.0.1") is True
        assert is_ip_blocked("172.16.0.1") is True
        assert is_ip_blocked("192.168.1.1") is True

    def test_blocks_link_local(self) -> None:
        """Link-local (cloud metadata) addresses should be blocked."""
        assert is_ip_blocked("169.254.169.254") is True

    def test_allows_public_ips(self) -> None:
        """Public IP addresses should be allowed."""
        assert is_ip_blocked("8.8.8.8") is False
        assert is_ip_blocked("1.1.1.1") is False
        assert is_ip_blocked("93.184.216.34") is False  # example.com

    def test_blocks_invalid_ip(self) -> None:
        """Invalid IP formats should be blocked (fail-safe)."""
        assert is_ip_blocked("not-an-ip") is True
        assert is_ip_blocked("") is True


class TestValidateWebhookUrl:
    """Tests for async webhook URL validation."""

    @pytest.mark.asyncio
    async def test_rejects_http_scheme(self) -> None:
        """Non-HTTPS URLs should be rejected."""
        with pytest.raises(SSRFProtectionError, match="HTTPS scheme"):
            await validate_webhook_url("http://example.com/webhook")

    @pytest.mark.asyncio
    async def test_rejects_missing_hostname(self) -> None:
        """URLs without hostname should be rejected."""
        with pytest.raises(SSRFProtectionError, match="valid hostname"):
            await validate_webhook_url("https:///webhook")

    @pytest.mark.asyncio
    async def test_rejects_blocked_hostnames(self) -> None:
        """Cloud metadata hostnames should be rejected."""
        with pytest.raises(SSRFProtectionError, match="not allowed"):
            await validate_webhook_url("https://metadata.google.internal/webhook")

    @pytest.mark.asyncio
    async def test_rejects_private_ip_resolution(self) -> None:
        """URLs resolving to private IPs should be rejected."""
        # Mock DNS to return private IP
        mock_addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.1", 0))]
        with patch("asyncio.to_thread", return_value=mock_addr_info):
            with pytest.raises(SSRFProtectionError, match="blocked IP"):
                await validate_webhook_url("https://internal.example.com/webhook")

    @pytest.mark.asyncio
    async def test_accepts_valid_https_url(self) -> None:
        """Valid HTTPS URLs with public IPs should be accepted."""
        # Mock DNS to return public IP
        mock_addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]
        with patch("asyncio.to_thread", return_value=mock_addr_info):
            # Should not raise
            await validate_webhook_url("https://example.com/webhook")

    @pytest.mark.asyncio
    async def test_handles_dns_failure(self) -> None:
        """DNS resolution failures should be handled gracefully."""
        with patch("asyncio.to_thread", side_effect=socket.gaierror("DNS failed")):
            with pytest.raises(SSRFProtectionError, match="Could not resolve"):
                await validate_webhook_url("https://nonexistent.invalid/webhook")

    @pytest.mark.asyncio
    async def test_is_async_and_non_blocking(self) -> None:
        """Verify the function is async and uses asyncio.to_thread."""
        # This test verifies the function can be awaited and uses to_thread
        mock_addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

        with patch("asyncio.to_thread", return_value=mock_addr_info) as mock_to_thread:
            await validate_webhook_url("https://example.com/webhook")

            # Verify asyncio.to_thread was called with socket.getaddrinfo
            mock_to_thread.assert_called_once()
            args = mock_to_thread.call_args[0]
            assert args[0] == socket.getaddrinfo
            assert args[1] == "example.com"

    @pytest.mark.asyncio
    async def test_concurrent_validations(self) -> None:
        """Multiple concurrent validations should work correctly."""
        mock_addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

        with patch("asyncio.to_thread", return_value=mock_addr_info):
            # Run 10 concurrent validations
            tasks = [validate_webhook_url(f"https://example{i}.com/webhook") for i in range(10)]
            # All should complete without error
            await asyncio.gather(*tasks)
