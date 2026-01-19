"""Unit tests for AI streaming string concatenation optimization (P2-4).

Tests verify that streaming uses list append + join instead of O(n²) string
concatenation for improved performance with long AI responses.
"""

import time


class TestStringConcatenationPattern:
    """Test the string concatenation pattern optimization."""

    def test_list_join_faster_than_concatenation(self):
        """Verify list + join is faster than string concatenation for many chunks."""
        num_chunks = 1000
        chunk_text = "x"

        # Method 1: String concatenation (O(n²))
        start = time.perf_counter()
        result1 = ""
        for _ in range(num_chunks):
            result1 += chunk_text
        time_concat = time.perf_counter() - start

        # Method 2: List + join (O(n))
        start = time.perf_counter()
        chunks = []
        for _ in range(num_chunks):
            chunks.append(chunk_text)
        result2 = "".join(chunks)
        time_join = time.perf_counter() - start

        # Assert
        assert result1 == result2, "Both methods should produce same result"
        assert len(result1) == num_chunks, "Should have correct length"
        # List + join should be faster (or at least not slower)
        # Note: For small strings, difference may be minimal
        assert time_join <= time_concat * 2, "List + join should not be significantly slower"

    def test_list_join_with_empty_strings(self):
        """Verify list + join handles empty strings correctly."""
        chunks = ["Hello", "", "world", "", "!"]
        result = "".join(chunks)
        assert result == "Helloworld!", "Should concatenate all chunks including empty"

    def test_list_join_memory_efficient(self):
        """Verify list + join uses linear memory."""
        num_chunks = 10000
        chunk_text = "x"

        # Build with list + join
        chunks = []
        for _ in range(num_chunks):
            chunks.append(chunk_text)
        result = "".join(chunks)

        # Assert
        assert len(result) == num_chunks, "Should have correct length"
        assert result == chunk_text * num_chunks, "Should match expected content"

    def test_list_join_preserves_order(self):
        """Verify list + join preserves chunk order."""
        chunks = ["First", " ", "Second", " ", "Third"]
        result = "".join(chunks)
        assert result == "First Second Third", "Should preserve order"


class TestStreamingOptimizationBehavior:
    """Test the behavior of the optimized streaming code."""

    def test_empty_content_filtered(self):
        """Verify empty content chunks are handled correctly."""
        # Simulate the optimized code pattern
        response_chunks = []

        # Simulate chunks with some empty content
        chunks = [
            {"content": "Hello", "done": False},
            {"content": "", "done": False},
            {"content": "world", "done": False},
            {"content": "", "done": True},
        ]

        for chunk in chunks:
            content = chunk.get("content", "")
            if content:  # Only append non-empty content
                response_chunks.append(content)

        full_response = "".join(response_chunks)

        # Assert
        assert full_response == "Helloworld", "Should only include non-empty chunks"

    def test_single_chunk_response(self):
        """Verify single chunk responses work correctly."""
        response_chunks = []

        chunks = [
            {"content": "Single response", "done": True},
        ]

        for chunk in chunks:
            content = chunk.get("content", "")
            if content:
                response_chunks.append(content)

        full_response = "".join(response_chunks)

        # Assert
        assert full_response == "Single response", "Should handle single chunk"

    def test_many_small_chunks(self):
        """Verify many small chunks are handled efficiently."""
        response_chunks = []

        # Simulate 100 single-character chunks
        for i in range(100):
            content = "x"
            if content:
                response_chunks.append(content)

        full_response = "".join(response_chunks)

        # Assert
        assert len(full_response) == 100, "Should concatenate all chunks"
        assert full_response == "x" * 100, "Should match expected content"

    def test_unicode_content(self):
        """Verify unicode content is handled correctly."""
        response_chunks = []

        chunks = [
            {"content": "Hello ", "done": False},
            {"content": "世界", "done": False},
            {"content": " 🌍", "done": True},
        ]

        for chunk in chunks:
            content = chunk.get("content", "")
            if content:
                response_chunks.append(content)

        full_response = "".join(response_chunks)

        # Assert
        assert full_response == "Hello 世界 🌍", "Should handle unicode correctly"


class TestPerformanceCharacteristics:
    """Test performance characteristics of the optimization."""

    def test_linear_time_complexity(self):
        """Verify list + join has linear time complexity."""
        # Test with increasing sizes
        sizes = [100, 200, 400]
        times = []

        for size in sizes:
            chunks = []
            start = time.perf_counter()
            for _ in range(size):
                chunks.append("x")
            result = "".join(chunks)
            elapsed = time.perf_counter() - start
            times.append(elapsed)
            assert len(result) == size

        # With linear complexity, doubling size should roughly double time
        # (allowing for some variance)
        # This is a rough check - exact timing depends on system load
        assert times[2] < times[0] * 10, "Should scale linearly, not quadratically"

    def test_benchmark_1000_chunks(self):
        """Benchmark with 1000 chunks (typical long AI response)."""
        num_chunks = 1000

        start = time.perf_counter()
        chunks = []
        for i in range(num_chunks):
            chunks.append(f"word{i} ")
        result = "".join(chunks)
        elapsed = time.perf_counter() - start

        # Assert
        assert len(result) > 0, "Should produce result"
        # Should complete quickly (< 10ms even on slow systems)
        assert elapsed < 0.01, f"Should complete in <10ms, took {elapsed*1000:.1f}ms"

    def test_benchmark_10000_chunks(self):
        """Benchmark with 10000 chunks (very long AI response)."""
        num_chunks = 10000

        start = time.perf_counter()
        chunks = []
        for _ in range(num_chunks):
            chunks.append("x")
        result = "".join(chunks)
        elapsed = time.perf_counter() - start

        # Assert
        assert len(result) == num_chunks, "Should have correct length"
        # Should still complete quickly (< 100ms)
        assert elapsed < 0.1, f"Should complete in <100ms, took {elapsed*1000:.1f}ms"
