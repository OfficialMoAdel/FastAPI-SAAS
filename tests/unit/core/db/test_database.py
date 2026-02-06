from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import async_get_db


@pytest.mark.asyncio
async def test_async_get_db_applies_rls_when_tenant_present():
    """Verify that async_get_db applies RLS context when a tenant is set."""

    # Mock tenant
    mock_tenant = MagicMock()
    mock_tenant.id = "tenant-123"

    # Mock session
    mock_session = AsyncMock(spec=AsyncSession)

    # Mock local_session context manager
    mock_local_session_cm = AsyncMock()
    mock_local_session_cm.__aenter__.return_value = mock_session
    mock_local_session_cm.__aexit__.return_value = None

    # Mock rls_context context manager
    mock_rls_cm = AsyncMock()
    mock_rls_cm.__aenter__.return_value = None
    mock_rls_cm.__aexit__.return_value = None

    with patch("src.app.core.db.database.local_session", return_value=mock_local_session_cm):
        with patch("src.app.core.db.database.get_current_tenant", return_value=mock_tenant):
            with patch("src.app.core.db.database.rls_context", return_value=mock_rls_cm) as mock_rls_factory:
                # Consume the generator
                gen = async_get_db()
                db = await anext(gen)

                assert db is mock_session

                # Verify RLS was applied
                mock_rls_factory.assert_called_once_with(mock_session, "tenant-123")
                mock_rls_cm.__aenter__.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_get_db_no_rls_when_no_tenant():
    """Verify that async_get_db does not apply RLS when no tenant is set."""

    # Mock session
    mock_session = AsyncMock(spec=AsyncSession)

    # Mock local_session context manager
    mock_local_session_cm = AsyncMock()
    mock_local_session_cm.__aenter__.return_value = mock_session
    mock_local_session_cm.__aexit__.return_value = None

    with patch("src.app.core.db.database.local_session", return_value=mock_local_session_cm):
        with patch("src.app.core.db.database.get_current_tenant", return_value=None):
            with patch("src.app.core.db.database.rls_context") as mock_rls_factory:
                # Consume the generator
                gen = async_get_db()
                db = await anext(gen)

                assert db is mock_session

                # Verify RLS was NOT applied
                mock_rls_factory.assert_not_called()
