"""
Unit tests for Bloom filter implementation.
"""


from src.app.core.security.bloom_filter import (
    BloomFilter,
    blacklist_bloom_filter,
    reset_bloom_filter,
)


class TestBloomFilterBasicOperations:
    """Tests for basic Bloom filter operations."""

    def test_add_and_might_contain(self) -> None:
        """Added items should be found by might_contain."""
        bf = BloomFilter(expected_items=1000, false_positive_rate=0.01)
        bf.add("token123")
        assert bf.might_contain("token123") is True

    def test_item_not_added_returns_false(self) -> None:
        """Items not added should return False (no false negatives)."""
        bf = BloomFilter(expected_items=1000, false_positive_rate=0.01)
        # Fresh filter, nothing added
        assert bf.might_contain("never_added") is False

    def test_add_multiple_items(self) -> None:
        """Multiple items should all be found."""
        bf = BloomFilter(expected_items=1000, false_positive_rate=0.01)
        tokens = ["token1", "token2", "token3", "token4", "token5"]
        for token in tokens:
            bf.add(token)

        for token in tokens:
            assert bf.might_contain(token) is True

    def test_add_batch(self) -> None:
        """Batch add should work like individual adds."""
        bf = BloomFilter(expected_items=1000, false_positive_rate=0.01)
        tokens = ["batch1", "batch2", "batch3"]
        bf.add_batch(tokens)

        for token in tokens:
            assert bf.might_contain(token) is True

    def test_clear(self) -> None:
        """Clear should remove all items."""
        bf = BloomFilter(expected_items=1000, false_positive_rate=0.01)
        bf.add("token123")
        assert bf.might_contain("token123") is True

        bf.clear()
        assert bf.might_contain("token123") is False
        assert bf.item_count == 0


class TestBloomFilterStatistics:
    """Tests for Bloom filter statistics."""

    def test_item_count_increments(self) -> None:
        """Item count should track added items."""
        bf = BloomFilter(expected_items=1000, false_positive_rate=0.01)
        assert bf.item_count == 0

        bf.add("token1")
        assert bf.item_count == 1

        bf.add("token2")
        assert bf.item_count == 2

    def test_batch_add_updates_count(self) -> None:
        """Batch add should update item count correctly."""
        bf = BloomFilter(expected_items=1000, false_positive_rate=0.01)
        bf.add_batch(["a", "b", "c"])
        assert bf.item_count == 3

    def test_size_bytes(self) -> None:
        """Size in bytes should be positive."""
        bf = BloomFilter(expected_items=1000, false_positive_rate=0.01)
        assert bf.size_bytes > 0

    def test_fill_ratio_starts_at_zero(self) -> None:
        """Empty filter should have 0 fill ratio."""
        bf = BloomFilter(expected_items=1000, false_positive_rate=0.01)
        assert bf.fill_ratio == 0.0

    def test_fill_ratio_increases(self) -> None:
        """Fill ratio should increase as items are added."""
        bf = BloomFilter(expected_items=100, false_positive_rate=0.01)
        initial_ratio = bf.fill_ratio

        for i in range(50):
            bf.add(f"token_{i}")

        assert bf.fill_ratio > initial_ratio

    def test_stats_returns_dict(self) -> None:
        """Stats should return a dictionary with expected keys."""
        bf = BloomFilter(expected_items=1000, false_positive_rate=0.01)
        bf.add("test")
        stats = bf.stats()

        assert "size_bits" in stats
        assert "size_bytes" in stats
        assert "hash_count" in stats
        assert "item_count" in stats
        assert "fill_ratio" in stats
        assert "estimated_fpr" in stats


class TestBloomFilterFalsePositiveRate:
    """Tests for false positive rate behavior."""

    def test_no_false_negatives(self) -> None:
        """Bloom filter should never have false negatives."""
        bf = BloomFilter(expected_items=1000, false_positive_rate=0.01)
        tokens = [f"token_{i}" for i in range(100)]

        for token in tokens:
            bf.add(token)

        # All added tokens MUST be found
        for token in tokens:
            assert bf.might_contain(token) is True, f"False negative for {token}"

    def test_false_positive_rate_below_threshold(self) -> None:
        """False positive rate should be close to configured rate."""
        bf = BloomFilter(expected_items=10000, false_positive_rate=0.01)

        # Add 10000 items
        for i in range(10000):
            bf.add(f"token_{i}")

        # Check 10000 items that were NOT added
        false_positives = 0
        for i in range(10000, 20000):
            if bf.might_contain(f"token_{i}"):
                false_positives += 1

        # Allow some margin (2x the target rate)
        actual_rate = false_positives / 10000
        assert actual_rate < 0.03, f"False positive rate {actual_rate} too high"


class TestBloomFilterOptimalParameters:
    """Tests for optimal parameter calculation."""

    def test_larger_expected_items_larger_size(self) -> None:
        """More expected items should result in larger filter."""
        bf_small = BloomFilter(expected_items=100, false_positive_rate=0.01)
        bf_large = BloomFilter(expected_items=10000, false_positive_rate=0.01)
        assert bf_large.size_bytes > bf_small.size_bytes

    def test_lower_fpr_larger_size(self) -> None:
        """Lower false positive rate should result in larger filter."""
        bf_high_fpr = BloomFilter(expected_items=1000, false_positive_rate=0.1)
        bf_low_fpr = BloomFilter(expected_items=1000, false_positive_rate=0.001)
        assert bf_low_fpr.size_bytes > bf_high_fpr.size_bytes


class TestBloomFilterThreadSafety:
    """Tests for thread safety."""

    def test_concurrent_adds(self) -> None:
        """Concurrent adds should not corrupt the filter."""
        import concurrent.futures

        bf = BloomFilter(expected_items=10000, false_positive_rate=0.01)

        def add_tokens(start: int, count: int) -> None:
            for i in range(start, start + count):
                bf.add(f"token_{i}")

        # Add tokens from multiple threads
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(add_tokens, i * 1000, 1000) for i in range(4)]
            concurrent.futures.wait(futures)

        # All tokens should be findable
        for i in range(4000):
            assert bf.might_contain(f"token_{i}") is True


class TestGlobalBloomFilter:
    """Tests for the global blacklist_bloom_filter singleton."""

    def test_singleton_exists(self) -> None:
        """Global singleton should be initialized."""
        assert blacklist_bloom_filter is not None

    def test_reset_creates_new_filter(self) -> None:
        """reset_bloom_filter should create a new filter."""
        # Add something to current filter
        blacklist_bloom_filter.add("test_token_singleton")

        # Reset
        reset_bloom_filter()

        # Re-import to get new instance
        from src.app.core.security.bloom_filter import blacklist_bloom_filter as new_bf

        # Should be empty
        assert new_bf.item_count == 0


class TestBloomFilterEdgeCases:
    """Tests for edge cases."""

    def test_empty_string(self) -> None:
        """Empty string should be handled."""
        bf = BloomFilter(expected_items=1000, false_positive_rate=0.01)
        bf.add("")
        assert bf.might_contain("") is True

    def test_long_token(self) -> None:
        """Long tokens should work."""
        bf = BloomFilter(expected_items=1000, false_positive_rate=0.01)
        long_token = "a" * 10000
        bf.add(long_token)
        assert bf.might_contain(long_token) is True

    def test_unicode_tokens(self) -> None:
        """Unicode tokens should work."""
        bf = BloomFilter(expected_items=1000, false_positive_rate=0.01)
        unicode_token = "token_🔐_认证_🎉"
        bf.add(unicode_token)
        assert bf.might_contain(unicode_token) is True

    def test_similar_tokens(self) -> None:
        """Similar tokens should be distinguished."""
        bf = BloomFilter(expected_items=1000, false_positive_rate=0.01)
        bf.add("token_1")
        # Similar but different token
        assert bf.might_contain("token_2") is False  # High probability
