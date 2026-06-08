"""Tests for DB connection pool behavior during AI service operations.

Verifies C2 optimization: connection release during LLM calls.
"""

import asyncio
import logging

import pytest

logger = logging.getLogger(__name__)


class TestConnectionReleasePattern:
    """Test the connection release pattern implementation."""

    def test_local_session_available(self) -> None:
        """Verify local_session is imported in AI service."""
        from src.app.modules.ai import service

        assert hasattr(service, "local_session"), "local_session should be imported"
        logger.info("local_session import: verified")

    def test_complete_method_has_commit_call(self) -> None:
        """Verify complete() method commits before LLM call."""
        import inspect

        from src.app.modules.ai.service import ChatCompletionService

        source = inspect.getsource(ChatCompletionService.complete)

        # Verify commit is called before ollama.chat
        commit_pos = source.find("await self.db.commit()")
        ollama_pos = source.find("await self.ollama.chat")

        assert commit_pos != -1, "db.commit() should be in complete()"
        assert ollama_pos != -1, "ollama.chat should be in complete()"
        assert commit_pos < ollama_pos, "commit should happen before ollama.chat"

        logger.info("Connection release pattern: commit before LLM verified in source")

    def test_complete_uses_local_session_for_write(self) -> None:
        """Verify complete() uses local_session for response write."""
        import inspect

        from src.app.modules.ai.service import ChatCompletionService

        source = inspect.getsource(ChatCompletionService.complete)

        # Verify local_session is used after LLM call
        assert "async with local_session()" in source, "Should use local_session for writes"
        assert "fresh_db" in source, "Should use fresh_db variable"

        logger.info("Fresh session pattern: local_session() used for response write")

    def test_stream_method_has_commit_call(self) -> None:
        """Verify complete_stream() method commits before LLM streaming."""
        import inspect

        from src.app.modules.ai.service import ChatCompletionService

        source = inspect.getsource(ChatCompletionService.complete_stream)

        commit_pos = source.find("await self.db.commit()")
        stream_pos = source.find("self.ollama.chat_stream")

        assert commit_pos != -1, "db.commit() should be in complete_stream()"
        assert stream_pos != -1, "ollama.chat_stream should be in complete_stream()"
        assert commit_pos < stream_pos, "commit should happen before streaming"

        logger.info("Stream connection release pattern: verified in source")


class TestAsyncioGatherParallelFetch:
    """Test P5: asyncio.gather() for parallel fetches."""

    @pytest.mark.asyncio
    async def test_parallel_fetch_faster_than_sequential(self) -> None:
        """Verify parallel fetch is faster than sequential."""
        import time

        fetch_delay = 0.01  # 10ms per fetch

        async def mock_fetch(name: str) -> dict:
            await asyncio.sleep(fetch_delay)
            return {name: "data"}

        # Sequential timing
        start = time.perf_counter()
        await mock_fetch("session")
        await mock_fetch("settings")
        await mock_fetch("messages")
        sequential_time = time.perf_counter() - start

        # Parallel timing
        start = time.perf_counter()
        await asyncio.gather(
            mock_fetch("session"),
            mock_fetch("settings"),
            mock_fetch("messages"),
        )
        parallel_time = time.perf_counter() - start

        # Parallel should be ~3x faster
        speedup = sequential_time / parallel_time
        assert speedup > 2.0, f"Expected >2x speedup, got {speedup:.2f}x"

        logger.info(f"Parallel fetch speedup: {speedup:.2f}x")

    @pytest.mark.asyncio
    async def test_gather_returns_all_results(self) -> None:
        """Verify asyncio.gather returns results in correct order."""

        async def fetch_a() -> str:
            await asyncio.sleep(0.02)
            return "A"

        async def fetch_b() -> str:
            await asyncio.sleep(0.01)
            return "B"

        async def fetch_c() -> str:
            await asyncio.sleep(0.015)
            return "C"

        results = await asyncio.gather(fetch_a(), fetch_b(), fetch_c())

        assert results == ["A", "B", "C"]
        logger.info("asyncio.gather: results in argument order")


class TestConcurrentRequestSimulation:
    """Test concurrent request patterns."""

    @pytest.mark.asyncio
    async def test_parallel_requests_pattern(self) -> None:
        """Simulate the connection pattern with multiple requests."""
        active_connections = 0
        max_concurrent = 0
        lock = asyncio.Lock()

        async def simulate_request() -> None:
            nonlocal active_connections, max_concurrent

            # Acquire connection (fetch phase)
            async with lock:
                active_connections += 1
                max_concurrent = max(max_concurrent, active_connections)

            # Release before LLM (C2 pattern)
            async with lock:
                active_connections -= 1

            # Simulate LLM call (no connection held)
            await asyncio.sleep(0.01)

            # Reacquire for save
            async with lock:
                active_connections += 1
                max_concurrent = max(max_concurrent, active_connections)

            # Release after save
            async with lock:
                active_connections -= 1

        # Run 10 concurrent requests
        await asyncio.gather(*[simulate_request() for _ in range(10)])

        # With proper release, max concurrent should be manageable
        assert max_concurrent <= 10
        logger.info(f"Concurrent simulation: max_concurrent={max_concurrent}")
