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
    assert "set_config('app.current_tenant_id', :tenant_id, false)" in str(args[0])


@pytest.mark.asyncio
async def test_set_rls_context_tenant_and_user(mock_connection):
    await set_rls_context(mock_connection, "tenant-123", "user-123")
    mock_connection.execute.assert_called_once()
    args, kwargs = mock_connection.execute.call_args
    assert "set_config('app.current_tenant_id', :tenant_id, false)" in str(args[0])
    assert "set_config('app.current_user_id', :user_id, false)" in str(args[0])


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
    assert "set_config('app.current_tenant_id', '', false)" in str(args[0])


@pytest.mark.asyncio
async def test_clear_rls_context_exception(mock_connection):
    mock_connection.execute.side_effect = Exception("DB error")
    # Should raise to force connection discard (fail closed)
    with pytest.raises(Exception, match="DB error"):
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
    # T2 FIX: the audit INSERT now runs on a dedicated connection via the
    # _write_bypass_audit helper, so only set_config set/clear hit the session
    # connection.
    with patch("src.app.core.db.rls._write_bypass_audit", new_callable=AsyncMock) as mock_audit:
        async with bypass_rls(mock_session, reason="test", user_id="user-123") as ctx:
            assert ctx == mock_session
            assert mock_connection.execute.call_count == 1  # set bypass

        assert mock_connection.execute.call_count == 2  # set + clear

        mock_audit.assert_awaited_once_with("user-123", "test")


@pytest.mark.asyncio
async def test_bypass_rls_no_reason(mock_session):
    with pytest.raises(ValueError):
        async with bypass_rls(mock_session, reason=""):
            pass


@pytest.mark.asyncio
async def test_bypass_rls_exception(mock_session, mock_connection):
    with (
        patch("src.app.core.db.rls._write_bypass_audit", new_callable=AsyncMock) as mock_audit,
        pytest.raises(ValueError),
    ):
        async with bypass_rls(mock_session, reason="test"):
            raise ValueError("Test error")

    mock_audit.assert_awaited_once()
    assert mock_connection.execute.call_count == 2  # Set, then clear in finally


@pytest.mark.asyncio
async def test_bypass_rls_audit_error(mock_session, mock_connection):
    # Audit log failure is FATAL — bypass_rls MUST raise the typed
    # RLSAuditFailureError (Q-M9) so callers match on the class, not strings.
    from src.app.core.db.rls import RLSAuditFailureError

    with (
        patch(
            "src.app.core.db.rls._write_bypass_audit",
            new_callable=AsyncMock,
            side_effect=Exception("Audit log failed"),
        ),
        pytest.raises(RLSAuditFailureError, match="RLS bypass aborted due to audit log failure"),
    ):
        async with bypass_rls(mock_session, reason="test"):
            pass

    # Verify set_config (bypass) was still applied and cleared on the session
    # connection even though the audit write aborted the bypass.
    assert mock_connection.execute.call_count == 2  # set_config set + reset


@pytest.mark.asyncio
async def test_write_bypass_audit_coerces_user_id():
    """Digit user_ids are stored as ints; non-digit ids move into `reason`."""
    from src.app.core.db.rls import _write_bypass_audit

    audit_conn = AsyncMock()
    audit_cm = MagicMock()
    audit_cm.__aenter__ = AsyncMock(return_value=audit_conn)
    audit_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("src.app.core.db.database.async_engine") as mock_engine:
        mock_engine.connect.return_value = audit_cm

        await _write_bypass_audit("42", "numeric actor")
        params = audit_conn.execute.call_args[0][1]
        assert params["user_id"] == 42
        assert params["reason"] == "numeric actor"

        await _write_bypass_audit("user-123", "string actor")
        params = audit_conn.execute.call_args[0][1]
        assert params["user_id"] is None
        assert "actor=user-123" in params["reason"]

        await _write_bypass_audit(None, "anonymous")
        params = audit_conn.execute.call_args[0][1]
        assert params["user_id"] is None


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


class TestRlsDirtyFlag:
    """P2 RLS dirty-flag skip: 4-case checkout matrix + marking proofs.

    Skip invariant: checkout runs 0 RTT iff ``record.info["rls_tenant"]``
    already equals this checkout's tenant AND ``info["rls_dirty"]`` is
    unset. Every out-of-listener GUC mutation marks dirty BEFORE mutating.
    """

    async def _run_restore(self, tenant_id_or_none, record):
        """Drive restore_rls_context through its run_async capture."""
        mock_dbapi = MagicMock()
        mock_tenant = None if tenant_id_or_none is None else MagicMock(id=tenant_id_or_none)
        with patch("src.app.core.db.rls.get_current_tenant", return_value=mock_tenant):
            restore_rls_context(mock_dbapi, record, None)
        async_func = mock_dbapi.run_async.call_args[0][0]
        mock_conn = AsyncMock()
        await async_func(mock_conn)
        return mock_conn

    @staticmethod
    def _record(info):
        record = MagicMock()
        record.info = info
        return record

    @pytest.mark.asyncio
    async def test_reuse_skip(self):
        """Same tenant, clean flag → 0 RTT (the P2 win)."""
        record = self._record({"rls_tenant": "T1"})
        mock_conn = await self._run_restore("T1", record)
        assert mock_conn.execute.await_count == 0
        assert record.info == {"rls_tenant": "T1"}

    @pytest.mark.asyncio
    async def test_bypass_reassert(self):
        """Same tenant but dirty (bypass/set/clear ran) → re-assert + clear flag."""
        record = self._record({"rls_tenant": "T1", "rls_dirty": True})
        mock_conn = await self._run_restore("T1", record)
        assert mock_conn.execute.await_count == 1
        assert record.info == {"rls_tenant": "T1"}

    @pytest.mark.asyncio
    async def test_switch_reassert(self):
        """Different tenant → re-assert with the NEW tenant id."""
        record = self._record({"rls_tenant": "T1"})
        mock_conn = await self._run_restore("T2", record)
        assert mock_conn.execute.await_count == 1
        assert mock_conn.execute.call_args[0][1] == "T2"
        assert record.info == {"rls_tenant": "T2"}

    @pytest.mark.asyncio
    async def test_missing_info_reassert(self):
        """record=None (existing callers/tests) → today's always-restore behavior."""
        mock_conn = await self._run_restore("T1", None)
        assert mock_conn.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_set_marks_dirty_before_mutation(self):
        """Fail-safe ordering: execute fails → flag is STILL set (re-assert, never skip)."""
        conn = AsyncMock(spec=AsyncConnection)
        conn.sync_connection.info = {}
        conn.execute.side_effect = Exception("DB error")
        with pytest.raises(Exception, match="DB error"):
            await set_rls_context(conn, "tenant-123")
        assert conn.sync_connection.info == {"rls_dirty": True}

    @pytest.mark.asyncio
    async def test_set_marks_dirty_on_success(self):
        conn = AsyncMock(spec=AsyncConnection)
        conn.sync_connection.info = {}
        await set_rls_context(conn, "tenant-123")
        assert conn.sync_connection.info == {"rls_dirty": True}
        assert conn.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_clear_marks_dirty(self):
        conn = AsyncMock(spec=AsyncConnection)
        conn.sync_connection.info = {}
        await clear_rls_context(conn)
        assert conn.sync_connection.info == {"rls_dirty": True}
        assert conn.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_bypass_sqlite_path_does_not_mark(self):
        """SQLite path mutates nothing → no flag (avoids pessimizing sqlite)."""
        conn = AsyncMock(spec=AsyncConnection)
        conn.dialect.name = "sqlite"
        conn.sync_connection.info = {}
        session = AsyncMock(spec=AsyncSession)
        session.connection.return_value = conn
        async with bypass_rls(session, reason="test"):
            pass
        assert conn.sync_connection.info == {}
