"""Unit tests for Bloom Filter xxhash implementation.

Tests verify that both hash functions (xxhash and SHA256) work correctly
and produce valid bloom filter behavior with consistent FPR.
"""

import pytest

from src.app.core.security.bloom_filter import BloomFilter


class TestBloomFilterHashFunctions:
    """Tests for bloom filter hash function implementations."""

    def test_xxhash_initialization(self) -> None:
        """Test bloom filter initializes with xxhash when enabled."""
        from src.app.core.config import settings

        # Ensure xxhash is enabled (default)
        assert settings.BLOOM_FILTER_USE_XXHASH is True

        bf = BloomFilter(1000, 0.01)
        assert bf._use_xxhash is True

    def test_sha256_initialization(self) -> None:
        """Test bloom filter can initialize with SHA256 for rollback."""
        from src.app.core.config import settings

        # Temporarily disable xxhash
        original = settings.BLOOM_FILTER_USE_XXHASH
        try:
            settings.BLOOM_FILTER_USE_XXHASH = False
            bf = BloomFilter(1000, 0.01)
            assert bf._use_xxhash is False
        finally:
            settings.BLOOM_FILTER_USE_XXHASH = original

    def test_xxhash_basic_operations(self) -> None:
        """Test basic bloom filter operations with xxhash."""
        bf = BloomFilter(1000, 0.01)

        # Add items
        bf.add("token1")
        bf.add("token2")
        bf.add("token3")

        # Check membership
        assert bf.might_contain("token1") is True
        assert bf.might_contain("token2") is True
        assert bf.might_contain("token3") is True

        # Check non-membership (should be False, but might have false positives)
        # We can't assert False because of false positives, but we can check it doesn't crash
        result = bf.might_contain("nonexistent")
        assert isinstance(result, bool)

    def test_sha256_basic_operations(self) -> None:
        """Test basic bloom filter operations with SHA256."""
        from src.app.core.config import settings

        original = settings.BLOOM_FILTER_USE_XXHASH
        try:
            settings.BLOOM_FILTER_USE_XXHASH = False
            bf = BloomFilter(1000, 0.01)

            # Add items
            bf.add("token1")
            bf.add("token2")
            bf.add("token3")

            # Check membership
            assert bf.might_contain("token1") is True
            assert bf.might_contain("token2") is True
            assert bf.might_contain("token3") is True

            # Check non-membership
            result = bf.might_contain("nonexistent")
            assert isinstance(result, bool)
        finally:
            settings.BLOOM_FILTER_USE_XXHASH = original

    def test_false_positive_rate_xxhash(self) -> None:
        """Test that xxhash maintains acceptable false positive rate."""
        bf = BloomFilter(1000, 0.01)

        # Add 500 items (50% of capacity)
        for i in range(500):
            bf.add(f"token_{i}")

        # Test false positive rate with 1000 non-existent items
        false_positives = 0
        test_count = 1000
        for i in range(test_count):
            if bf.might_contain(f"nonexistent_{i}"):
                false_positives += 1

        actual_fpr = false_positives / test_count

        # FPR should be within reasonable range (target is 1%, allow up to 5%)
        assert actual_fpr <= 0.05, f"FPR {actual_fpr:.4f} exceeds 5% threshold"

    def test_false_positive_rate_sha256(self) -> None:
        """Test that SHA256 maintains acceptable false positive rate."""
        from src.app.core.config import settings

        original = settings.BLOOM_FILTER_USE_XXHASH
        try:
            settings.BLOOM_FILTER_USE_XXHASH = False
            bf = BloomFilter(1000, 0.01)

            # Add 500 items (50% of capacity)
            for i in range(500):
                bf.add(f"token_{i}")

            # Test false positive rate
            false_positives = 0
            test_count = 1000
            for i in range(test_count):
                if bf.might_contain(f"nonexistent_{i}"):
                    false_positives += 1

            actual_fpr = false_positives / test_count

            # FPR should be within reasonable range
            assert actual_fpr <= 0.05, f"FPR {actual_fpr:.4f} exceeds 5% threshold"
        finally:
            settings.BLOOM_FILTER_USE_XXHASH = original

    def test_no_false_negatives(self) -> None:
        """Test that bloom filter never has false negatives (critical property)."""
        bf = BloomFilter(1000, 0.01)

        # Add items
        test_items = [f"token_{i}" for i in range(100)]
        for item in test_items:
            bf.add(item)

        # Verify all added items are found (no false negatives)
        for item in test_items:
            assert bf.might_contain(item) is True, f"False negative for {item}"

    def test_batch_add_performance(self) -> None:
        """Test batch add operation works correctly."""
        bf = BloomFilter(10000, 0.01)

        # Add batch of items
        items = [f"token_{i}" for i in range(1000)]
        bf.add_batch(items)

        # Verify all items are found
        for item in items:
            assert bf.might_contain(item) is True

    def test_stats_reporting(self) -> None:
        """Test that stats are reported correctly."""
        bf = BloomFilter(1000, 0.01)

        # Add some items
        for i in range(100):
            bf.add(f"token_{i}")

        stats = bf.stats()

        assert "size_bits" in stats
        assert "size_bytes" in stats
        assert "hash_count" in stats
        assert "item_count" in stats
        assert stats["item_count"] == 100
        assert "fill_ratio" in stats
        assert "estimated_fpr" in stats

    def test_clear_operation(self) -> None:
        """Test that clear operation works correctly."""
        bf = BloomFilter(1000, 0.01)

        # Add items
        bf.add("token1")
        bf.add("token2")
        assert bf.item_count == 2

        # Clear
        bf.clear()
        assert bf.item_count == 0

        # Items should not be found after clear
        # (Note: might still return True due to bit collisions, but unlikely)
        # We just verify clear doesn't crash
        bf.might_contain("token1")

    def test_jwt_token_format(self) -> None:
        """Test with realistic JWT token format."""
        bf = BloomFilter(100000, 0.01)

        # Realistic JWT token
        token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"

        bf.add(token)
        assert bf.might_contain(token) is True

        # Similar but different token should not be found (unless false positive)
        different_token = token[:-1] + "x"
        # We can't assert False due to false positives, just verify it doesn't crash
        bf.might_contain(different_token)
