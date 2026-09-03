"""U23 - Storage Settings N+1 Elimination Unit Tests.

Tests verifying that StorageSettingsService.get_settings_or_raise
properly raises StorageNotConfiguredError instead of falling back
to N+1 pattern queries.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.storage import (
    StorageAdminService,
    StorageNotConfiguredError,
)

StorageSettingsService = StorageAdminService


@pytest.fixture
def mock_db():
    """Create mock database session."""
    db = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture
def storage_service(mock_db):
    """Create StorageSettingsService with mocked db."""
    return StorageSettingsService(mock_db)


class TestStorageSettingsNoFallback:
    """Verify storage settings raises error instead of N+1 fallback."""

    @pytest.mark.asyncio
    async def test_get_settings_or_raise_not_found(self, storage_service, mock_db):
        """Verify StorageNotConfiguredError raised when settings missing."""
        # Mock no settings found
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(StorageNotConfiguredError) as exc_info:
            await storage_service.get_settings_or_raise(tenant_id=999)

        assert "Storage not configured" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_settings_or_raise_found(self, storage_service, mock_db):
        """Verify settings returned when they exist."""
        mock_settings = MagicMock()
        mock_settings.id = 1
        mock_settings.tenant_id = 100
        mock_settings.provider = "local"
        mock_settings.max_file_size_mb = 10

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_settings
        mock_db.execute.return_value = mock_result

        result = await storage_service.get_settings_or_raise(tenant_id=100)

        assert result.id == 1
        assert result.tenant_id == 100

    @pytest.mark.asyncio
    async def test_single_query_for_settings(self, storage_service, mock_db):
        """Verify only ONE query executed for settings lookup."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        try:
            await storage_service.get_settings_or_raise(tenant_id=999)
        except StorageNotConfiguredError:
            pass

        # Only ONE execute call - no N+1 fallback queries
        assert mock_db.execute.call_count == 1


class TestNoDefaultFallback:
    """Verify no default/fallback settings are created on missing."""

    @pytest.mark.asyncio
    async def test_no_auto_create_on_missing(self, storage_service, mock_db):
        """Verify no automatic settings creation when missing."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        try:
            await storage_service.get_settings_or_raise(tenant_id=999)
        except StorageNotConfiguredError:
            pass

        # No add() call - no auto-creation
        mock_db.add.assert_not_called()
        # No commit - no side effects
        mock_db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_settings_returns_none_not_fallback(self, storage_service, mock_db):
        """Verify get_settings returns None, not a fallback object."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await storage_service.get_settings(tenant_id=999)

        # Returns None, not a default/fallback
        assert result is None


class TestIsomorphismStorageSettings:
    """Isomorphism tests - verify same behavior as documented."""

    @pytest.mark.asyncio
    async def test_error_message_consistency(self, storage_service, mock_db):
        """Verify error message is consistent and informative."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(StorageNotConfiguredError) as exc_info:
            await storage_service.get_settings_or_raise(tenant_id=999)

        # Error should guide user to admin action
        error_msg = str(exc_info.value)
        assert "Storage not configured" in error_msg
        assert "administrator" in error_msg.lower()

    @pytest.mark.asyncio
    async def test_settings_data_integrity(self, storage_service, mock_db):
        """Verify returned settings match stored data exactly."""
        mock_settings = MagicMock()
        mock_settings.id = 42
        mock_settings.tenant_id = 100
        mock_settings.provider = "azure"
        mock_settings.bucket_name = "test-container"
        mock_settings.region = "eastus"
        mock_settings.max_file_size_mb = 25
        mock_settings.max_file_count = 1000
        mock_settings.storage_quota_bytes = 10737418240  # 10GB
        mock_settings.allowed_file_types = ["image/png", "image/jpeg"]

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_settings
        mock_db.execute.return_value = mock_result

        result = await storage_service.get_settings_or_raise(tenant_id=100)

        # Isomorphism: All fields match
        assert result.id == 42
        assert result.tenant_id == 100
        assert result.provider == "azure"
        assert result.bucket_name == "test-container"
        assert result.region == "eastus"
        assert result.max_file_size_mb == 25
        assert result.max_file_count == 1000
        assert result.storage_quota_bytes == 10737418240


class TestGetProviderNoN1:
    """Verify get_provider doesn't cause N+1 issues."""

    @pytest.mark.asyncio
    async def test_get_provider_single_settings_query(self, storage_service, mock_db):
        """Verify get_provider uses single query for settings."""
        mock_settings = MagicMock()
        mock_settings.provider = "local"
        mock_settings.credentials_encrypted = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_settings
        mock_db.execute.return_value = mock_result

        with patch.object(storage_service, "_create_provider") as mock_create:
            mock_create.return_value = MagicMock()

            await storage_service.get_provider(tenant_id=100)

        # Only ONE query for settings
        assert mock_db.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_get_provider_raises_on_missing(self, storage_service, mock_db):
        """Verify get_provider raises error when settings missing."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(StorageNotConfiguredError):
            await storage_service.get_provider(tenant_id=999)
