"""Unit tests for Bloom Filter bitarray optimization (z28g.6).

Tests the bitarray C extension used for bit manipulation in BloomFilter.
"""

import logging
import timeit

import pytest
from bitarray import bitarray

from src.app.core.security.bloom_filter import BloomFilter

logger = logging.getLogger(__name__)


class TestBitarrayInitialization:
    """Test bitarray initializes correctly."""

    def test_bitarray_basic_init(self):
        """Verify bitarray initializes with correct size."""
        bits = bitarray(1000000)
        bits.setall(0)

        logger.info(f"Created bitarray of size {len(bits)}")
        assert len(bits) == 1000000
        assert bits.count() == 0

    def test_bitarray_setall_zero(self):
        """Verify setall(0) clears all bits."""
        bits = bitarray(100)
        bits.setall(1)
        assert bits.count() == 100

        bits.setall(0)
        assert bits.count() == 0


class TestBitarrayGetSetBit:
    """Test get/set operations on bitarray."""

    def test_get_set_single_bit(self):
        """Verify get/set operations work correctly."""
        bits = bitarray(100)
        bits.setall(0)

        bits[42] = True

        assert bits[42] == True
        assert bits[0] == False
        assert bits[99] == False

    def test_set_multiple_bits(self):
        """Verify setting multiple bits works."""
        bits = bitarray(100)
        bits.setall(0)

        bits[10] = True
        bits[20] = True
        bits[30] = True

        assert bits.count() == 3
        assert bits[10] == True
        assert bits[20] == True
        assert bits[30] == True
        assert bits[15] == False


class TestBitarrayBufferInfo:
    """Test buffer_info() for size calculation."""

    def test_buffer_info_returns_bytes(self):
        """Verify buffer_info() works for size_bytes calculation."""
        bits = bitarray(800000)  # 100KB = 800000 bits

        info = bits.buffer_info()
        size_bytes = info[1]

        logger.info(f"buffer_info: {info}, size_bytes: {size_bytes}")
        assert size_bytes == 100000  # 100KB

    def test_buffer_info_small(self):
        """Verify buffer_info for small arrays."""
        bits = bitarray(8)  # 1 byte
        info = bits.buffer_info()

        logger.info(f"Small bitarray buffer_info: {info}")
        assert info[1] >= 1  # At least 1 byte


class TestBitarrayCount:
    """Test count() for fill_ratio calculation."""

    def test_count_returns_set_bits(self):
        """Verify count() returns number of set bits."""
        bits = bitarray(100)
        bits.setall(0)

        bits[10] = True
        bits[20] = True
        bits[30] = True

        assert bits.count() == 3

    def test_count_for_fill_ratio(self):
        """Verify fill ratio calculation with count()."""
        bits = bitarray(100)
        bits.setall(0)

        bits[10] = True
        bits[20] = True
        bits[30] = True

        fill_ratio = bits.count() / len(bits)
        logger.info(f"Fill ratio: {fill_ratio}")
        assert fill_ratio == 0.03  # 3%


class TestBloomFilterWithBitarray:
    """Test BloomFilter uses bitarray correctly."""

    def test_bloom_filter_uses_bitarray(self):
        """Verify BloomFilter._bit_array is a bitarray."""
        bf = BloomFilter(expected_items=1000, false_positive_rate=0.01)

        logger.info(f"BloomFilter._bit_array type: {type(bf._bit_array)}")
        assert isinstance(bf._bit_array, bitarray)

    def test_bloom_filter_size_bytes(self):
        """Verify size_bytes uses buffer_info()."""
        bf = BloomFilter(expected_items=10000, false_positive_rate=0.01)

        size = bf.size_bytes
        logger.info(f"BloomFilter size_bytes: {size}")
        assert size > 0
        assert isinstance(size, int)

    def test_bloom_filter_fill_ratio(self):
        """Verify fill_ratio uses count()."""
        bf = BloomFilter(expected_items=1000, false_positive_rate=0.01)

        # Add some items
        for i in range(100):
            bf.add(f"token_{i}")

        ratio = bf.fill_ratio
        logger.info(f"Fill ratio after 100 items: {ratio:.4f}")
        assert 0 < ratio < 1

    def test_bloom_filter_clear_uses_setall(self):
        """Verify clear() uses setall(0)."""
        bf = BloomFilter(expected_items=1000, false_positive_rate=0.01)

        # Add items
        for i in range(100):
            bf.add(f"token_{i}")
        assert bf.fill_ratio > 0

        # Clear
        bf.clear()

        assert bf.fill_ratio == 0
        assert bf._bit_array.count() == 0


class TestBitarrayPerformance:
    """Test that bitarray is faster than bytearray."""

    def test_bitarray_get_faster_than_bytearray(self):
        """Verify bitarray indexing is faster than bytearray bit manipulation."""
        # Setup bytearray
        ba = bytearray(125000)  # 1M bits

        # Setup bitarray
        bits = bitarray(1000000)
        bits.setall(0)

        iterations = 50000

        # Benchmark bytearray get (with bit manipulation)
        def bytearray_get():
            pos = 500000
            byte_idx = pos // 8
            bit_idx = pos % 8
            return bool(ba[byte_idx] & (1 << bit_idx))

        ba_time = timeit.timeit(bytearray_get, number=iterations)

        # Benchmark bitarray get (direct indexing)
        def bitarray_get():
            return bits[500000]

        bit_time = timeit.timeit(bitarray_get, number=iterations)

        ba_ns = (ba_time * 1e9) / iterations
        bit_ns = (bit_time * 1e9) / iterations

        logger.info(f"bytearray get: {ba_ns:.1f}ns, bitarray get: {bit_ns:.1f}ns")
        logger.info(f"Speedup: {ba_ns / bit_ns:.2f}x")

        # bitarray should be faster (or at least comparable)
        # Allow some margin for CI variance
        assert bit_time <= ba_time * 1.5, f"bitarray should be faster: {bit_time} vs {ba_time}"

    def test_bitarray_set_faster_than_bytearray(self):
        """Verify bitarray set is faster than bytearray bit manipulation."""
        # Setup bytearray
        ba = bytearray(125000)

        # Setup bitarray
        bits = bitarray(1000000)
        bits.setall(0)

        iterations = 50000

        # Benchmark bytearray set
        def bytearray_set():
            pos = 500000
            byte_idx = pos // 8
            bit_idx = pos % 8
            ba[byte_idx] |= 1 << bit_idx

        ba_time = timeit.timeit(bytearray_set, number=iterations)

        # Benchmark bitarray set
        def bitarray_set():
            bits[500000] = True

        bit_time = timeit.timeit(bitarray_set, number=iterations)

        ba_ns = (ba_time * 1e9) / iterations
        bit_ns = (bit_time * 1e9) / iterations

        logger.info(f"bytearray set: {ba_ns:.1f}ns, bitarray set: {bit_ns:.1f}ns")
        logger.info(f"Speedup: {ba_ns / bit_ns:.2f}x")

        # bitarray should be faster
        assert bit_time <= ba_time * 1.5, f"bitarray should be faster: {bit_time} vs {ba_time}"

    def test_bitarray_count_faster_than_popcount_loop(self):
        """Verify bitarray.count() is faster than manual popcount."""
        # Setup bytearray
        ba = bytearray(12500)  # 100K bits
        for i in range(0, 12500, 3):
            ba[i] = 0xFF  # Set some bytes

        # Setup bitarray with same pattern
        bits = bitarray(100000)
        bits.setall(0)
        for i in range(0, 100000, 24):
            for j in range(8):
                if i + j < 100000:
                    bits[i + j] = True

        iterations = 1000

        # Benchmark bytearray popcount (manual loop)
        def bytearray_count():
            return sum(bin(byte).count("1") for byte in ba)

        ba_time = timeit.timeit(bytearray_count, number=iterations)

        # Benchmark bitarray count (C extension)
        def bitarray_count():
            return bits.count()

        bit_time = timeit.timeit(bitarray_count, number=iterations)

        ba_us = (ba_time * 1e6) / iterations
        bit_us = (bit_time * 1e6) / iterations

        logger.info(f"bytearray popcount: {ba_us:.1f}μs, bitarray count: {bit_us:.1f}μs")
        logger.info(f"Speedup: {ba_us / bit_us:.1f}x")

        # bitarray.count() should be MUCH faster (C extension)
        assert bit_time < ba_time, f"bitarray.count() should be faster: {bit_time} vs {ba_time}"
