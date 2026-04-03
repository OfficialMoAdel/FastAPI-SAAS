"""Unit tests for database module.

This file consolidates tests from:
- test_database.py
- test_index_ttl.py
- test_rls_listener.py
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import async_get_db
from src.app.core.db.rls import restore_rls_context
from src.app.core.utils import cache

# =============================================================================
# Tests from test_database.py
# =============================================================================


@pytest.mark.asyncio
async def test_async_get_db_applies_rls_when_tenant_present():
    """Verify that async_get_db applies RLS context when a tenant is set."""

    # Mock tenant
    mock_tenant = MagicMock()
    mock_tenant.id = "tenant-123"

    # Mock session
    mock_session = AsyncMock(spec=AsyncSession)

    # Mock local_session context manager - returns a session object
    mock_local_session = MagicMock(return_value=mock_session)

    # Mock rls_context context manager
    mock_rls_cm = AsyncMock()
    mock_rls_cm.__aenter__.return_value = None
    mock_rls_cm.__aexit__.return_value = None

    with patch("src.app.core.db.database._get_local_session", return_value=mock_local_session):
        with patch("src.app.core.db.database.get_current_tenant", return_value=mock_tenant):
            with patch("src.app.core.db.database.rls_context", return_value=mock_rls_cm) as mock_rls_factory:
                # async_get_db is an async generator, so we iterate it manually
                gen = async_get_db()
                db = await anext(gen)
                assert db is mock_session
                # Verify RLS was applied
                mock_rls_factory.assert_called_once_with(mock_session, "tenant-123")
                # Verify context manager entered
                mock_rls_cm.__aenter__.assert_awaited_once()

                # Cleanup
                try:
                    await anext(gen)
                except StopAsyncIteration:
                    pass
                # Verify context manager exited
                mock_rls_cm.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_get_db_no_rls_when_no_tenant():
    """Verify that async_get_db does not apply RLS when no tenant is set."""

    # Mock session
    mock_session = AsyncMock(spec=AsyncSession)

    # Mock local_session returns a session object
    mock_local_session = MagicMock(return_value=mock_session)

    with patch("src.app.core.db.database._get_local_session", return_value=mock_local_session):
        with patch("src.app.core.db.database.get_current_tenant", return_value=None):
            with patch("src.app.core.db.database.rls_context") as mock_rls_factory:
                # async_get_db is an async generator
                gen = async_get_db()
                db = await anext(gen)
                assert db is mock_session
                # Verify RLS was NOT applied
                mock_rls_factory.assert_not_called()

                try:
                    await anext(gen)
                except StopAsyncIteration:
                    pass


# =============================================================================
# Tests from test_index_ttl.py
# =============================================================================


@pytest.mark.asyncio
async def test_add_to_tenant_index_ttl():
    """Verify index expires after cache entries (2x TTL)."""
    tenant_id = "test_tenant"
    cache_key = "test_key"
    ttl = 3600

    mock_client = AsyncMock()
    # Fix: pipeline needs to return an async context manager, not be a coroutine itself
    mock_client.pipeline = MagicMock()
    mock_pipeline = AsyncMock()
    mock_client.pipeline.return_value.__aenter__.return_value = mock_pipeline

    with patch.object(cache, "client", mock_client):
        await cache.add_to_tenant_index(tenant_id, cache_key, ttl=ttl)

        index_key = f"tenant_index:{tenant_id}"
        mock_pipeline.sadd.assert_called_with(index_key, cache_key)
        mock_pipeline.expire.assert_any_call(index_key, ttl)


@pytest.mark.asyncio
async def test_set_tenant_cache_index_ttl():
    """Verify set_tenant_cache sets index TTL."""
    tenant_id = "test_tenant"
    prefix = "user"
    key_parts = ("123",)
    value = "data"
    ttl = 60

    mock_client = AsyncMock()
    mock_client.pipeline = MagicMock()
    mock_pipeline = AsyncMock()
    mock_client.pipeline.return_value.__aenter__.return_value = mock_pipeline

    with patch.object(cache, "client", mock_client):
        await cache.set_tenant_cache(tenant_id, prefix, key_parts, value, ttl)

        index_key = f"tenant_index:{tenant_id}"
        cache_key = f"tenant:{tenant_id}:{prefix}:123"

        mock_pipeline.setex.assert_called_with(cache_key, ttl, value)
        mock_pipeline.sadd.assert_any_call(index_key, cache_key)
        # expire is now called
        mock_pipeline.expire.assert_any_call(index_key, ttl * 2)


@pytest.mark.asyncio
async def test_migrate_tenant_cache_index_ttl():
    """Verify migration sets appropriate TTL on index."""
    tenant_id = "123"
    mock_client = AsyncMock()
    mock_client.pipeline = MagicMock()
    mock_pipeline = AsyncMock()
    mock_client.pipeline.return_value.__aenter__.return_value = mock_pipeline

    # Mock scan to return one key then done
    mock_client.scan.side_effect = [(10, ["tenant:123:key1"]), (0, [])]
    mock_client.ttl.return_value = 100

    with patch.object(cache, "client", mock_client):
        await cache.migrate_tenant_cache_index(tenant_id)

        index_key = f"tenant_index:{tenant_id}"
        mock_pipeline.sadd.assert_any_call(index_key, "tenant:123:key1")


# =============================================================================
# Tests from test_rls_listener.py
# =============================================================================


@pytest.mark.asyncio
async def test_restore_rls_context_with_tenant():
    """Test that restore_rls_context executes SQL when tenant is set."""
    mock_dbapi_connection = MagicMock()

    mock_tenant = MagicMock()
    mock_tenant.id = "123"

    with patch("src.app.core.db.rls.get_current_tenant", return_value=mock_tenant):
        restore_rls_context(mock_dbapi_connection, None, None)

    # Verify run_async was called
    mock_dbapi_connection.run_async.assert_called_once()

    # Call the async function passed to run_async
    async_func = mock_dbapi_connection.run_async.call_args[0][0]
    mock_conn = AsyncMock()
    await async_func(mock_conn)

    mock_conn.execute.assert_called_once()
    sql_text = mock_conn.execute.call_args[0][0]
    assert "set_config('app.current_tenant_id', '123', false)" in str(sql_text)


@pytest.mark.asyncio
async def test_restore_rls_context_without_tenant():
    """Test that restore_rls_context clears RLS when no tenant is set."""
    mock_dbapi_connection = MagicMock()

    with patch("src.app.core.db.rls.get_current_tenant", return_value=None):
        restore_rls_context(mock_dbapi_connection, None, None)

    mock_dbapi_connection.run_async.assert_called_once()

    async_func = mock_dbapi_connection.run_async.call_args[0][0]
    mock_conn = AsyncMock()
    await async_func(mock_conn)

    assert mock_conn.execute.call_count == 3


@pytest.mark.asyncio
async def test_restore_rls_context_exception_handling():
    """Test that restore_rls_context logs heavily but doesn't raise."""
    mock_dbapi_connection = MagicMock()
    mock_tenant = MagicMock()
    mock_tenant.id = "123"

    with patch("src.app.core.db.rls.get_current_tenant", return_value=mock_tenant):
        restore_rls_context(mock_dbapi_connection, None, None)

    async_func = mock_dbapi_connection.run_async.call_args[0][0]
    mock_conn = AsyncMock()
    mock_conn.execute.side_effect = Exception("DB error")

    with patch("src.app.core.db.rls.logger.critical") as mock_logger:
        # Should not raise
        await async_func(mock_conn)
        mock_logger.assert_called_once()
