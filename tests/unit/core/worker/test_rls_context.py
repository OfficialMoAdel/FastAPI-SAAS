import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from sqlalchemy import text
from src.app.core.worker.rls_context import job_tenant_context, job_bypass_context


@pytest.mark.asyncio
async def test_job_tenant_context_yields_session_with_rls():
    """Verify job_tenant_context yields session and sets RLS."""
    mock_session = AsyncMock()
    mock_connection = AsyncMock()
    mock_session.connection.return_value = mock_connection

    mock_session_cm = MagicMock()
    mock_session_cm.__aenter__.return_value = mock_session
    mock_session_cm.__aexit__.return_value = None

    with patch("src.app.core.worker.rls_context.local_session", return_value=mock_session_cm):
        async with job_tenant_context(tenant_id=123, user_id=456) as db:
            assert db is mock_session

            # Verify RLS was set
            assert mock_connection.execute.called
            calls = mock_connection.execute.call_args_list

            # Check tenant setting
            tenant_set = False
            print(f"DEBUG: Calls: {calls}")
            for call in calls:
                # call is (args, kwargs) tuple
                args, kwargs = call
                sql = args[0] if args else None
                # Params might be in args[1] or kwargs
                params = args[1] if len(args) > 1 else kwargs
                
                print(f"DEBUG: Checking call - SQL: {sql}, Params: {params}")
                
                # SQLAlchemy text object string representation
                if "app.current_tenant_id" in str(sql) and str(params.get("tenant_id")) == "123":
                    tenant_set = True
            assert tenant_set, "Tenant ID not set in RLS"


@pytest.mark.asyncio
async def test_job_bypass_context_yields_session_with_bypass():
    """Verify job_bypass_context yields session and sets bypass."""
    mock_session = AsyncMock()
    mock_connection = AsyncMock()
    mock_session.connection.return_value = mock_connection

    mock_session_cm = MagicMock()
    mock_session_cm.__aenter__.return_value = mock_session
    mock_session_cm.__aexit__.return_value = None

    with patch("src.app.core.worker.rls_context.local_session", return_value=mock_session_cm):
        async with job_bypass_context("Test Reason") as db:
            assert db is mock_session

            # Verify Bypass set
            assert mock_connection.execute.called
            # We don't verify specific SQL here, just that it executed something (RLS bypass)