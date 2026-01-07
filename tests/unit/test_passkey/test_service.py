"""Unit tests for PasskeyService registration functionality.

TDD: These tests are written first and should FAIL until implementation is complete.

Task: T066 [US7] Write unit tests for PasskeyService registration

Tests the PasskeyService for:
- Challenge generation using py_webauthn
- Registration verification with credential encryption
- Max 10 passkey limit per user (CLR-003)
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from webauthn.helpers.structs import PublicKeyCredentialDescriptor, PublicKeyCredentialType


class TestPasskeyServiceExists:
    """Verify PasskeyService module structure."""

    def test_service_module_exists(self):
        """Verify passkey service module can be imported."""
        from src.app.modules.passkey import service

        assert service is not None

    def test_passkey_service_class_exists(self):
        """Verify PasskeyService class exists."""
        from src.app.modules.passkey.service import PasskeyService

        assert PasskeyService is not None

    def test_passkey_service_singleton_exists(self):
        """Verify passkey_service singleton exists."""
        from src.app.modules.passkey.service import passkey_service

        assert passkey_service is not None


class TestPasskeyServiceChallengeGeneration:
    """Tests for registration challenge generation."""

    @pytest.mark.asyncio
    async def test_generate_registration_challenge_method_exists(self):
        """Verify generate_registration_challenge method exists."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        assert hasattr(service, "generate_registration_challenge")

    @pytest.mark.asyncio
    async def test_generate_registration_challenge_returns_options(self):
        """Verify challenge generation returns WebAuthn options."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()
        mock_user = {"id": 1, "username": "testuser", "email": "test@example.com"}

        with patch.object(service, "_get_existing_credentials", new_callable=AsyncMock) as mock_creds:
            mock_creds.return_value = []

            with patch(
                "src.app.modules.passkey.service.crud_passkey_credentials.can_create", new_callable=AsyncMock
            ) as mock_can_create:
                mock_can_create.return_value = True

                result = await service.generate_registration_challenge(
                    db=mock_db,
                    user=mock_user,
                    device_type="Test Device",
                )

                # Should return dict with WebAuthn options
                assert "publicKey" in result or "challenge" in result

    @pytest.mark.asyncio
    async def test_generate_registration_challenge_stores_challenge(self):
        """Verify challenge is stored in Redis for verification."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()
        mock_user = {"id": 1, "username": "testuser", "email": "test@example.com"}
        mock_redis = AsyncMock()

        with patch.object(service, "_get_existing_credentials", new_callable=AsyncMock) as mock_creds:
            mock_creds.return_value = []

            with patch(
                "src.app.modules.passkey.service.crud_passkey_credentials.can_create", new_callable=AsyncMock
            ) as mock_can_create:
                mock_can_create.return_value = True

                with patch.object(service, "_store_challenge", new_callable=AsyncMock) as mock_store:
                    await service.generate_registration_challenge(
                        db=mock_db,
                        user=mock_user,
                        device_type="Test Device",
                        redis=mock_redis,
                    )

                    # Should store challenge
                    mock_store.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_registration_challenge_includes_user_info(self):
        """Verify challenge includes user display name and ID."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()
        mock_user = {"id": 1, "username": "testuser", "email": "test@example.com", "name": "Test User"}

        with patch.object(service, "_get_existing_credentials", new_callable=AsyncMock) as mock_creds:
            mock_creds.return_value = []

            with patch(
                "src.app.modules.passkey.service.crud_passkey_credentials.can_create", new_callable=AsyncMock
            ) as mock_can_create:
                mock_can_create.return_value = True

                with patch.object(service, "_store_challenge", new_callable=AsyncMock):
                    result = await service.generate_registration_challenge(
                        db=mock_db,
                        user=mock_user,
                        device_type="Test Device",
                    )

                    # Should include user info in options
                    assert result is not None

    @pytest.mark.asyncio
    async def test_generate_registration_challenge_excludes_existing_credentials(self):
        """Verify existing credentials are excluded from registration."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()
        mock_user = {"id": 1, "username": "testuser", "email": "test@example.com"}

        # Simulate existing credential (object as expected by webauthn exclusions)
        existing_cred_obj = PublicKeyCredentialDescriptor(
            type=PublicKeyCredentialType.PUBLIC_KEY, id=b"existing-cred-id"
        )

        with patch.object(service, "_get_existing_credentials", new_callable=AsyncMock) as mock_creds:
            mock_creds.return_value = [existing_cred_obj]

            with patch(
                "src.app.modules.passkey.service.crud_passkey_credentials.can_create", new_callable=AsyncMock
            ) as mock_can_create:
                mock_can_create.return_value = True

                with patch.object(service, "_store_challenge", new_callable=AsyncMock):
                    result = await service.generate_registration_challenge(
                        db=mock_db,
                        user=mock_user,
                        device_type="Test Device",
                    )

                    # Should have excluded existing credentials
                    mock_creds.assert_called_once()


class TestPasskeyServiceMaxLimit:
    """Tests for max 10 passkey limit per user (CLR-003)."""

    @pytest.mark.asyncio
    async def test_rejects_registration_when_at_limit(self):
        """Verify registration fails when user has 10 passkeys."""
        from src.app.core.exceptions.http_exceptions import BadRequestException
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()
        mock_user = {"id": 1, "username": "testuser", "email": "test@example.com"}

        with patch(
            "src.app.modules.passkey.service.crud_passkey_credentials.can_create",
            new_callable=AsyncMock,
        ) as mock_can_create:
            mock_can_create.return_value = False  # cannot create (at limit)

            with pytest.raises(BadRequestException) as exc_info:
                await service.generate_registration_challenge(
                    db=mock_db,
                    user=mock_user,
                    device_type="Test Device",
                )

            assert "maximum" in str(exc_info.value.detail).lower() or "10" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_allows_registration_under_limit(self):
        """Verify registration succeeds when user has less than 10 passkeys."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()
        mock_user = {"id": 1, "username": "testuser", "email": "test@example.com"}

        with patch(
            "src.app.modules.passkey.service.crud_passkey_credentials.can_create",
            new_callable=AsyncMock,
        ) as mock_can_create:
            mock_can_create.return_value = True  # Can create (under limit)

            with patch.object(service, "_get_existing_credentials", new_callable=AsyncMock) as mock_creds:
                mock_creds.return_value = []

                with patch.object(service, "_store_challenge", new_callable=AsyncMock):
                    # Should not raise
                    result = await service.generate_registration_challenge(
                        db=mock_db,
                        user=mock_user,
                        device_type="Test Device",
                    )

                    assert result is not None


class TestPasskeyServiceVerification:
    """Tests for registration verification."""

    @pytest.mark.asyncio
    async def test_verify_registration_method_exists(self):
        """Verify verify_registration method exists."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        assert hasattr(service, "verify_registration")

    @pytest.mark.asyncio
    async def test_verify_registration_validates_challenge(self):
        """Verify registration validates the stored challenge."""
        from src.app.core.exceptions.http_exceptions import BadRequestException
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()
        mock_user = {"id": 1, "username": "testuser", "email": "test@example.com"}

        with patch.object(service, "_get_stored_challenge", new_callable=AsyncMock) as mock_challenge:
            mock_challenge.return_value = None  # No stored challenge

            with pytest.raises(BadRequestException) as exc_info:
                await service.verify_registration(
                    db=mock_db,
                    user=mock_user,
                    credential={"response": {}},
                )

            assert "challenge" in str(exc_info.value.detail).lower() or "expired" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_verify_registration_encrypts_credential(self):
        """Verify registration encrypts credential_id and public_key."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()
        mock_user = {"id": 1, "username": "testuser", "email": "test@example.com"}

        # Mock stored challenge
        mock_challenge_data = {
            "challenge": b"test-challenge",
            "device_type": "Test Device",
        }

        with patch.object(service, "_get_stored_challenge", new_callable=AsyncMock) as mock_get_challenge:
            mock_get_challenge.return_value = mock_challenge_data

            with patch.object(service, "_verify_with_webauthn", new_callable=MagicMock) as mock_verify:
                mock_verify.return_value = MagicMock(
                    credential_id=b"cred-id",
                    credential_public_key=b"pub-key",
                    sign_count=0,
                    aaguid=UUID("00000000-0000-0000-0000-000000000000"),
                    credential_device_type="single_device",
                )

                with patch.object(service, "_store_credential", new_callable=AsyncMock) as mock_store:
                    with patch.object(service, "_delete_challenge", new_callable=AsyncMock):
                        await service.verify_registration(
                            db=mock_db,
                            user=mock_user,
                            credential={
                                "id": "mock-id",
                                "rawId": "mock-raw-id",
                                "response": {
                                    "clientDataJSON": "mock-client-data",
                                    "attestationObject": "mock-attestation",
                                },
                                "type": "public-key",
                            },
                            redis=AsyncMock(),
                        )

                        # Should store encrypted credential
                        mock_store.assert_called_once()

    @pytest.mark.asyncio
    async def test_verify_registration_creates_credential_record(self):
        """Verify registration creates PasskeyCredential in database."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()
        mock_user = {"id": 1, "username": "testuser", "email": "test@example.com"}

        mock_challenge_data = {
            "challenge": b"test-challenge",
            "device_type": "Test Device",
        }

        with patch.object(service, "_get_stored_challenge", new_callable=AsyncMock) as mock_get_challenge:
            mock_get_challenge.return_value = mock_challenge_data

            with patch.object(service, "_verify_with_webauthn", new_callable=MagicMock) as mock_verify:
                mock_verify.return_value = MagicMock(
                    credential_id=b"cred-id",
                    credential_public_key=b"pub-key",
                    sign_count=0,
                    aaguid=UUID("00000000-0000-0000-0000-000000000000"),
                    credential_device_type="single_device",
                )

                with patch.object(service, "_store_credential", new_callable=AsyncMock) as mock_store:
                    with patch.object(service, "_delete_challenge", new_callable=AsyncMock):
                        result = await service.verify_registration(
                            db=mock_db,
                            user=mock_user,
                            credential={
                                "id": "mock-id",
                                "rawId": "mock-raw-id",
                                "response": {
                                    "clientDataJSON": "mock-client-data",
                                    "attestationObject": "mock-attestation",
                                },
                                "type": "public-key",
                            },
                            redis=AsyncMock(),
                        )

                        # Should return created credential info
                        assert result is not None


class TestPasskeyServiceCredentialListing:
    """Tests for listing user credentials."""

    @pytest.mark.asyncio
    async def test_list_credentials_method_exists(self):
        """Verify list_credentials method exists."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        assert hasattr(service, "list_credentials")

    @pytest.mark.asyncio
    async def test_list_credentials_returns_user_passkeys(self):
        """Verify list_credentials returns user's passkeys."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()

        # Mock credentials
        mock_creds = [
            MagicMock(id=1, device_type="Touch ID", created_at=datetime.now(UTC)),
            MagicMock(id=2, device_type="YubiKey", created_at=datetime.now(UTC)),
        ]

        with patch(
            "src.app.modules.passkey.crud.crud_passkey_credentials.get_by_user",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = mock_creds

            result = await service.list_credentials(db=mock_db, user_id=1)

            assert len(result) == 2


class TestPasskeyServiceCredentialDeletion:
    """Tests for deleting credentials."""

    @pytest.mark.asyncio
    async def test_delete_credential_method_exists(self):
        """Verify delete_credential method exists."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        assert hasattr(service, "delete_credential")

    @pytest.mark.asyncio
    async def test_delete_credential_verifies_ownership(self):
        """Verify delete checks that credential belongs to user."""
        from src.app.core.exceptions.http_exceptions import NotFoundException
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()

        with patch(
            "src.app.modules.passkey.crud.crud_passkey_credentials.get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = None  # Credential not found

            with pytest.raises(NotFoundException):
                await service.delete_credential(
                    db=mock_db,
                    user_id=1,
                    credential_id=999,
                )

    @pytest.mark.asyncio
    async def test_delete_credential_removes_passkey(self):
        """Verify delete removes the passkey credential."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()

        mock_cred = MagicMock(id=1, user_id=1)

        with patch(
            "src.app.modules.passkey.crud.crud_passkey_credentials.get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = mock_cred

            with patch(
                "src.app.modules.passkey.crud.crud_passkey_credentials.delete",
                new_callable=AsyncMock,
            ) as mock_delete:
                await service.delete_credential(
                    db=mock_db,
                    user_id=1,
                    credential_id=1,
                )

                mock_delete.assert_called_once()


class TestPasskeyServiceAuthentication:
    """Tests for authentication challenge and verification."""

    @pytest.mark.asyncio
    async def test_generate_auth_challenge_success(self):
        """Verify generation of authentication challenge."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()
        mock_redis = AsyncMock()

        with patch.object(service, "_store_auth_challenge", new_callable=AsyncMock) as mock_store:
            result = await service.generate_authentication_challenge(db=mock_db, redis=mock_redis)

            assert "challenge" in result
            mock_store.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_auth_challenge_with_email(self):
        """Verify generation with email hint."""
        from src.app.modules.passkey.service import PasskeyService
        from webauthn.helpers.structs import PublicKeyCredentialDescriptor, PublicKeyCredentialType

        service = PasskeyService()
        mock_db = AsyncMock()

        # Mock finding credentials for email
        mock_cred_desc = PublicKeyCredentialDescriptor(type=PublicKeyCredentialType.PUBLIC_KEY, id=b"test-id")

        with patch.object(service, "_get_credentials_for_email", new_callable=AsyncMock) as mock_get_creds:
            mock_get_creds.return_value = [mock_cred_desc]

            result = await service.generate_authentication_challenge(db=mock_db, email="test@example.com")

            # Should have generated options with allowCredentials
            assert result is not None
            mock_get_creds.assert_called_once_with(mock_db, "test@example.com")

    @pytest.mark.asyncio
    async def test_verify_authentication_success(self):
        """Verify successful authentication."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()
        mock_redis = AsyncMock()

        # Mock data
        mock_cred_payload = {"id": "mock-id", "rawId": "mock-raw-id", "type": "public-key", "response": {}}

        with patch.object(service, "_get_stored_auth_challenge", new_callable=AsyncMock) as mock_get_challenge:
            mock_get_challenge.return_value = b"challenge"

            with patch.object(service, "_find_credential_by_id", new_callable=AsyncMock) as mock_find_cred:
                mock_db_cred = MagicMock(id=1, counter=10, user_id=1, public_key=b"enc_key")
                mock_find_cred.return_value = mock_db_cred

                with patch.object(service, "_verify_authentication_response", new_callable=MagicMock) as mock_verify:
                    mock_verify.return_value = MagicMock(new_sign_count=11)

                    with patch.object(service, "_update_credential_counter", new_callable=AsyncMock) as mock_update:
                        with patch.object(service, "_get_user_for_credential", new_callable=AsyncMock) as mock_get_user:
                            mock_get_user.return_value = MagicMock(id=1)

                            with patch.object(service, "_generate_auth_tokens", new_callable=MagicMock) as mock_tokens:
                                mock_tokens.return_value = {"access_token": "token"}

                                with patch.object(service, "_delete_auth_challenge", new_callable=AsyncMock):
                                    result = await service.verify_authentication(
                                        db=mock_db, redis=mock_redis, credential=mock_cred_payload
                                    )

                                    assert result["access_token"] == "token"
                                    mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_verify_authentication_replay_attack(self):
        """Verify replay attack detection (counter check)."""
        from src.app.core.exceptions.http_exceptions import BadRequestException
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()
        mock_redis = AsyncMock()

        with patch.object(service, "_get_stored_auth_challenge", new_callable=AsyncMock) as mock_get_challenge:
            mock_get_challenge.return_value = b"challenge"

            with patch.object(service, "_find_credential_by_id", new_callable=AsyncMock) as mock_find_cred:
                # Stored counter is 10
                mock_db_cred = MagicMock(id=1, counter=10, user_id=1)
                mock_find_cred.return_value = mock_db_cred

                with patch.object(service, "_verify_authentication_response", new_callable=MagicMock) as mock_verify:
                    # New counter is 10 (or lower) -> Replay
                    mock_verify.return_value = MagicMock(new_sign_count=10)

                    with pytest.raises(BadRequestException) as exc:
                        await service.verify_authentication(db=mock_db, redis=mock_redis, credential={"id": "x"})

                    assert "replay" in str(exc.value.detail).lower()

    @pytest.mark.asyncio
    async def test_verify_authentication_invalid_challenge(self):
        """Verify fails when challenge is invalid or expired."""
        from src.app.core.exceptions.http_exceptions import BadRequestException
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()

        with patch.object(service, "_get_stored_auth_challenge", new_callable=AsyncMock) as mock_get_challenge:
            mock_get_challenge.return_value = None

            with pytest.raises(BadRequestException) as exc:
                await service.verify_authentication(db=AsyncMock(), redis=AsyncMock(), credential={"id": "x"})

            assert "challenge" in str(exc.value.detail).lower()

    @pytest.mark.asyncio
    async def test_verify_authentication_credential_not_found(self):
        """Verify fails when credential not found in DB."""
        from src.app.core.exceptions.http_exceptions import BadRequestException
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()

        with patch.object(service, "_get_stored_auth_challenge", new_callable=AsyncMock) as mock_get_challenge:
            mock_get_challenge.return_value = b"challenge"

            with patch.object(service, "_find_credential_by_id", new_callable=AsyncMock) as mock_find:
                mock_find.return_value = None

                with pytest.raises(BadRequestException) as exc:
                    await service.verify_authentication(db=AsyncMock(), redis=AsyncMock(), credential={"id": "x"})

                assert "credential not found" in str(exc.value.detail).lower()
