"""Tests for `allow_internal` DNS-rebinding pinning in the URL validator.

The kill switch (`WEBHOOK_ALLOW_INTERNAL_URLS`) used to return ``None`` before
any resolution happened, so delivery passed ``resolved_ip=None`` to the
transport and the connection re-resolved the hostname at connect time — the
exact TOCTOU window pinning exists to close. These tests pin the new contract:

* ``allow_internal=True`` still resolves and returns a pin-able IP;
* cloud metadata addresses stay blocked regardless of the flag;
* everything else (HTTPS scheme, private-range rejection without the flag)
  is unchanged.
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from src.app.core.security.url_validator import (
    SSRFProtectionError,
    is_metadata_ip,
    validate_webhook_url,
)


def _addr_info(*ips: str) -> list[tuple]:
    """Build a ``socket.getaddrinfo``-shaped result for the given IPs."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0)) for ip in ips]


class TestIsMetadataIp:
    """Predicate used by the allow_internal range check."""

    def test_metadata_ipv4_blocked(self) -> None:
        assert is_metadata_ip("169.254.169.254") is True

    def test_metadata_ipv6_blocked(self) -> None:
        assert is_metadata_ip("fd00:ec2::254") is True

    def test_ipv4_mapped_metadata_blocked(self) -> None:
        assert is_metadata_ip("::ffff:169.254.169.254") is True

    def test_private_not_metadata(self) -> None:
        assert is_metadata_ip("10.0.0.5") is False
        assert is_metadata_ip("127.0.0.1") is False

    def test_public_not_metadata(self) -> None:
        assert is_metadata_ip("93.184.216.34") is False

    def test_invalid_input_fails_safe(self) -> None:
        assert is_metadata_ip("not-an-ip") is True
        assert is_metadata_ip("") is True


class TestAllowInternalPinning:
    """allow_internal must resolve and return a pin-able IP."""

    @pytest.mark.asyncio
    async def test_returns_resolved_private_ip(self) -> None:
        """Internal targets still get their resolved IP back for pinning."""
        with patch("asyncio.to_thread", return_value=_addr_info("10.0.0.5")):
            resolved = await validate_webhook_url(
                "https://consumer.internal.example/webhook",
                allow_internal=True,
            )
        assert resolved == "10.0.0.5"

    @pytest.mark.asyncio
    async def test_returns_one_of_multiple_private_ips(self) -> None:
        """Any resolved IP is returned; the caller pins it."""
        with patch("asyncio.to_thread", return_value=_addr_info("192.168.1.10", "172.16.0.9")):
            resolved = await validate_webhook_url(
                "https://consumer.internal.example/webhook",
                allow_internal=True,
            )
        assert resolved in {"192.168.1.10", "172.16.0.9"}

    @pytest.mark.asyncio
    async def test_hostname_not_resolved_until_after_scheme_check(self) -> None:
        """HTTPS scheme check still runs before any resolution."""
        with patch("asyncio.to_thread") as mock_to_thread:
            with pytest.raises(SSRFProtectionError, match="HTTPS scheme"):
                await validate_webhook_url("http://consumer.internal.example/webhook", allow_internal=True)
            mock_to_thread.assert_not_called()

    @pytest.mark.asyncio
    async def test_gaierror_still_raises(self) -> None:
        """Without a resolved IP there is nothing to pin: fail closed."""
        with patch("asyncio.to_thread", side_effect=socket.gaierror("DNS failed")):
            with pytest.raises(SSRFProtectionError, match="Could not resolve"):
                await validate_webhook_url("https://unresolvable.internal.example/webhook", allow_internal=True)


class TestAllowInternalKeepsMetadataBlocked:
    """The metadata range check survives the allow_internal flag."""

    @pytest.mark.asyncio
    async def test_ipv4_metadata_still_rejected(self) -> None:
        with patch("asyncio.to_thread", return_value=_addr_info("169.254.169.254")):
            with pytest.raises(SSRFProtectionError, match="blocked IP address: 169.254.169.254"):
                await validate_webhook_url("https://evil.example/webhook", allow_internal=True)

    @pytest.mark.asyncio
    async def test_ipv6_metadata_still_rejected(self) -> None:
        with patch(
            "asyncio.to_thread",
            return_value=[(socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("fd00:ec2::254", 0, 0, 0))],
        ):
            with pytest.raises(SSRFProtectionError, match="fd00:ec2::254"):
                await validate_webhook_url("https://evil.example/webhook", allow_internal=True)

    @pytest.mark.asyncio
    async def test_metadata_hostname_resolving_to_metadata_rejected(self) -> None:
        """Blocked-hostname check is skipped, but the IP check still catches it."""
        with patch("asyncio.to_thread", return_value=_addr_info("169.254.169.254")):
            with pytest.raises(SSRFProtectionError, match="blocked IP address: 169.254.169.254"):
                await validate_webhook_url("https://metadata.google.internal/webhook", allow_internal=True)

    @pytest.mark.asyncio
    async def test_mixed_resolution_fails_closed(self) -> None:
        """One metadata answer among private ones still rejects the URL."""
        with patch("asyncio.to_thread", return_value=_addr_info("10.0.0.5", "169.254.169.254")):
            with pytest.raises(SSRFProtectionError, match="blocked IP address"):
                await validate_webhook_url("https://evil.example/webhook", allow_internal=True)


class TestFlagOffBehaviorUnchanged:
    """Regression guard: the default path is untouched."""

    @pytest.mark.asyncio
    async def test_private_ip_rejected_without_flag(self) -> None:
        with patch("asyncio.to_thread", return_value=_addr_info("10.0.0.5")):
            with pytest.raises(SSRFProtectionError, match="blocked IP address: 10.0.0.5"):
                await validate_webhook_url("https://internal.example.com/webhook")

    @pytest.mark.asyncio
    async def test_blocked_hostname_rejected_without_flag(self) -> None:
        with patch("asyncio.to_thread", return_value=_addr_info("93.184.216.34")):
            with pytest.raises(SSRFProtectionError, match="not allowed"):
                await validate_webhook_url("https://metadata.google.internal/webhook")

    @pytest.mark.asyncio
    async def test_public_ip_still_pinned(self) -> None:
        with patch("asyncio.to_thread", return_value=_addr_info("93.184.216.34")):
            resolved = await validate_webhook_url("https://example.com/webhook")
        assert resolved == "93.184.216.34"
