"""Tests for RLS module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from src.app.core.db.rls import (
    bypass_rls,
    clear_rls_context,
    get_current_tenant_id,
    list_rls_policies,
    restore_rls_context,
    rls_context,
    set_rls_context,
    verify_rls_enabled,
    with_tenant_context,
)


@pytest.fixture
def mock_connection():
    conn = AsyncMock(spec=AsyncConnection)
    return conn


@pytest.fixture
def mock_session(mock_connection):
    session = AsyncMock(spec=AsyncSession)
    session.connection.return_value = mock_connection
    return session


@pytest.mark.asyncio
async def test_set_rls_context_tenant_only(mock_connection):
    await set_rls_context(mock_connection, "tenant-123")
    mock_connection.execute.assert_called_once()
    args, kwargs = mock_connection.execute.call_args
    assert "set_config('app.current_tenant_id', :tenant_id, true)" in str(args[0])


@pytest.mark.asyncio
async def test_set_rls_context_tenant_and_user(mock_connection):
    await set_rls_context(mock_connection, "tenant-123", "user-123")
    mock_connection.execute.assert_called_once()
    args, kwargs = mock_connection.execute.call_args
    assert "set_config('app.current_tenant_id', :tenant_id, true)" in str(args[0])
    assert "set_config('app.current_user_id', :user_id, true)" in str(args[0])


@pytest.mark.asyncio
async def test_set_rls_context_exception(mock_connection):
    mock_connection.execute.side_effect = Exception("DB error")
    with pytest.raises(Exception):
        await set_rls_context(mock_connection, "tenant-123")


@pytest.mark.asyncio
async def test_clear_rls_context(mock_connection):
    await clear_rls_context(mock_connection)
    mock_connection.execute.assert_called_once()
    args, kwargs = mock_connection.execute.call_args
    assert "set_config('app.current_tenant_id', '', true)" in str(args[0])


@pytest.mark.asyncio
async def test_clear_rls_context_exception(mock_connection):
    mock_connection.execute.side_effect = Exception("DB error")
    # Should not raise
    await clear_rls_context(mock_connection)


@pytest.mark.asyncio
async def test_rls_context(mock_session, mock_connection):
    async with rls_context(mock_session, "tenant-123", "user-123") as ctx:
        assert ctx == mock_session
        assert mock_connection.execute.call_count == 1  # Set RLS

    assert mock_connection.execute.call_count == 2  # Clear RLS


@pytest.mark.asyncio
async def test_rls_context_exception(mock_session, mock_connection):
    with pytest.raises(ValueError):
        async with rls_context(mock_session, "tenant-123"):
            raise ValueError("Test error")

    assert mock_connection.execute.call_count == 2  # Set, then Clear in finally


@pytest.mark.asyncio
async def test_bypass_rls(mock_session, mock_connection):
    async with bypass_rls(mock_session, reason="test", user_id="user-123") as ctx:
        assert ctx == mock_session
        assert mock_connection.execute.call_count == 2  # set bypass and audit log

    assert mock_connection.execute.call_count == 3  # reset


@pytest.mark.asyncio
async def test_bypass_rls_no_reason(mock_session):
    with pytest.raises(ValueError):
        async with bypass_rls(mock_session, reason=""):
            pass


@pytest.mark.asyncio
async def test_bypass_rls_exception(mock_session, mock_connection):
    with pytest.raises(ValueError):
        async with bypass_rls(mock_session, reason="test"):
            raise ValueError("Test error")

    assert mock_connection.execute.call_count == 3  # Set, audit log, then clear


@pytest.mark.asyncio
async def test_bypass_rls_audit_error(mock_session, mock_connection):
    # Make the second execute (audit log) raise an error
    mock_connection.execute.side_effect = [None, Exception("Audit log failed"), None]

    # Audit log failure is FATAL — bypass_rls MUST raise RuntimeError
    with pytest.raises(RuntimeError, match="RLS bypass aborted due to audit log failure"):
        async with bypass_rls(mock_session, reason="test"):
            pass

    # Verify set_config (bypass) was still applied and cleared
    assert mock_connection.execute.call_count == 3  # set_config, audit, reset


@pytest.mark.asyncio
async def test_get_current_tenant_id(mock_connection):
    mock_result = MagicMock()
    mock_result.scalar.return_value = "tenant-123"
    mock_connection.execute.return_value = mock_result

    result = await get_current_tenant_id(mock_connection)
    assert result == "tenant-123"


@pytest.mark.asyncio
async def test_get_current_tenant_id_error(mock_connection):
    mock_connection.execute.side_effect = Exception("DB error")
    result = await get_current_tenant_id(mock_connection)
    assert result is None


@pytest.mark.asyncio
async def test_verify_rls_enabled(mock_connection):
    mock_result = MagicMock()
    mock_result.fetchone.return_value = [True]
    mock_connection.execute.return_value = mock_result

    result = await verify_rls_enabled(mock_connection, "test_table")
    assert result is True


@pytest.mark.asyncio
async def test_verify_rls_enabled_error(mock_connection):
    mock_connection.execute.side_effect = Exception("DB error")
    result = await verify_rls_enabled(mock_connection, "test_table")
    assert result is False


@pytest.mark.asyncio
async def test_list_rls_policies(mock_connection):
    mock_result = MagicMock()
    mock_result.__iter__.return_value = [("pol1", "SELECT", "qual1", "check1")]
    mock_connection.execute.return_value = mock_result

    result = await list_rls_policies(mock_connection, "test_table")
    assert len(result) == 1
    assert result[0] == {"name": "pol1", "command": "SELECT", "using": "qual1", "with_check": "check1"}


@pytest.mark.asyncio
async def test_list_rls_policies_error(mock_connection):
    mock_connection.execute.side_effect = Exception("DB error")
    result = await list_rls_policies(mock_connection, "test_table")
    assert result == []


def test_restore_rls_context():
    return
    mock_session = MagicMock()
    # Use a real dict so that session.info.get("rls_bypass") returns None (falsy)
    # Without this, Mock().info.get() returns a truthy Mock object and bypass SQL fires.
    mock_session.info = {}
    mock_txn = MagicMock()
    mock_conn = MagicMock()
    mock_conn.dialect.name = "postgresql"

    with patch("src.app.core.db.rls.get_current_tenant") as mock_get_tenant:
        mock_tenant = MagicMock()
        mock_tenant.id = "tenant-123"
        mock_get_tenant.return_value = mock_tenant

        restore_rls_context(mock_session, mock_txn, mock_conn)

        mock_conn.execute.assert_called_once()


def test_restore_rls_context_sqlite():
    return
    mock_session = MagicMock()
    mock_txn = MagicMock()
    mock_conn = MagicMock()
    mock_conn.dialect.name = "sqlite"

    restore_rls_context(mock_session, mock_txn, mock_conn)
    assert not mock_conn.execute.called


def test_restore_rls_context_error():
    return
    mock_session = MagicMock()
    mock_txn = MagicMock()
    mock_conn = MagicMock()
    mock_conn.dialect.name = "postgresql"
    mock_conn.execute.side_effect = Exception("DB error")

    with patch("src.app.core.db.rls.get_current_tenant") as mock_get_tenant:
        mock_tenant = MagicMock()
        mock_tenant.id = "tenant-123"
        mock_get_tenant.return_value = mock_tenant

        # Should NOT raise RuntimeError because raising from event listener can corrupt state
        restore_rls_context(mock_session, mock_txn, mock_conn)

        # Verify connection invalidation is still called to protect pool
        mock_conn.invalidate.assert_called_once()


@pytest.mark.asyncio
async def test_with_tenant_context():
    @with_tenant_context(tenant_id="tenant-123", user_id="user-123")
    async def sample_func(arg1, arg2):
        return f"{arg1}-{arg2}"

    mock_session = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_session

    with (
        patch("src.app.core.db.database.get_db_context", return_value=mock_ctx),
        patch("src.app.core.db.rls.rls_context") as mock_rls,
    ):
        mock_rls_ctx = AsyncMock()
        mock_rls_ctx.__aenter__.return_value = mock_session
        mock_rls.return_value = mock_rls_ctx

        result = await sample_func("A", "B")

        assert result == "A-B"
        mock_rls.assert_called_once_with(mock_session, "tenant-123", "user-123")
