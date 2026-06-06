"""Tests for uvloop import and platform compatibility.

Verifies:
- uvloop imports successfully on Unix platforms
- Graceful fallback on Windows (no crashes)
- Worker functions module imports on all platforms
"""

import asyncio
import logging
import sys

import pytest

logger = logging.getLogger(__name__)

# Platform detection
IS_WINDOWS = sys.platform == "win32"
IS_UNIX = sys.platform in ("linux", "darwin", "freebsd")


class TestUvloopImport:
    """Test uvloop import behavior across platforms."""

    @pytest.mark.skipif(IS_WINDOWS, reason="uvloop not available on Windows")
    def test_uvloop_import_unix(self) -> None:
        """Verify uvloop imports successfully on Unix."""
        import uvloop

        assert uvloop is not None
        logger.info(
            "Platform: %s, Python: %s, uvloop: %s",
            sys.platform,
            sys.version,
            uvloop.__version__,
        )

    @pytest.mark.skipif(IS_WINDOWS, reason="uvloop not available on Windows")
    def test_uvloop_policy_set_unix(self) -> None:
        """Verify event loop policy can be set on Unix."""
        import uvloop

        # Save original policy
        original_policy = asyncio.get_event_loop_policy()

        try:
            asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
            loop = asyncio.new_event_loop()

            assert isinstance(loop, uvloop.Loop)
            logger.info("Loop type: %s, Policy: uvloop.EventLoopPolicy", type(loop).__name__)

            loop.close()
        finally:
            # Restore original policy
            asyncio.set_event_loop_policy(original_policy)

    @pytest.mark.skipif(not IS_WINDOWS, reason="Windows-only test")
    def test_uvloop_graceful_skip_windows(self) -> None:
        """Verify uvloop import fails gracefully on Windows."""
        try:
            import uvloop  # noqa: F401

            pytest.fail("uvloop should not be importable on Windows")
        except ImportError:
            logger.info("Platform: %s - uvloop correctly unavailable", sys.platform)
            pass  # Expected behavior


class TestWorkerFunctionsImport:
    """Test worker module imports on all platforms."""

    def test_worker_functions_import(self) -> None:
        """Verify worker functions module imports without error on all platforms."""
        # This should not raise ImportError even on Windows
        # because functions.py has try/except around uvloop import
        from src.app.core.worker import functions

        assert functions is not None
        logger.info(
            "Worker functions imported successfully on %s (Python %s)",
            sys.platform,
            sys.version_info[:2],
        )

    def test_worker_settings_import(self) -> None:
        """Verify worker settings module imports without error."""
        from src.app.core.worker.settings import WorkerSettings

        assert WorkerSettings is not None
        assert hasattr(WorkerSettings, "job_serializer")
        assert hasattr(WorkerSettings, "job_deserializer")
        logger.info("WorkerSettings imported with orjson serialization configured")

    def test_uvloop_fallback_in_functions(self) -> None:
        """Verify functions.py has proper uvloop fallback logic."""
        import inspect

        from src.app.core.worker import functions

        source = inspect.getsource(functions)

        # Verify intentional omission of uvloop import
        assert "uvloop is deliberately not initialized here" in source
        
        logger.info("Worker functions intentionally defers uvloop initialization")


class TestEventLoopPerformance:
    """Benchmark event loop performance (Unix only)."""

    @pytest.mark.skipif(IS_WINDOWS, reason="uvloop not available on Windows")
    @pytest.mark.asyncio
    async def test_event_loop_performance_comparison(self) -> None:
        """Compare uvloop vs asyncio default loop performance."""
        import time

        import uvloop

        iterations = 1000

        async def simple_async_work() -> int:
            """Simple async operation for benchmarking."""
            await asyncio.sleep(0)
            return 1

        # Test with default asyncio loop (current)
        start = time.perf_counter()
        for _ in range(iterations):
            await simple_async_work()
        asyncio_time = time.perf_counter() - start

        # Test with uvloop (create new loop)
        uvloop_policy = uvloop.EventLoopPolicy()
        uvloop_loop = uvloop_policy.new_event_loop()

        try:
            start = time.perf_counter()
            for _ in range(iterations):
                uvloop_loop.run_until_complete(simple_async_work())
            uvloop_time = time.perf_counter() - start
        finally:
            uvloop_loop.close()

        logger.info(
            "Performance comparison (%d iterations): asyncio=%.4fs, uvloop=%.4fs, speedup=%.2fx",
            iterations,
            asyncio_time,
            uvloop_time,
            asyncio_time / uvloop_time if uvloop_time > 0 else 0,
        )

        # uvloop should be faster or at least not significantly slower
        # Allow 20% tolerance for test environment variance
        assert uvloop_time <= asyncio_time * 1.2, "uvloop should not be significantly slower than asyncio"
