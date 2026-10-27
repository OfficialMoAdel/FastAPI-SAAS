"""Unit tests for SSOSettingsService.

TDD: These tests validate the SSOSettingsService encryption and CRUD logic.

Task: T099 [US11] Write unit tests for SSOSettingsService
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.auth.schemas import (
    SSOSettingsCreate,
    SSOSettingsUpdate,
)
from src.app.modules.auth.services.sso_settings import SSOSettingsService, sso_settings_service


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
            obj.created_at = datetime.now(UTC)
            obj.updated_at = datetime.now(UTC)

        mock_db.refresh = mock_refresh

        mock_encryption = MagicMock()
        mock_encryption.encrypt_column.return_value = "encrypted_value"

        with patch("src.app.modules.auth.services.sso_settings.get_encryption", return_value=mock_encryption):
            with patch("src.app.modules.auth.services.sso_settings.crud_sso_settings") as mock_crud:
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
        """Verify DuplicateError raised for existing provider."""
        from src.app.shared.exceptions import DuplicateError

        service = SSOSettingsService()
        mock_db = AsyncMock()

        with patch("src.app.modules.auth.services.sso_settings.crud_sso_settings") as mock_crud:
            mock_crud.exists = AsyncMock(return_value=True)

            settings = SSOSettingsCreate(
                provider="google",
                client_id="test_id",
                client_secret="test_secret",
            )

            with pytest.raises(DuplicateError):
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

        with patch("src.app.modules.auth.services.sso_settings.crud_sso_settings") as mock_crud:
            mock_crud.get = AsyncMock(return_value=mock_settings)

            with patch("src.app.modules.auth.services.sso_settings.get_encryption", return_value=mock_encryption):
                result = await service.get_decrypted_settings(mock_db, "google")

                assert result["client_id"] == "decrypted_id"
                assert result["client_secret"] == "decrypted_secret"
                assert result["additional_config"] == {"extra": "value"}
                assert result["is_enabled"] is True

    @pytest.mark.asyncio
    async def test_get_decrypted_settings_raises_not_found(self):
        """Verify ModelNotFoundError raised for missing provider."""
        from src.app.shared.exceptions import ModelNotFoundError

        service = SSOSettingsService()
        mock_db = AsyncMock()

        with patch("src.app.modules.auth.services.sso_settings.crud_sso_settings") as mock_crud:
            mock_crud.get = AsyncMock(return_value=None)

            with pytest.raises(ModelNotFoundError):
                await service.get_decrypted_settings(mock_db, "nonexistent")


class TestSSOSettingsServiceUpdate:
    """Test update_settings method."""

    @pytest.mark.asyncio
    async def test_update_settings_encrypts_new_credentials(self):
        """Verify updated credentials are encrypted."""
        service = SSOSettingsService()
        mock_db = AsyncMock()

        mock_current = {
            "id": 1,
            "provider": "google",
            "additional_config": {},
            "is_enabled": True,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }

        mock_encryption = MagicMock()
        mock_encryption.encrypt_column.return_value = "new_encrypted_value"

        with patch("src.app.modules.auth.services.sso_settings.crud_sso_settings") as mock_crud:
            # get called twice: before update and after update
            mock_crud.get = AsyncMock(return_value=mock_current)
            mock_crud.update = AsyncMock()

            with patch("src.app.modules.auth.services.sso_settings.get_encryption", return_value=mock_encryption):
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

        mock_current = {
            "id": 1,
            "provider": "google",
            "additional_config": {},
            "is_enabled": True,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }

        with patch("src.app.modules.auth.services.sso_settings.crud_sso_settings") as mock_crud:
            mock_crud.get = AsyncMock(return_value=mock_current)
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

        with patch("src.app.modules.auth.services.sso_settings.crud_sso_settings") as mock_crud:
            mock_crud.exists = AsyncMock(return_value=True)
            mock_crud.delete = AsyncMock()

            await service.delete_settings(mock_db, "google", user_id=1)

            mock_crud.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_settings_raises_not_found(self):
        """Verify ModelNotFoundError raised for missing provider."""
        from src.app.shared.exceptions import ModelNotFoundError

        service = SSOSettingsService()
        mock_db = AsyncMock()

        with patch("src.app.modules.auth.services.sso_settings.crud_sso_settings") as mock_crud:
            mock_crud.exists = AsyncMock(return_value=False)

            with pytest.raises(ModelNotFoundError):
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


# ============================================================================
# Batch provider reads (Phase 1.2)
# ============================================================================

PROVIDERS_UNDER_TEST = ("google", "github", "microsoft", "apple")


def _public_payload(provider: str, *, enabled: bool = True, row_id: int = 1) -> dict:
    """The PUBLIC projection the cache holds (no client id/secret)."""
    return {
        "id": row_id,
        "provider": provider,
        "is_enabled": enabled,
        "additional_config": {"hd": "example.com"},
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 1, 2, tzinfo=UTC),
    }


def _db_row(provider: str, *, enabled: bool = True, row_id: int = 2) -> dict:
    """A full DB row: it carries the encrypted secrets the read must project away."""
    return {
        "id": row_id,
        "provider": provider,
        "client_id": b"ENCRYPTED_CID",
        "client_secret": b"ENCRYPTED_SECRET",
        "is_enabled": enabled,
        "additional_config": {},
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 1, 2, tzinfo=UTC),
    }


class TestSSOSettingsServiceBatchRead:
    """``get_settings_by_providers`` is the batch form of the single read."""

    @staticmethod
    def _crud_get(**rows):
        """A ``crud.get`` that answers per provider (missing key -> None)."""

        async def _get(db, provider):
            return rows.get(provider)

        return _get

    @pytest.mark.asyncio
    async def test_all_hits_are_one_cache_round_trip_and_no_db_read(self):
        """The headline of 1.2: four providers, one MGET, zero queries."""
        from src.app.core.cache.client import cache_client

        service = SSOSettingsService()
        payloads = [_public_payload(provider) for provider in PROVIDERS_UNDER_TEST]
        db_get = AsyncMock()

        with (
            patch.object(cache_client, "get_json_many", new=AsyncMock(return_value=payloads)) as mget,
            patch("src.app.modules.auth.services.sso_settings.crud_sso_settings.get", new=db_get),
        ):
            result = await service.get_settings_by_providers(AsyncMock(), list(PROVIDERS_UNDER_TEST))

        assert list(result) == list(PROVIDERS_UNDER_TEST)
        assert all(settings.is_enabled for settings in result.values())
        mget.assert_awaited_once_with([f"sso_settings:{provider}" for provider in PROVIDERS_UNDER_TEST])
        db_get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mixed_hits_and_misses_only_read_the_missing_providers(self):
        """A miss costs a per-provider read; a hit costs nothing extra."""
        from src.app.core.cache.client import cache_client

        service = SSOSettingsService()
        payloads = [_public_payload("google"), None, _public_payload("microsoft"), None]
        db_get = AsyncMock(side_effect=self._crud_get(github=_db_row("github"), apple=_db_row("apple")))

        with (
            patch.object(cache_client, "get_json_many", new=AsyncMock(return_value=payloads)),
            patch("src.app.modules.auth.services.sso_settings.crud_sso_settings.get", new=db_get),
            patch.object(cache_client, "set_json", new=AsyncMock()),
        ):
            result = await service.get_settings_by_providers(AsyncMock(), list(PROVIDERS_UNDER_TEST))

        assert list(result) == list(PROVIDERS_UNDER_TEST), "provider order must survive the batch"
        assert [call.kwargs["provider"] for call in db_get.await_args_list] == ["github", "apple"]
        # Cache ids vs DB ids prove which source answered for each provider.
        assert result["google"].id == 1
        assert result["github"].id == 2

    @pytest.mark.asyncio
    async def test_a_cache_outage_falls_back_to_the_db_for_every_provider(self):
        """Read-ahead cache contract: an outage is a batch of misses, not an error."""
        from src.app.core.cache.client import cache_client

        service = SSOSettingsService()
        db_get = AsyncMock(side_effect=self._crud_get(**{p: _db_row(p) for p in PROVIDERS_UNDER_TEST}))

        with (
            patch.object(cache_client, "get_json_many", new=AsyncMock(return_value=[None] * 4)),
            patch("src.app.modules.auth.services.sso_settings.crud_sso_settings.get", new=db_get),
            patch.object(cache_client, "set_json", new=AsyncMock()),
        ):
            result = await service.get_settings_by_providers(AsyncMock(), list(PROVIDERS_UNDER_TEST))

        assert list(result) == list(PROVIDERS_UNDER_TEST)
        assert db_get.await_count == 4

    @pytest.mark.asyncio
    async def test_a_db_failure_is_not_swallowed(self):
        """Only a missing row is an expected outcome; a broken DB is not."""
        from src.app.core.cache.client import cache_client

        service = SSOSettingsService()
        db_get = AsyncMock(side_effect=RuntimeError("db down"))

        with (
            patch.object(cache_client, "get_json_many", new=AsyncMock(return_value=[None] * 4)),
            patch("src.app.modules.auth.services.sso_settings.crud_sso_settings.get", new=db_get),
        ):
            with pytest.raises(RuntimeError, match="db down"):
                await service.get_settings_by_providers(AsyncMock(), list(PROVIDERS_UNDER_TEST))

    @pytest.mark.asyncio
    async def test_an_unconfigured_provider_is_omitted(self):
        """The batch form of ``ModelNotFoundError``: absent, not empty settings."""
        from src.app.core.cache.client import cache_client

        service = SSOSettingsService()
        db_get = AsyncMock(side_effect=self._crud_get(**{p: _db_row(p) for p in PROVIDERS_UNDER_TEST[:-1]}))

        with (
            patch.object(cache_client, "get_json_many", new=AsyncMock(return_value=[None] * 4)),
            patch("src.app.modules.auth.services.sso_settings.crud_sso_settings.get", new=db_get),
            patch.object(cache_client, "set_json", new=AsyncMock()),
        ):
            result = await service.get_settings_by_providers(AsyncMock(), list(PROVIDERS_UNDER_TEST))

        assert list(result) == list(PROVIDERS_UNDER_TEST[:-1])
        assert "apple" not in result

    @pytest.mark.asyncio
    async def test_a_malformed_cached_payload_is_refetched(self):
        """A cached value the read model rejects is a MISS, same as the single read."""
        from src.app.core.cache.client import cache_client

        service = SSOSettingsService()
        payloads = [{"provider": "google"}, _public_payload("github")]  # missing id/created_at
        db_get = AsyncMock(side_effect=self._crud_get(google=_db_row("google")))

        with (
            patch.object(cache_client, "get_json_many", new=AsyncMock(return_value=payloads)),
            patch("src.app.modules.auth.services.sso_settings.crud_sso_settings.get", new=db_get),
            patch.object(cache_client, "set_json", new=AsyncMock()),
        ):
            result = await service.get_settings_by_providers(AsyncMock(), ["google", "github"])

        assert list(result) == ["google", "github"]
        assert result["google"].id == 2, "the malformed entry must come from the DB"
        assert result["github"].id == 1, "the valid entry must come from the cache"
        db_get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_an_empty_provider_list_reads_nothing(self):
        from src.app.core.cache.client import cache_client

        service = SSOSettingsService()
        db_get = AsyncMock()

        with (
            patch.object(cache_client, "get_json_many", new=AsyncMock(return_value=[])) as mget,
            patch("src.app.modules.auth.services.sso_settings.crud_sso_settings.get", new=db_get),
        ):
            result = await service.get_settings_by_providers(AsyncMock(), [])

        assert result == {}
        mget.assert_awaited_once_with([])
        db_get.assert_not_awaited()
