from unittest.mock import Mock, patch

from src.app.core.db.rls import RLS_TENANT_VAR, restore_rls_context


def test_restore_rls_context_with_tenant():
    """Test that restore_rls_context executes SQL when tenant is set."""
    mock_session = Mock()
    mock_transaction = Mock()
    mock_connection = Mock()
    # Must mock dialect.name since restore_rls_context checks for "postgresql"
    mock_connection.dialect = Mock()
    mock_connection.dialect.name = "postgresql"

    # Mock tenant
    mock_tenant = Mock()
    mock_tenant.id = 123

    # We patch where it is imported in rls.py
    # Since rls.py does `from ..tenancy.context import get_current_tenant`
    # We should patch `src.app.core.db.rls.get_current_tenant`
    with patch("src.app.core.db.rls.get_current_tenant", return_value=mock_tenant):
        restore_rls_context(mock_session, mock_transaction, mock_connection)

    # Verify execute was called
    mock_connection.execute.assert_called_once()
    call_args = mock_connection.execute.call_args
    sql_text = call_args[0][0]
    params = call_args[0][1]

    assert str(sql_text) == f"SELECT set_config('{RLS_TENANT_VAR}', :tenant_id, true)"
    assert params == {"tenant_id": "123"}


def test_restore_rls_context_without_tenant():
    """Test that restore_rls_context does nothing when no tenant is set."""
    mock_session = Mock()
    mock_transaction = Mock()
    mock_connection = Mock()
    mock_connection.dialect = Mock()
    mock_connection.dialect.name = "postgresql"

    with patch("src.app.core.db.rls.get_current_tenant", return_value=None):
        restore_rls_context(mock_session, mock_transaction, mock_connection)

    mock_connection.execute.assert_not_called()


def test_restore_rls_context_sqlite_skipped():
    """Test that restore_rls_context skips execution for SQLite (used in tests)."""
    mock_session = Mock()
    mock_transaction = Mock()
    mock_connection = Mock()
    mock_connection.dialect = Mock()
    mock_connection.dialect.name = "sqlite"

    mock_tenant = Mock()
    mock_tenant.id = 123

    with patch("src.app.core.db.rls.get_current_tenant", return_value=mock_tenant):
        restore_rls_context(mock_session, mock_transaction, mock_connection)

    # Should not execute for SQLite
    mock_connection.execute.assert_not_called()
