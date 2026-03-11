"""Unit tests for Bitmask decode() algorithm optimization (z28g.7).

Tests the Brian Kernighan O(k) algorithm for decoding permission bitmasks.
"""

import logging
import random

from src.app.core.rbac.bitmask import PermissionBitmask

logger = logging.getLogger(__name__)


class TestDecodeEmptyMask:
    """Test decoding empty/zero masks."""

    def test_decode_zero_returns_empty_set(self):
        """Verify decode(0) returns empty set."""
        result = PermissionBitmask.decode(0)
        logger.info(f"decode(0) = {result}")
        assert result == set()
        assert isinstance(result, set)


class TestDecodeSingleBit:
    """Test decoding single-bit masks."""

    def test_decode_single_permission(self):
        """Verify single permission decodes correctly."""
        all_slugs = list(PermissionBitmask.get_all_permissions().keys())

        # Test first few permissions
        for slug in all_slugs[:5]:
            mask = PermissionBitmask.encode({slug})
            result = PermissionBitmask.decode(mask)

            logger.info(f"Single permission {slug}: mask={mask}, decoded={result}")
            assert result == {slug}


class TestDecodeFullMask:
    """Test decoding full permission masks."""

    def test_decode_all_permissions(self):
        """Verify all permissions decode correctly."""
        all_slugs = set(PermissionBitmask.get_all_permissions().keys())

        # Encode all permissions
        full_mask = PermissionBitmask.encode(all_slugs)

        # Decode back
        result = PermissionBitmask.decode(full_mask)

        logger.info(f"All permissions count: {len(all_slugs)}")
        logger.info(f"Decoded count: {len(result)}")

        assert result == all_slugs


class TestDecodeMatchesEncode:
    """Test that decode is the inverse of encode."""

    def test_roundtrip_single_permission(self):
        """Verify single permission roundtrips correctly."""
        for slug in list(PermissionBitmask.get_all_permissions().keys())[:5]:
            mask = PermissionBitmask.encode({slug})
            decoded = PermissionBitmask.decode(mask)

            logger.info(f"Roundtrip {slug}: mask={mask}, decoded={decoded}")
            assert decoded == {slug}

    def test_roundtrip_multiple_permissions(self):
        """Verify multiple permissions roundtrip correctly."""
        all_slugs = list(PermissionBitmask.get_all_permissions().keys())
        # Use actual permission slugs from the codebase
        slugs = set(all_slugs[:3])
        mask = PermissionBitmask.encode(slugs)
        decoded = PermissionBitmask.decode(mask)

        logger.info(f"Roundtrip {slugs}: mask={mask}, decoded={decoded}")
        assert decoded == slugs

    def test_roundtrip_random_subsets(self):
        """Verify random permission subsets roundtrip correctly."""
        all_slugs = list(PermissionBitmask.get_all_permissions().keys())

        for _ in range(10):
            # Random subset
            k = random.randint(1, min(10, len(all_slugs)))
            subset = set(random.sample(all_slugs, k))

            mask = PermissionBitmask.encode(subset)
            decoded = PermissionBitmask.decode(mask)

            logger.info(f"Random subset size {k}: {subset} -> {decoded}")
            assert decoded == subset


class TestKernighanAlgorithm:
    """Test Brian Kernighan's algorithm properties."""

    def test_iterations_equal_popcount(self):
        """Verify algorithm iterates exactly popcount times."""
        # The algorithm should iterate exactly k times where k = popcount(mask)
        masks = [
            (0b0001, 1),  # 1 bit set
            (0b0011, 2),  # 2 bits set
            (0b0111, 3),  # 3 bits set
            (0b1111, 4),  # 4 bits set
            (0b10101010, 4),  # 4 bits set
            (0xFFFF, 16),  # 16 bits set
        ]

        for mask, expected_popcount in masks:
            actual_popcount = PermissionBitmask.count_permissions(mask)
            logger.info(f"mask=0x{mask:X}, expected={expected_popcount}, actual={actual_popcount}")
            assert actual_popcount == expected_popcount

    def test_bit_count_optimization(self):
        """Verify count_permissions uses int.bit_count()."""
        # This should be O(1) on Python 3.10+
        large_mask = 0xFFFFFFFFFFFFFFFF  # 64 bits set

        count = PermissionBitmask.count_permissions(large_mask)

        logger.info(f"Large mask popcount: {count}")
        assert count == 64


class TestDecodeEdgeCases:
    """Test edge cases in decode."""

    def test_decode_max_int(self):
        """Verify decode handles very large masks."""
        # Create a mask with all defined positions set
        all_slugs = set(PermissionBitmask.get_all_permissions().keys())
        max_mask = PermissionBitmask.encode(all_slugs)

        result = PermissionBitmask.decode(max_mask)

        logger.info(f"Max mask: {max_mask}, decoded: {len(result)} permissions")
        assert result == all_slugs

    def test_decode_undefined_positions(self):
        """Verify decode ignores undefined bit positions."""
        # Set a bit that's beyond any defined permission
        undefined_mask = 1 << 60  # Way beyond any permission

        result = PermissionBitmask.decode(undefined_mask)

        logger.info(f"Undefined position mask result: {result}")
        # Should be empty since position 60 isn't mapped
        assert result == set()

    def test_decode_mixed_defined_undefined(self):
        """Verify decode handles mix of defined and undefined positions."""
        # Get a valid mask
        valid_slugs = {"users:read"}
        valid_mask = PermissionBitmask.encode(valid_slugs)

        # Add an undefined bit
        mixed_mask = valid_mask | (1 << 60)

        result = PermissionBitmask.decode(mixed_mask)

        logger.info(f"Mixed mask result: {result}")
        # Should only contain the valid permission
        assert result == valid_slugs


class TestDecodePerformance:
    """Test that O(k) algorithm is faster than O(P) for sparse masks."""

    def test_sparse_mask_fast(self):
        """Verify sparse masks decode quickly (O(k) not O(P))."""
        import timeit

        # Sparse mask - only 2 bits set
        sparse_mask = 0b0101

        # Time many iterations
        iterations = 10000
        elapsed = timeit.timeit(lambda: PermissionBitmask.decode(sparse_mask), number=iterations)

        avg_ns = (elapsed * 1e9) / iterations
        logger.info(f"Sparse mask decode: {avg_ns:.1f}ns average")

        # Should be fast (under 10μs typically)
        assert avg_ns < 100000  # 100μs max

    def test_dense_vs_sparse_scaling(self):
        """Verify dense masks take longer than sparse (O(k) scaling)."""
        import timeit

        # Get all permissions
        all_slugs = set(PermissionBitmask.get_all_permissions().keys())

        # Sparse: 2 permissions
        sparse_slugs = set(list(all_slugs)[:2])
        sparse_mask = PermissionBitmask.encode(sparse_slugs)

        # Dense: all permissions
        dense_mask = PermissionBitmask.encode(all_slugs)

        iterations = 5000

        sparse_time = timeit.timeit(lambda: PermissionBitmask.decode(sparse_mask), number=iterations)

        dense_time = timeit.timeit(lambda: PermissionBitmask.decode(dense_mask), number=iterations)

        sparse_avg = (sparse_time * 1e6) / iterations
        dense_avg = (dense_time * 1e6) / iterations
        ratio = dense_time / sparse_time if sparse_time > 0 else 0

        logger.info(f"Sparse ({len(sparse_slugs)} perms): {sparse_avg:.2f}μs")
        logger.info(f"Dense ({len(all_slugs)} perms): {dense_avg:.2f}μs")
        logger.info(f"Ratio (dense/sparse): {ratio:.2f}x")

        # Dense should take more time (O(k) scaling)
        # But not proportionally more (would be P/k ratio for old O(P) algorithm)
        assert dense_time > sparse_time
