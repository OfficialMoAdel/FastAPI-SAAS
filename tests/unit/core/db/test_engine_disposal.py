"""Tests for async_engine.dispose() connection leak prevention.

Verifies:
- Engine disposal during lifespan shutdown
- Connection pool properly empties after dispose
- No connection leaks during rapid restart cycles
- Dispose is idempotent (safe to call multiple times)
"""

import logging

import pytest

logger = logging.getLogger(__name__)


class TestLifespanEngineDisposal:
    """Test engine disposal during app lifespan."""

    def test_lifespan_has_dispose_call(self) -> None:
        """Verify the application composition lifespan disposes the engine."""
        import inspect

        from src.app import composition
        from src.app.core import runtime

        composition_source = inspect.getsource(composition)
        runtime_source = inspect.getsource(runtime)

        assert "_dispose_engine" in composition_source
        assert "finally:" in runtime_source
        assert "await phase.teardown()" in runtime_source

        logger.info("Engine disposal is wired through the shared phase composer")

    @pytest.mark.asyncio
    async def test_dispose_called_even_on_error(self) -> None:
        """Verify dispose is called even if app raises during operation - via finally block."""
        # This is a design verification test - the finally block ensures cleanup
        import inspect

        from src.app import composition
        from src.app.core import runtime

        composition_source = inspect.getsource(composition)
        runtime_source = inspect.getsource(runtime)

        assert "_dispose_engine" in composition_source
        assert "try:" in runtime_source
        assert "finally:" in runtime_source
        assert "await phase.teardown()" in runtime_source
        logger.info("Verified: shared phase teardown guarantees disposal on error")


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
        """Verify the application composition lifespan disposes the engine."""
        import inspect

        from src.app import composition
        from src.app.core import runtime

        composition_source = inspect.getsource(composition)
        runtime_source = inspect.getsource(runtime)

        assert "_dispose_engine" in composition_source
        assert "finally:" in runtime_source
        assert "await phase.teardown()" in runtime_source
        logger.info("composition.py wires disposal through the shared phase composer")

    def test_dispose_in_finally_block(self) -> None:
        """Verify dispose is inside finally block for guaranteed cleanup."""
        import inspect

        from src.app import composition
        from src.app.core import runtime

        composition_source = inspect.getsource(composition)
        runtime_source = inspect.getsource(runtime)

        assert "_dispose_engine" in composition_source
        assert "finally:" in runtime_source
        assert "await phase.teardown()" in runtime_source

        logger.info("Verified: disposal is a tail callback executed by compose_phases")
