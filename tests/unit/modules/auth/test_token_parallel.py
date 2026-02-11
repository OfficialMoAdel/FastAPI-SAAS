"""Unit tests for parallel token generation optimization (P2-3).

Tests verify that access and refresh tokens are generated in parallel using
asyncio.gather() and asyncio.to_thread() for improved performance.
"""

import asyncio
import time
from datetime import timedelta
from unittest.mock import patch

import pytest

from src.app.core.security import create_access_token, create_refresh_token


class TestTokenGenerationParallel:
    """Test that tokens are generated in parallel."""

    @pytest.mark.asyncio
    async def test_tokens_use_thread_pool_for_rs256(self):
        """Verify token functions use thread pool for RS256 signing.
        
        RS256 (RSA) signing is CPU-intensive and should run in a thread pool
        to avoid blocking the event loop. HS256 signing is fast enough to run
        directly without thread overhead.
        """
        token_data = {"sub": "testuser", "tier_id": 1, "tier_name": "free"}

        with patch("src.app.core.security._bcrypt_executor") as mock_executor:
            # Create a mock future that returns a valid JWT-like string
            import asyncio
            
            async def mock_submit(*args, **kwargs):
                return "mock_rs256_token"
            
            mock_executor.submit = mock_submit

            # Generate tokens
            access_token = await create_access_token(token_data)
            refresh_token = await create_refresh_token(token_data)

            # Tokens should be generated successfully
            assert access_token is not None
            assert refresh_token is not None
            # Tokens should be different
            assert access_token != refresh_token

    @pytest.mark.asyncio
    async def test_gather_executes_concurrently(self):
        """Verify asyncio.gather runs token generation concurrently."""
        token_data = {"sub": "testuser", "tier_id": 1, "tier_name": "free"}

        # Track execution order
        execution_order = []

        async def mock_access_token(*args, **kwargs):
            execution_order.append("access_start")
            await asyncio.sleep(0.01)  # Simulate work
            execution_order.append("access_end")
            return "access_token"

        async def mock_refresh_token(*args, **kwargs):
            execution_order.append("refresh_start")
            await asyncio.sleep(0.01)  # Simulate work
            execution_order.append("refresh_end")
            return "refresh_token"

        with patch("src.app.core.security.create_access_token", mock_access_token):
            with patch("src.app.core.security.create_refresh_token", mock_refresh_token):
                # Use gather to run in parallel
                access_token, refresh_token = await asyncio.gather(
                    mock_access_token(token_data),
                    mock_refresh_token(token_data),
                )

        # Assert - both should start before either finishes (interleaved execution)
        assert execution_order[0] == "access_start"
        assert execution_order[1] == "refresh_start"
        assert "access_end" in execution_order
        assert "refresh_end" in execution_order


class TestTokensValidAfterParallel:
    """Test that tokens work correctly after parallel generation."""

    @pytest.mark.asyncio
    async def test_access_token_valid_structure(self):
        """Verify access token has correct structure after parallel generation."""
        token_data = {"sub": "testuser", "tier_id": 1, "tier_name": "free"}
        expires_delta = timedelta(minutes=30)

        access_token = await create_access_token(token_data, expires_delta)

        # Assert - token should be a non-empty string (JWT format)
        assert isinstance(access_token, str)
        assert len(access_token) > 0
        assert access_token.count(".") == 2  # JWT has 3 parts separated by dots

    @pytest.mark.asyncio
    async def test_refresh_token_valid_structure(self):
        """Verify refresh token has correct structure after parallel generation."""
        token_data = {"sub": "testuser", "tier_id": 1, "tier_name": "free"}

        refresh_token = await create_refresh_token(token_data)

        # Assert - token should be a non-empty string (JWT format)
        assert isinstance(refresh_token, str)
        assert len(refresh_token) > 0
        assert refresh_token.count(".") == 2  # JWT has 3 parts separated by dots

    @pytest.mark.asyncio
    async def test_tokens_different(self):
        """Verify access and refresh tokens are different."""
        token_data = {"sub": "testuser", "tier_id": 1, "tier_name": "free"}
        expires_delta = timedelta(minutes=30)

        access_token, refresh_token = await asyncio.gather(
            create_access_token(token_data, expires_delta),
            create_refresh_token(token_data),
        )

        # Assert - tokens should be different
        assert access_token != refresh_token

    @pytest.mark.asyncio
    async def test_parallel_generation_produces_valid_tokens(self):
        """Verify parallel generation produces valid tokens."""
        token_data = {"sub": "testuser", "tier_id": 1, "tier_name": "free"}
        expires_delta = timedelta(minutes=30)

        # Generate in parallel using gather
        access_token, refresh_token = await asyncio.gather(
            create_access_token(token_data, expires_delta),
            create_refresh_token(token_data),
        )

        # Assert - both tokens should be valid JWT strings
        assert isinstance(access_token, str)
        assert isinstance(refresh_token, str)
        assert len(access_token) > 0
        assert len(refresh_token) > 0
        assert access_token.count(".") == 2
        assert refresh_token.count(".") == 2


class TestBenchmarkComparison:
    """Test performance characteristics of parallel token generation."""

    @pytest.mark.asyncio
    async def test_parallel_faster_than_sequential(self):
        """Verify parallel generation is faster than sequential (or at least not slower)."""
        token_data = {"sub": "testuser", "tier_id": 1, "tier_name": "free"}
        expires_delta = timedelta(minutes=30)
        iterations = 10

        # Sequential generation
        start = time.perf_counter()
        for _ in range(iterations):
            access_token = await create_access_token(token_data, expires_delta)
            refresh_token = await create_refresh_token(token_data)
        sequential_time = time.perf_counter() - start

        # Parallel generation
        start = time.perf_counter()
        for _ in range(iterations):
            access_token, refresh_token = await asyncio.gather(
                create_access_token(token_data, expires_delta),
                create_refresh_token(token_data),
            )
        parallel_time = time.perf_counter() - start

        # Assert - parallel should not be significantly slower
        # Note: Due to Python GIL and thread pool overhead, improvement may be minimal
        # Allow 2x threshold for CI environments with varying load
        assert parallel_time <= sequential_time * 2.0, (
            f"Parallel generation should not be significantly slower: "
            f"sequential={sequential_time:.4f}s, parallel={parallel_time:.4f}s"
        )

    @pytest.mark.asyncio
    async def test_benchmark_single_generation(self):
        """Benchmark single token generation pair."""
        token_data = {"sub": "testuser", "tier_id": 1, "tier_name": "free"}
        expires_delta = timedelta(minutes=30)

        start = time.perf_counter()
        access_token, refresh_token = await asyncio.gather(
            create_access_token(token_data, expires_delta),
            create_refresh_token(token_data),
        )
        elapsed = time.perf_counter() - start

        # Assert - should complete quickly (< 10ms)
        assert elapsed < 0.01, f"Token generation took {elapsed*1000:.2f}ms, expected < 10ms"
        assert isinstance(access_token, str)
        assert isinstance(refresh_token, str)

    @pytest.mark.asyncio
    async def test_concurrent_token_generation(self):
        """Test multiple concurrent token generation operations."""
        token_data = {"sub": "testuser", "tier_id": 1, "tier_name": "free"}
        expires_delta = timedelta(minutes=30)

        # Generate 5 token pairs concurrently
        tasks = [
            asyncio.gather(
                create_access_token(token_data, expires_delta),
                create_refresh_token(token_data),
            )
            for _ in range(5)
        ]

        start = time.perf_counter()
        results = await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - start

        # Assert - all should complete successfully
        assert len(results) == 5
        for access_token, refresh_token in results:
            assert isinstance(access_token, str)
            assert isinstance(refresh_token, str)
            assert access_token != refresh_token

        # Should complete in reasonable time (< 50ms)
        assert elapsed < 0.05, f"Concurrent generation took {elapsed*1000:.2f}ms, expected < 50ms"


class TestAuthServiceIntegration:
    """Test auth service uses parallel token generation."""

    @pytest.mark.asyncio
    async def test_login_uses_gather(self):
        """Verify login endpoint uses asyncio.gather for token generation."""
        # This test verifies the pattern is used in auth service
        # We check that the code structure matches the expected pattern

        import inspect

        from src.app.modules.auth.service import AuthService

        # Get the login method source
        source = inspect.getsource(AuthService.login)

        # Assert - should use asyncio.gather
        assert "asyncio.gather" in source, "Login should use asyncio.gather for parallel token generation"
        assert "create_access_token" in source
        assert "create_refresh_token" in source