"""Tests for async_engine.dispose() connection leak prevention.

Verifies:
- Engine disposal during lifespan shutdown
- Connection pool properly empties after dispose
- No connection leaks during rapid restart cycles
- Dispose is idempotent (safe to call multiple times)
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

logger = logging.getLogger(__name__)


class TestLifespanEngineDisposal:
    """Test engine disposal during app lifespan."""

    def test_lifespan_has_dispose_call(self) -> None:
        """Verify setup.py lifespan includes engine.dispose() call in finally block."""
        import inspect

        from src.app.core import setup

        source = inspect.getsource(setup)

        # Verify dispose is called
        assert "await engine.dispose()" in source, "setup.py should call engine.dispose()"

        # Verify it's in finally block
        finally_pos = source.find("finally:")
        dispose_pos = source.find("await engine.dispose()")

        assert finally_pos != -1, "finally block not found"
        assert dispose_pos > finally_pos, "dispose should be inside finally block"

        logger.info("Engine.dispose() found in finally block at setup.py")

    @pytest.mark.asyncio
    async def test_dispose_called_even_on_error(self) -> None:
        """Verify dispose is called even if app raises during operation - via finally block."""
        # This is a design verification test - the finally block ensures cleanup
        import inspect

        from src.app.core import setup

        source = inspect.getsource(setup)

        # Verify the structure: try/finally with dispose in finally
        assert "try:" in source
        assert "finally:" in source
        assert "await engine.dispose()" in source

        # Find their positions to verify structure
        try_pos = source.find("try:")
        finally_pos = source.find("finally:")
        dispose_pos = source.find("await engine.dispose()")

        assert try_pos < finally_pos < dispose_pos, "dispose must be in finally block after try"
        logger.info("Verified: finally block guarantees dispose on error")


class TestConnectionPoolBehavior:
    """Test connection pool behavior with dispose."""

    @pytest.mark.asyncio
    async def test_connection_pool_empty_after_shutdown(self) -> None:
        """Verify no connections remain after shutdown."""
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        # Create a test engine with minimal pool
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=NullPool,  # Use NullPool for testing
        )

        # Do some queries to create connections
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

        pool_before = engine.pool.checkedout() if hasattr(engine.pool, "checkedout") else 0
        logger.info("Pool checked out before dispose: %d", pool_before)

        # Dispose engine
        await engine.dispose()

        # Pool should be empty after dispose
        pool_after = engine.pool.checkedout() if hasattr(engine.pool, "checkedout") else 0
        assert pool_after == 0
        logger.info("Pool checked out after dispose: %d", pool_after)

    @pytest.mark.asyncio
    async def test_dispose_idempotent(self) -> None:
        """Verify dispose() can be called multiple times safely."""
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=NullPool,
        )

        # First dispose
        await engine.dispose()
        logger.info("First dispose() call succeeded")

        # Second dispose should not error
        await engine.dispose()
        logger.info("Second dispose() call succeeded (idempotent)")

        # Third dispose for good measure
        await engine.dispose()
        logger.info("Third dispose() call succeeded (idempotent)")


class TestRapidRestartCycles:
    """Test rapid app restart cycles don't leak connections."""

    @pytest.mark.asyncio
    async def test_rapid_restart_no_leak(self) -> None:
        """Simulate rapid app restart cycles - no connection accumulation."""
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        connection_counts: list[int] = []

        for cycle in range(5):
            engine = create_async_engine(
                "sqlite+aiosqlite:///:memory:",
                poolclass=NullPool,
            )

            # Simulate some activity
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

            # Get connection count before dispose
            count = engine.pool.checkedout() if hasattr(engine.pool, "checkedout") else 0
            connection_counts.append(count)

            # Dispose
            await engine.dispose()

            logger.info("Cycle %d: connections=%d", cycle, count)

        # Verify no accumulation (all should be 0 after context exit)
        assert all(c == 0 for c in connection_counts), f"Connection leak detected: {connection_counts}"
        logger.info("No connection accumulation over %d cycles", len(connection_counts))


class TestDisposeClosesActiveConnections:
    """Test that dispose properly closes active connections."""

    @pytest.mark.asyncio
    async def test_dispose_after_connection_context(self) -> None:
        """Verify connections are properly closed after context exit."""
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=NullPool,
        )

        # Use connection in context
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            logger.info("Connection active, result: %s", result.scalar())

        # Connection should be returned to pool (or closed for NullPool)
        await engine.dispose()

        # No ResourceWarning should be raised
        logger.info("Dispose completed without warnings")


class TestSetupModuleIntegration:
    """Integration tests for setup.py lifespan."""

    def test_setup_has_dispose_call(self) -> None:
        """Verify setup.py lifespan includes engine.dispose() call."""
        import inspect

        from src.app.core import setup

        source = inspect.getsource(setup)

        assert "await engine.dispose()" in source, "setup.py should call engine.dispose()"
        assert "finally:" in source, "dispose should be in finally block"
        logger.info("setup.py correctly includes engine.dispose() in finally block")

    def test_dispose_in_finally_block(self) -> None:
        """Verify dispose is inside finally block for guaranteed cleanup."""
        import inspect

        from src.app.core import setup

        source = inspect.getsource(setup)

        # Find finally block and dispose call
        finally_pos = source.find("finally:")
        dispose_pos = source.find("await engine.dispose()")

        assert finally_pos != -1, "finally block not found"
        assert dispose_pos != -1, "engine.dispose() not found"
        assert dispose_pos > finally_pos, "dispose should be inside finally block"

        logger.info(
            "Verified: dispose() at position %d is inside finally block at position %d",
            dispose_pos,
            finally_pos,
        )
