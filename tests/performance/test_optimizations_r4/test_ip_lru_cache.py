"""P0-3: IP Address LRU Cache Tests.

This module tests the LRU caching optimization for IP address parsing
in the rate limit middleware. The optimization uses functools.lru_cache
to memoize ipaddress.ip_address() calls for trusted network checks.

Tests imported from: tests/unit/middleware/test_ip_cache.py

Isomorphism Verification:
- Same IPs return same trusted/untrusted status
- Cache hits return identical results to fresh computation
- Invalid IPs are handled identically
"""

from __future__ import annotations

import pytest

from src.app.core.utils.ip_utils import (
    TRUSTED_PROXY_IPS,
    TRUSTED_PROXY_NETWORKS,
    _is_ip_in_trusted_networks,
)


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the LRU cache before each test."""
    _is_ip_in_trusted_networks.cache_clear()
    yield
    _is_ip_in_trusted_networks.cache_clear()


class TestP03IPLRUCache:
    """P0-3: IP Address LRU Cache optimization tests."""

    def test_private_10_network_is_trusted(self) -> None:
        """10.x.x.x addresses should be in trusted networks."""
        assert _is_ip_in_trusted_networks("10.0.0.1") is True
        assert _is_ip_in_trusted_networks("10.255.255.255") is True
        assert _is_ip_in_trusted_networks("10.1.2.3") is True

    def test_private_172_network_is_trusted(self) -> None:
        """172.16.x.x - 172.31.x.x addresses should be in trusted networks."""
        assert _is_ip_in_trusted_networks("172.16.0.1") is True
        assert _is_ip_in_trusted_networks("172.31.255.255") is True
        assert _is_ip_in_trusted_networks("172.20.10.5") is True

    def test_private_192_network_is_trusted(self) -> None:
        """192.168.x.x addresses should be in trusted networks."""
        assert _is_ip_in_trusted_networks("192.168.0.1") is True
        assert _is_ip_in_trusted_networks("192.168.255.255") is True
        assert _is_ip_in_trusted_networks("192.168.1.100") is True

    def test_public_ip_not_trusted(self) -> None:
        """Public IP addresses should not be in trusted networks."""
        assert _is_ip_in_trusted_networks("8.8.8.8") is False
        assert _is_ip_in_trusted_networks("1.1.1.1") is False
        assert _is_ip_in_trusted_networks("93.184.216.34") is False

    def test_invalid_ip_returns_false(self) -> None:
        """Invalid IP strings should return False (not raise)."""
        assert _is_ip_in_trusted_networks("not-an-ip") is False
        assert _is_ip_in_trusted_networks("") is False
        assert _is_ip_in_trusted_networks("256.256.256.256") is False
        assert _is_ip_in_trusted_networks("abc.def.ghi.jkl") is False

    def test_cache_hit_returns_same_result(self) -> None:
        """Cached value should match fresh computation."""
        # First call - cache miss
        result1 = _is_ip_in_trusted_networks("10.0.0.1")

        # Second call - cache hit
        result2 = _is_ip_in_trusted_networks("10.0.0.1")

        assert result1 == result2 is True

        # Check cache stats
        info = _is_ip_in_trusted_networks.cache_info()
        assert info.hits >= 1
        assert info.misses >= 1

    def test_cache_stats_tracking(self) -> None:
        """Cache should track hits and misses correctly."""
        # Clear and verify empty
        _is_ip_in_trusted_networks.cache_clear()
        info = _is_ip_in_trusted_networks.cache_info()
        assert info.hits == 0
        assert info.misses == 0

        # First call - miss
        _is_ip_in_trusted_networks("10.0.0.1")
        info = _is_ip_in_trusted_networks.cache_info()
        assert info.misses == 1
        assert info.hits == 0

        # Second call same IP - hit
        _is_ip_in_trusted_networks("10.0.0.1")
        info = _is_ip_in_trusted_networks.cache_info()
        assert info.hits == 1
        assert info.misses == 1

        # Different IP - miss
        _is_ip_in_trusted_networks("192.168.1.1")
        info = _is_ip_in_trusted_networks.cache_info()
        assert info.hits == 1
        assert info.misses == 2

    def test_different_ips_cached_separately(self) -> None:
        """Different IPs should have separate cache entries."""
        result1 = _is_ip_in_trusted_networks("10.0.0.1")  # trusted
        result2 = _is_ip_in_trusted_networks("8.8.8.8")  # not trusted

        assert result1 is True
        assert result2 is False

        # Both should be cached now
        info = _is_ip_in_trusted_networks.cache_info()
        assert info.currsize == 2

    def test_cache_maxsize_is_2048(self) -> None:
        """Cache should have correct maxsize configured."""
        info = _is_ip_in_trusted_networks.cache_info()
        assert info.maxsize == 2048


class TestP03TrustedProxyConfiguration:
    """Tests for module-level trusted proxy configuration."""

    def test_trusted_proxy_ips_contains_loopback(self) -> None:
        """TRUSTED_PROXY_IPS should contain loopback addresses."""
        assert "127.0.0.1" in TRUSTED_PROXY_IPS
        assert "::1" in TRUSTED_PROXY_IPS

    def test_trusted_proxy_networks_is_tuple(self) -> None:
        """TRUSTED_PROXY_NETWORKS should be a tuple (hashable for lru_cache)."""
        assert isinstance(TRUSTED_PROXY_NETWORKS, tuple)

    def test_trusted_proxy_networks_contains_private_ranges(self) -> None:
        """TRUSTED_PROXY_NETWORKS should contain RFC1918 private ranges."""
        network_strs = [str(n) for n in TRUSTED_PROXY_NETWORKS]
        assert "10.0.0.0/8" in network_strs
        assert "172.16.0.0/12" in network_strs
        assert "192.168.0.0/16" in network_strs


class TestP03CachePerformance:
    """Performance-related tests for the IP cache."""

    def test_many_ips_cached(self) -> None:
        """Cache should handle many different IPs."""
        for i in range(100):
            _is_ip_in_trusted_networks(f"10.0.0.{i}")

        info = _is_ip_in_trusted_networks.cache_info()
        assert info.currsize == 100
        assert info.misses == 100

    def test_repeated_ips_hit_cache(self) -> None:
        """Repeated IPs should hit cache efficiently."""
        # Warm up cache with 10 IPs
        for i in range(10):
            _is_ip_in_trusted_networks(f"10.0.0.{i}")

        initial_info = _is_ip_in_trusted_networks.cache_info()
        initial_hits = initial_info.hits

        # Access same IPs 100 times each
        for _ in range(100):
            for i in range(10):
                _is_ip_in_trusted_networks(f"10.0.0.{i}")

        final_info = _is_ip_in_trusted_networks.cache_info()
        # Should have 1000 additional hits (100 * 10)
        assert final_info.hits == initial_hits + 1000
