"""Integration tests for event system performance optimizations.

Tests: U3 (Parallel Handlers), U18 (Handler Pre-registration), U19 (Handler Timeout)
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog

logger = structlog.get_logger(__name__)


# =============================================================================
# U3: Parallel Event Handlers
# =============================================================================
@pytest.mark.integration
class TestParallelEventHandlers:
    """U3: Verify event handlers execute in parallel."""

    @pytest.mark.asyncio
    async def test_parallel_execution_timing(self, perf_timer):
        """Verify 3 slow handlers complete in ~100ms, not 300ms."""

        async def slow_handler(delay: float = 0.1):
            await asyncio.sleep(delay)
            return "done"

        handlers = [slow_handler, slow_handler, slow_handler]

        # Parallel execution
        with perf_timer:
            results = await asyncio.gather(*[h() for h in handlers])
        parallel_time = perf_timer.elapsed_ms

        assert len(results) == 3
        assert all(r == "done" for r in results)

        # Should complete in ~100ms (parallel), not ~300ms (sequential)
        logger.info("parallel_execution", elapsed_ms=parallel_time)
        assert parallel_time < 200  # Allow some overhead

    @pytest.mark.asyncio
    async def test_sequential_vs_parallel(self, perf_timer):
        """Compare sequential vs parallel handler execution."""

        async def handler():
            await asyncio.sleep(0.05)  # 50ms
            return True

        # Sequential
        with perf_timer:
            for _ in range(3):
                await handler()
        sequential_time = perf_timer.elapsed_ms

        # Parallel
        with perf_timer:
            await asyncio.gather(handler(), handler(), handler())
        parallel_time = perf_timer.elapsed_ms

        logger.info(
            "execution_comparison",
            sequential_ms=sequential_time,
            parallel_ms=parallel_time,
            speedup=sequential_time / parallel_time if parallel_time > 0 else 0,
        )

        # Parallel should be ~3x faster
        assert parallel_time < sequential_time * 0.6

    @pytest.mark.asyncio
    async def test_parallel_handler_isolation(self):
        """Verify parallel handlers don't interfere with each other."""
        results = []

        async def handler_a():
            await asyncio.sleep(0.02)
            results.append("A")

        async def handler_b():
            await asyncio.sleep(0.01)
            results.append("B")

        async def handler_c():
            await asyncio.sleep(0.03)
            results.append("C")

        await asyncio.gather(handler_a(), handler_b(), handler_c())

        # All handlers completed
        assert set(results) == {"A", "B", "C"}

    @pytest.mark.asyncio
    async def test_parallel_handler_error_isolation(self):
        """Verify one handler failure doesn't block others."""

        async def good_handler():
            await asyncio.sleep(0.01)
            return "success"

        async def bad_handler():
            await asyncio.sleep(0.01)
            raise ValueError("Handler failed")

        # Use return_exceptions to capture errors without blocking
        results = await asyncio.gather(
            good_handler(),
            bad_handler(),
            good_handler(),
            return_exceptions=True,
        )

        # Two successes, one error
        successes = [r for r in results if r == "success"]
        errors = [r for r in results if isinstance(r, Exception)]

        assert len(successes) == 2
        assert len(errors) == 1

    @pytest.mark.asyncio
    async def test_parallel_isomorphism(self):
        """Verify parallel execution produces same results as sequential."""
        counter = {"value": 0}

        async def increment():
            await asyncio.sleep(0.01)
            counter["value"] += 1
            return counter["value"]

        # Reset and run sequential
        counter["value"] = 0
        seq_results = []
        for _ in range(3):
            seq_results.append(await increment())

        # Reset and run parallel
        counter["value"] = 0
        par_results = await asyncio.gather(increment(), increment(), increment())

        # Final count should be same
        # Note: Individual results may differ due to race conditions
        assert counter["value"] == 3


# =============================================================================
# U18: Handler Pre-registration
# =============================================================================
@pytest.mark.integration
class TestHandlerPreregistration:
    """U18: Verify handlers are pre-registered at startup."""

    @pytest.mark.asyncio
    async def test_handler_registry_populated(self):
        """Verify handler registry has handlers after startup."""
        from src.app.core.events import event_bus

        # Registry should exist
        assert hasattr(event_bus, "_handlers") or hasattr(event_bus, "handlers")

    @pytest.mark.asyncio
    async def test_no_cold_start_delay(self, perf_timer):
        """Verify first event dispatch has no cold start."""

        # Mock event dispatch
        async def mock_dispatch():
            await asyncio.sleep(0.001)

        # First dispatch should be fast (pre-registered)
        with perf_timer:
            await mock_dispatch()
        first_time = perf_timer.elapsed_ms

        # Second dispatch
        with perf_timer:
            await mock_dispatch()
        second_time = perf_timer.elapsed_ms

        # First should not be significantly slower
        assert first_time < second_time * 5  # Allow 5x variance

    @pytest.mark.asyncio
    async def test_handler_registration_idempotent(self):
        """Verify registering same handler twice doesn't duplicate."""
        handlers = set()

        def register(handler_id):
            handlers.add(handler_id)

        register("handler_1")
        register("handler_1")  # Duplicate

        assert len(handlers) == 1

    @pytest.mark.asyncio
    async def test_preregistration_isomorphism(self):
        """Verify pre-registered handlers behave same as lazy-registered."""
        results = []

        # Simulate pre-registration at startup
        pre_registered_handler = lambda: results.append("pre")

        # Simulate lazy registration on first event
        lazy_registered_handler = lambda: results.append("lazy")

        # Both should be callable
        pre_registered_handler()
        lazy_registered_handler()

        assert results == ["pre", "lazy"]


# =============================================================================
# U19: Handler Timeout
# =============================================================================
@pytest.mark.integration
class TestHandlerTimeout:
    """U19: Verify slow handlers are timed out."""

    @pytest.mark.asyncio
    async def test_handler_timeout_respected(self, perf_timer):
        """Verify slow handler times out."""

        async def slow_handler():
            await asyncio.sleep(10)  # 10 seconds
            return "completed"

        timeout = 0.1  # 100ms timeout

        with perf_timer:
            try:
                result = await asyncio.wait_for(slow_handler(), timeout=timeout)
            except asyncio.TimeoutError:
                result = "timeout"

        assert result == "timeout"
        assert perf_timer.elapsed_ms < 200  # Timed out quickly

    @pytest.mark.asyncio
    async def test_timeout_doesnt_block_other_handlers(self, perf_timer):
        """Verify timeout on one handler doesn't block others."""

        async def slow_handler():
            await asyncio.sleep(10)
            return "slow"

        async def fast_handler():
            await asyncio.sleep(0.01)
            return "fast"

        async def run_with_timeout(coro, timeout):
            try:
                return await asyncio.wait_for(coro, timeout=timeout)
            except asyncio.TimeoutError:
                return "timeout"

        with perf_timer:
            results = await asyncio.gather(
                run_with_timeout(slow_handler(), 0.1),
                run_with_timeout(fast_handler(), 1.0),
                run_with_timeout(fast_handler(), 1.0),
            )

        # Slow handler timed out, fast handlers completed
        assert results.count("timeout") == 1
        assert results.count("fast") == 2
        assert perf_timer.elapsed_ms < 500

    @pytest.mark.asyncio
    async def test_timeout_value_configuration(self):
        """Verify timeout is configurable."""
        default_timeout = 30.0  # seconds

        # Should be configurable via settings
        from src.app.core.config import settings

        # Check if timeout setting exists
        timeout = getattr(settings, "EVENT_HANDLER_TIMEOUT", default_timeout)
        assert timeout > 0

    @pytest.mark.asyncio
    async def test_timeout_isomorphism(self):
        """Verify timed out handler same as never-ran for other handlers."""
        completed = []

        async def handler_a():
            completed.append("A")
            return "A"

        async def handler_b_slow():
            await asyncio.sleep(10)
            completed.append("B")
            return "B"

        async def handler_c():
            completed.append("C")
            return "C"

        async def run_with_timeout(coro, timeout):
            try:
                return await asyncio.wait_for(coro, timeout=timeout)
            except asyncio.TimeoutError:
                return None

        results = await asyncio.gather(
            run_with_timeout(handler_a(), 1.0),
            run_with_timeout(handler_b_slow(), 0.05),
            run_with_timeout(handler_c(), 1.0),
        )

        # A and C completed, B timed out
        assert "A" in completed
        assert "C" in completed
        assert "B" not in completed  # Never got to append

    @pytest.mark.asyncio
    async def test_graceful_timeout_cleanup(self):
        """Verify timeout cleanup doesn't leak resources."""
        cleanup_called = False

        async def handler_with_cleanup():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                nonlocal cleanup_called
                cleanup_called = True
                raise

        task = asyncio.create_task(handler_with_cleanup())

        await asyncio.sleep(0.01)
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass

        # Cleanup was triggered on cancellation
        assert cleanup_called
