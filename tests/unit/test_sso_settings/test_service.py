"""Unit tests for SSOSettingsService.

TDD: These tests validate the SSOSettingsService encryption and CRUD logic.

Task: T099 [US11] Write unit tests for SSOSettingsService
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.sso_settings.schemas import (
    SSOSettingsCreate,
    SSOSettingsUpdate,
)
from src.app.modules.sso_settings.service import SSOSettingsService, sso_settings_service


class TestSSOSettingsService:
    """Test SSOSettingsService functionality."""

    def test_service_exists(self):
        """Verify SSOSettingsService can be imported."""
        assert SSOSettingsService is not None
        assert sso_settings_service is not None

    def test_service_has_required_methods(self):
        """Verify service has all required CRUD methods."""
        service = SSOSettingsService()

        assert hasattr(service, "create_settings")
        assert hasattr(service, "get_settings_by_provider")
        assert hasattr(service, "get_decrypted_settings")
        assert hasattr(service, "update_settings")
        assert hasattr(service, "delete_settings")


class TestSSOSettingsServiceCreate:
    """Test create_settings method."""

    @pytest.mark.asyncio
    async def test_create_settings_encrypts_credentials(self):
        """Verify client_id and client_secret are encrypted before storage."""
        service = SSOSettingsService()
        mock_db = AsyncMock()
        
        # Mock refresh to set DB fields on the object
        async def mock_refresh(obj):
            obj.id = 1
            obj.created_at = datetime.now(timezone.utc)
            obj.updated_at = datetime.now(timezone.utc)
        mock_db.refresh = mock_refresh

        mock_encryption = MagicMock()
        mock_encryption.encrypt_column.return_value = "encrypted_value"

        with patch("src.app.modules.sso_settings.service.get_encryption", return_value=mock_encryption):
            with patch("src.app.modules.sso_settings.service.crud_sso_settings") as mock_crud:
                mock_crud.exists = AsyncMock(return_value=False)

                settings = SSOSettingsCreate(
                    provider="google",
                    client_id="test_client_id",
                    client_secret="test_client_secret",
                )

                await service.create_settings(mock_db, settings, user_id=1)

                # Verify encryption was called for both fields
                encrypt_calls = mock_encryption.encrypt_column.call_args_list
                assert len(encrypt_calls) == 2
                assert encrypt_calls[0][0][0] == "test_client_id"
                assert encrypt_calls[1][0][0] == "test_client_secret"

    @pytest.mark.asyncio
    async def test_create_settings_rejects_duplicate_provider(self):
        """Verify DuplicateValueException raised for existing provider."""
        from src.app.core.exceptions.http_exceptions import DuplicateValueException

        service = SSOSettingsService()
        mock_db = AsyncMock()

        with patch("src.app.modules.sso_settings.service.crud_sso_settings") as mock_crud:
            mock_crud.exists = AsyncMock(return_value=True)

            settings = SSOSettingsCreate(
                provider="google",
                client_id="test_id",
                client_secret="test_secret",
            )

            with pytest.raises(DuplicateValueException):
                await service.create_settings(mock_db, settings, user_id=1)


class TestSSOSettingsServiceGetDecrypted:
    """Test get_decrypted_settings method."""

    @pytest.mark.asyncio
    async def test_get_decrypted_settings_returns_plaintext(self):
        """Verify credentials are decrypted when retrieved."""
        service = SSOSettingsService()
        mock_db = AsyncMock()

        mock_settings = MagicMock()
        mock_settings.client_id = b"encrypted_client_id"
        mock_settings.client_secret = b"encrypted_client_secret"
        mock_settings.additional_config = {"extra": "value"}
        mock_settings.is_enabled = True

        mock_encryption = MagicMock()
        mock_encryption.decrypt_column.side_effect = ["decrypted_id", "decrypted_secret"]

        with patch("src.app.modules.sso_settings.service.crud_sso_settings") as mock_crud:
            mock_crud.get = AsyncMock(return_value=mock_settings)

            with patch("src.app.modules.sso_settings.service.get_encryption", return_value=mock_encryption):
                result = await service.get_decrypted_settings(mock_db, "google")

                assert result["client_id"] == "decrypted_id"
                assert result["client_secret"] == "decrypted_secret"
                assert result["additional_config"] == {"extra": "value"}
                assert result["is_enabled"] is True

    @pytest.mark.asyncio
    async def test_get_decrypted_settings_raises_not_found(self):
        """Verify NotFoundException raised for missing provider."""
        from src.app.core.exceptions.http_exceptions import NotFoundException

        service = SSOSettingsService()
        mock_db = AsyncMock()

        with patch("src.app.modules.sso_settings.service.crud_sso_settings") as mock_crud:
            mock_crud.get = AsyncMock(return_value=None)

            with pytest.raises(NotFoundException):
                await service.get_decrypted_settings(mock_db, "nonexistent")


class TestSSOSettingsServiceUpdate:
    """Test update_settings method."""

    @pytest.mark.asyncio
    async def test_update_settings_encrypts_new_credentials(self):
        """Verify updated credentials are encrypted."""
        service = SSOSettingsService()
        mock_db = AsyncMock()

        # First get returns current settings (before update)
        mock_current = MagicMock()
        mock_current.id = 1
        mock_current.provider = "google"
        
        # Second get returns updated settings (after update)
        mock_updated = MagicMock()
        mock_updated.id = 1
        mock_updated.provider = "google"
        mock_updated.additional_config = {}
        mock_updated.is_enabled = True
        mock_updated.created_at = datetime.now(timezone.utc)
        mock_updated.updated_at = datetime.now(timezone.utc)

        mock_encryption = MagicMock()
        mock_encryption.encrypt_column.return_value = "new_encrypted_value"

        with patch("src.app.modules.sso_settings.service.crud_sso_settings") as mock_crud:
            # get called twice: before update and after update
            mock_crud.get = AsyncMock(side_effect=[mock_current, mock_updated])
            mock_crud.update = AsyncMock()

            with patch("src.app.modules.sso_settings.service.get_encryption", return_value=mock_encryption):
                update = SSOSettingsUpdate(
                    client_id="new_client_id",
                    client_secret="new_client_secret",
                )

                await service.update_settings(mock_db, "google", update, user_id=1)

                # Verify encryption was called for both new values
                assert mock_encryption.encrypt_column.call_count == 2

    @pytest.mark.asyncio
    async def test_update_settings_partial_update(self):
        """Verify partial updates work (only specified fields updated)."""
        service = SSOSettingsService()
        mock_db = AsyncMock()

        # First get returns current settings
        mock_current = MagicMock()
        mock_current.id = 1
        mock_current.provider = "google"
        
        # Second get returns updated settings
        mock_updated = MagicMock()
        mock_updated.id = 1
        mock_updated.provider = "google"
        mock_updated.additional_config = {}
        mock_updated.is_enabled = False
        mock_updated.created_at = datetime.now(timezone.utc)
        mock_updated.updated_at = datetime.now(timezone.utc)

        with patch("src.app.modules.sso_settings.service.crud_sso_settings") as mock_crud:
            mock_crud.get = AsyncMock(side_effect=[mock_current, mock_updated])
            mock_crud.update = AsyncMock()

            update = SSOSettingsUpdate(is_enabled=False)

            await service.update_settings(mock_db, "google", update, user_id=1)

            # Verify update was called with only is_enabled
            call_kwargs = mock_crud.update.call_args[1]
            assert "is_enabled" in call_kwargs["object"]


class TestSSOSettingsServiceDelete:
    """Test delete_settings method."""

    @pytest.mark.asyncio
    async def test_delete_settings_removes_provider(self):
        """Verify delete_settings calls crud delete."""
        service = SSOSettingsService()
        mock_db = AsyncMock()

        with patch("src.app.modules.sso_settings.service.crud_sso_settings") as mock_crud:
            mock_crud.exists = AsyncMock(return_value=True)
            mock_crud.delete = AsyncMock()

            await service.delete_settings(mock_db, "google", user_id=1)

            mock_crud.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_settings_raises_not_found(self):
        """Verify NotFoundException raised for missing provider."""
        from src.app.core.exceptions.http_exceptions import NotFoundException

        service = SSOSettingsService()
        mock_db = AsyncMock()

        with patch("src.app.modules.sso_settings.service.crud_sso_settings") as mock_crud:
            mock_crud.exists = AsyncMock(return_value=False)

            with pytest.raises(NotFoundException):
                await service.delete_settings(mock_db, "nonexistent", user_id=1)


class TestSSOSettingsServiceValidProviders:
    """Test provider validation."""

    @pytest.mark.parametrize("provider", ["google", "github", "microsoft", "apple"])
    def test_valid_providers_accepted(self, provider):
        """Verify all supported SSO providers are valid."""
        # The schema should accept these providers
        settings = SSOSettingsCreate(
            provider=provider,
            client_id="test_id",
            client_secret="test_secret",
        )

        assert settings.provider == provider
