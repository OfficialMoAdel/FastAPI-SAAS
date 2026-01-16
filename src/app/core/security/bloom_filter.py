"""
In-memory Bloom Filter for token blacklist pre-filtering.

A probabilistic data structure that enables fast "definitely not in set" checks.
Reduces Redis load by filtering out valid tokens before network call.

How it works:
- Uses multiple hash functions to map tokens to bit positions
- If ANY bit is 0 → token is DEFINITELY not blacklisted (no Redis call needed)
- If ALL bits are 1 → token MIGHT be blacklisted (check Redis to confirm)

Trade-offs:
- False positive rate ~1% (configurable via size and hash count)
- Zero false negatives (if filter says "not in set", it's guaranteed)
- Memory efficient: 100K tokens ≈ 1.2MB with 1% false positive rate

Usage:
    from src.app.core.security.bloom_filter import blacklist_bloom_filter

    # Check before Redis
    if not blacklist_bloom_filter.might_contain(token):
        return False  # Definitely not blacklisted, skip Redis

    # Token might be blacklisted, check Redis
    return await is_token_in_redis(token)
"""

from __future__ import annotations

import hashlib
import math
import threading
from typing import Final

# Default configuration for token blacklist
# Expected items: 100K tokens, 1% false positive rate
DEFAULT_EXPECTED_ITEMS: Final[int] = 100_000
DEFAULT_FALSE_POSITIVE_RATE: Final[float] = 0.01


class BloomFilter:
    """
    Thread-safe Bloom filter implementation for token blacklist.

    Optimized for:
    - Fast membership test (multiple hash probes)
    - Low memory footprint (bit array)
    - Thread-safe concurrent access
    """

    __slots__ = ("_bit_array", "_size", "_hash_count", "_lock", "_item_count")

    def __init__(
        self,
        expected_items: int = DEFAULT_EXPECTED_ITEMS,
        false_positive_rate: float = DEFAULT_FALSE_POSITIVE_RATE,
    ) -> None:
        """
        Initialize Bloom filter with optimal parameters.

        Args:
            expected_items: Expected number of items to store
            false_positive_rate: Desired false positive rate (0.01 = 1%)
        """
        # Calculate optimal size and hash count
        # m = -n*ln(p) / (ln(2)^2) where n=items, p=false positive rate
        self._size = self._optimal_size(expected_items, false_positive_rate)
        # k = m/n * ln(2) where m=size, n=items
        self._hash_count = self._optimal_hash_count(self._size, expected_items)

        # Use bytearray for memory efficiency
        # Each byte stores 8 bits
        byte_count = (self._size + 7) // 8
        self._bit_array = bytearray(byte_count)

        # Thread safety for concurrent access
        self._lock = threading.Lock()
        self._item_count = 0

    @staticmethod
    def _optimal_size(n: int, p: float) -> int:
        """Calculate optimal bit array size."""
        m = -n * math.log(p) / (math.log(2) ** 2)
        return max(1, int(m))

    @staticmethod
    def _optimal_hash_count(m: int, n: int) -> int:
        """Calculate optimal number of hash functions."""
        k = (m / n) * math.log(2)
        return max(1, int(k))

    def _hash_values(self, item: str) -> list[int]:
        """
        Generate multiple hash values for an item.

        Uses double hashing technique: h(i) = h1 + i*h2
        This is more efficient than computing k independent hashes.
        """
        # Use SHA-256 for good distribution
        h = hashlib.sha256(item.encode()).digest()

        # Extract two 64-bit hashes from the digest
        h1 = int.from_bytes(h[:8], "big")
        h2 = int.from_bytes(h[8:16], "big")

        # Generate k hash values using double hashing
        return [(h1 + i * h2) % self._size for i in range(self._hash_count)]

    def _get_bit(self, pos: int) -> bool:
        """Get bit at position."""
        byte_idx = pos // 8
        bit_idx = pos % 8
        return bool(self._bit_array[byte_idx] & (1 << bit_idx))

    def _set_bit(self, pos: int) -> None:
        """Set bit at position."""
        byte_idx = pos // 8
        bit_idx = pos % 8
        self._bit_array[byte_idx] |= 1 << bit_idx

    def add(self, item: str) -> None:
        """
        Add item to the filter.

        Thread-safe: uses lock for concurrent access.
        """
        positions = self._hash_values(item)
        with self._lock:
            for pos in positions:
                self._set_bit(pos)
            self._item_count += 1

    def add_batch(self, items: list[str]) -> None:
        """
        Add multiple items efficiently.

        More efficient than calling add() in a loop as it
        acquires the lock once for all items.
        """
        with self._lock:
            for item in items:
                positions = self._hash_values(item)
                for pos in positions:
                    self._set_bit(pos)
                self._item_count += 1

    def might_contain(self, item: str) -> bool:
        """
        Check if item might be in the filter.

        Returns:
            False → item is DEFINITELY not in set (no false negatives)
            True → item MIGHT be in set (possible false positive)

        Thread-safe for reads (no lock needed as we only read bits).
        """
        positions = self._hash_values(item)
        for pos in positions:
            if not self._get_bit(pos):
                return False  # Definitely not in set
        return True  # Might be in set

    def clear(self) -> None:
        """Clear the filter (remove all items)."""
        with self._lock:
            self._bit_array = bytearray(len(self._bit_array))
            self._item_count = 0

    @property
    def item_count(self) -> int:
        """Approximate count of items added."""
        return self._item_count

    @property
    def size_bytes(self) -> int:
        """Memory size in bytes."""
        return len(self._bit_array)

    @property
    def fill_ratio(self) -> float:
        """Ratio of bits set to total bits."""
        set_bits = sum(bin(byte).count("1") for byte in self._bit_array)
        return set_bits / self._size if self._size > 0 else 0.0

    def estimated_false_positive_rate(self) -> float:
        """
        Estimate current false positive rate based on fill ratio.

        Formula: (1 - e^(-kn/m))^k
        """
        if self._item_count == 0:
            return 0.0
        exponent = -self._hash_count * self._item_count / self._size
        return (1 - math.exp(exponent)) ** self._hash_count

    def stats(self) -> dict:
        """Get filter statistics."""
        return {
            "size_bits": self._size,
            "size_bytes": self.size_bytes,
            "hash_count": self._hash_count,
            "item_count": self._item_count,
            "fill_ratio": round(self.fill_ratio, 4),
            "estimated_fpr": round(self.estimated_false_positive_rate(), 6),
        }


# Singleton instance for token blacklist
# Initialize with default parameters; can be reconfigured at startup
blacklist_bloom_filter = BloomFilter(
    expected_items=DEFAULT_EXPECTED_ITEMS,
    false_positive_rate=DEFAULT_FALSE_POSITIVE_RATE,
)


async def sync_bloom_filter_from_redis() -> int:
    """
    Sync Bloom filter with Redis blacklist on startup.

    Should be called during application startup after Redis is connected.

    Returns:
        Number of tokens synced to filter
    """
    from src.app.core.utils.cache import client as redis_client

    if redis_client is None:
        return 0

    try:
        from .token_blacklist_cache import BLACKLIST_KEY

        # Get all tokens from Redis sorted set
        # Returns list of (member, score) tuples
        tokens = await redis_client.zrange(BLACKLIST_KEY, 0, -1)

        if tokens:
            # Decode bytes if needed
            token_strs = [t.decode() if isinstance(t, bytes) else t for t in tokens]
            blacklist_bloom_filter.add_batch(token_strs)

        return len(tokens) if tokens else 0
    except Exception:
        return 0


def reset_bloom_filter(
    expected_items: int = DEFAULT_EXPECTED_ITEMS,
    false_positive_rate: float = DEFAULT_FALSE_POSITIVE_RATE,
) -> None:
    """
    Reset and reconfigure the Bloom filter.

    Useful for testing or when expected parameters change significantly.
    """
    global blacklist_bloom_filter
    blacklist_bloom_filter = BloomFilter(
        expected_items=expected_items,
        false_positive_rate=false_positive_rate,
    )
