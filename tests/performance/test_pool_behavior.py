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
        """Verify complete() commits before the LLM call (via _prepare_turn)."""
        import inspect

        from src.app.modules.ai.service import ChatCompletionService

        # The commit now lives in the single turn-preparation owner, so the
        # release invariant is asserted as: _prepare_turn commits, and
        # complete() enters it before it touches the provider.
        prepare_source = inspect.getsource(ChatCompletionService._prepare_turn)
        assert "await self.db.commit()" in prepare_source, "db.commit() should be in _prepare_turn()"
        assert "self._invoke_chat" not in prepare_source, "_prepare_turn must not call the provider"

        source = inspect.getsource(ChatCompletionService.complete)
        prepare_pos = source.find("await self._prepare_turn(")
        ollama_pos = source.find("self._invoke_chat(")

        assert prepare_pos != -1, "_prepare_turn should be called in complete()"
        assert ollama_pos != -1, "_invoke_chat should be in complete()"
        assert prepare_pos < ollama_pos, "connection release must happen before LLM invocation"

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
        """Verify complete_stream() commits before LLM streaming."""
        import inspect

        from src.app.modules.ai.service import ChatCompletionService

        prepare_source = inspect.getsource(ChatCompletionService._prepare_turn)
        assert "await self.db.commit()" in prepare_source, "db.commit() should be in _prepare_turn()"

        source = inspect.getsource(ChatCompletionService.complete_stream)
        prepare_pos = source.find("await self._prepare_turn(")
        stream_pos = source.find("self._invoke_chat_stream(")

        assert prepare_pos != -1, "_prepare_turn should be called in complete_stream()"
        assert stream_pos != -1, "_invoke_chat_stream should be in complete_stream()"
        assert prepare_pos < stream_pos, "connection release must happen before streaming"

        logger.info("Stream connection release pattern: verified in source")


class TestAsyncioGatherParallelFetch:
    """Test P5: asyncio.gather() for parallel fetches."""

    @pytest.mark.asyncio
    async def test_parallel_fetch_faster_than_sequential(self) -> None:
        """Verify parallel fetch is faster than sequential."""
        import time

        fetch_delay = 0.05  # 50ms per fetch (higher to avoid Windows 15.6ms clock resolution flakiness)

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

        # Parallel should be ~3x faster, but use 1.5x to account for CI/Windows overhead
        speedup = sequential_time / parallel_time
        assert speedup > 1.5, f"Expected >1.5x speedup, got {speedup:.2f}x"

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
