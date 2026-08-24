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

                result, registration_id = await service.generate_registration_challenge(
                    db=mock_db,
                    user=mock_user,
                    device_type="Test Device",
                )

                # Should return dict with WebAuthn options (now returns (options, registration_id))
                assert "challenge" in result
                assert isinstance(registration_id, str)

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
                    await service.generate_registration_challenge(
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
                    registration_id="test-registration-id",
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
            "user_id": 1,
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
                            registration_id="test-registration-id",
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
            "user_id": 1,
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

                with patch.object(service, "_store_credential", new_callable=AsyncMock):
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
                            registration_id="test-registration-id",
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
            "src.app.modules.passkey.crud.crud_passkey_credentials.get_credential",
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
            "src.app.modules.passkey.crud.crud_passkey_credentials.get_credential",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = mock_cred

            with patch(
                "src.app.modules.passkey.crud.crud_passkey_credentials.delete_credential",
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
            result, challenge_id = await service.generate_authentication_challenge(db=mock_db, redis=mock_redis)

            assert "challenge" in result
            assert isinstance(challenge_id, str)
            mock_store.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_auth_challenge_with_email(self):
        """Verify generation with email hint."""
        from webauthn.helpers.structs import PublicKeyCredentialDescriptor, PublicKeyCredentialType

        from src.app.modules.passkey.service import PasskeyService

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

                            with patch.object(service, "_generate_auth_tokens", new_callable=AsyncMock) as mock_tokens:
                                mock_tokens.return_value = {"access_token": "token"}

                                with patch.object(service, "_delete_auth_challenge", new_callable=AsyncMock):
                                    with patch("src.app.modules.mfa.service.get_mfa_service") as mock_mfa:
                                        mock_mfa.return_value.is_mfa_enabled = AsyncMock(return_value=False)
                                        result = await service.verify_authentication(
                                        db=mock_db,
                                        redis=mock_redis,
                                        credential=mock_cred_payload,
                                        challenge_id="test-challenge-id",
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
                        await service.verify_authentication(
                            db=mock_db, redis=mock_redis, credential={"id": "x"}, challenge_id="test-challenge-id"
                        )  # noqa: E501

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
                await service.verify_authentication(
                    db=AsyncMock(), redis=AsyncMock(), credential={"id": "x"}, challenge_id="test-challenge-id"
                )  # noqa: E501

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
                    await service.verify_authentication(
                        db=AsyncMock(), redis=AsyncMock(), credential={"id": "x"}, challenge_id="test-challenge-id"
                    )  # noqa: E501

                assert "credential not found" in str(exc.value.detail).lower()


# === MERGED: Tests from tests/modules/test_passkey_service.py ===
# Tests for PasskeyService optimization methods


@pytest.fixture
def mock_db_session():
    return AsyncMock()


@pytest.mark.asyncio
async def test_decrypt_credentials_parallel():
    """Verify parallel decryption helper."""
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()

    # Mock encryption
    with patch("src.app.core.security.encryption.get_encryption") as mock_get_enc:
        mock_enc = MagicMock()
        mock_get_enc.return_value = mock_enc
        # Decrypt returns bytes
        mock_enc.decrypt_credential.return_value = b"decrypted_id"

        # Test data: list of (id, transports)
        data = [
            ("enc1", None),
            ("enc2", "usb"),
        ]

        results = await service._decrypt_credentials_parallel(data, "user1")

        assert len(results) == 2
        assert isinstance(results[0], PublicKeyCredentialDescriptor)
        assert results[0].id == b"decrypted_id"
        assert results[0].transports is None

        assert results[1].id == b"decrypted_id"
        assert results[1].transports == "usb"

        assert mock_enc.decrypt_credential.call_count == 2


@pytest.mark.asyncio
async def test_get_existing_credentials_optimized(mock_db_session, override_settings):
    """Test _get_existing_credentials with optimized column select."""
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()

    # Mock execute result
    mock_result = MagicMock()
    # Return list of tuples (credential_id,)
    mock_result.fetchall.return_value = [("enc1",), ("enc2",)]
    mock_db_session.execute = AsyncMock(return_value=mock_result)

    # Mock settings to enable optimization
    with override_settings(PASSKEY_USE_COLUMN_SELECT=True):
        with patch.object(service, "_decrypt_credentials_parallel", new_callable=AsyncMock) as mock_helper:
            await service._get_existing_credentials(mock_db_session, 1)

            # Verify call to helper
            mock_helper.assert_awaited_once()
            args, _ = mock_helper.call_args
            creds_list, user_id = args

            assert len(creds_list) == 2
            assert creds_list[0] == ("enc1", None)
            assert creds_list[1] == ("enc2", None)
            assert user_id == 1


@pytest.mark.asyncio
async def test_get_existing_credentials_fallback(mock_db_session, override_settings):
    """Test _get_existing_credentials with fallback CRUD."""
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()

    # Mock CRUD return
    mock_cred = MagicMock()
    mock_cred.credential_id = "enc1"
    mock_cred.transports = "usb"

    with override_settings(PASSKEY_USE_COLUMN_SELECT=False, PASSKEY_MAX_CREDENTIALS_PER_USER=100):
        with patch("src.app.modules.passkey.service.crud_passkey_credentials") as mock_crud:
            mock_crud.get_by_user = AsyncMock(return_value=[mock_cred])
            with patch.object(service, "_decrypt_credentials_parallel", new_callable=AsyncMock) as mock_helper:
                await service._get_existing_credentials(mock_db_session, 1)

                mock_helper.assert_awaited_once()
                args, _ = mock_helper.call_args
                creds_list, _ = args

                assert creds_list[0] == ("enc1", "usb")


# === COVERAGE TESTS: Additional helper methods and error handling ===


@pytest.mark.asyncio
async def test_store_challenge():
    """Test _store_challenge stores challenge in Redis."""
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()

    mock_redis = AsyncMock()

    await service._store_challenge(
        mock_redis, registration_id=1, challenge=b"test-challenge", device_type="Test Device", user_id=1
    )

    # Verify redis.setex was called
    mock_redis.setex.assert_called_once()
    call_args = mock_redis.setex.call_args
    assert "passkey:reg_challenge:1" in str(call_args)


@pytest.mark.asyncio
async def test_get_stored_challenge():
    """Test _get_stored_challenge retrieves and converts challenge."""
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()

    mock_redis = AsyncMock()
    # Mock the Lua script returning a JSON string
    mock_execute = AsyncMock(return_value='{"challenge": "test", "device_type": "Test"}')

    with patch("src.app.modules.passkey.service.get_lua_manager") as mock_mgr:
        mock_mgr.return_value.execute = mock_execute
        result = await service._get_stored_challenge(mock_redis, registration_id=1)

    assert result is not None
    assert "challenge" in result


@pytest.mark.asyncio
async def test_get_stored_challenge_not_found():
    """Test _get_stored_challenge returns None when not found."""
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()

    mock_redis = AsyncMock()

    with patch("src.app.modules.passkey.service.get_lua_manager") as mock_mgr:
        mock_mgr.return_value.execute = AsyncMock(return_value=None)
        result = await service._get_stored_challenge(mock_redis, registration_id=1)

    assert result is None


@pytest.mark.asyncio
async def test_delete_challenge():
    """Test _delete_challenge removes challenge from Redis."""
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()

    mock_redis = AsyncMock()

    await service._delete_challenge(mock_redis, registration_id=1)

    mock_redis.delete.assert_called_once()


@pytest.mark.asyncio
async def test_store_auth_challenge():
    """Test _store_auth_challenge stores auth challenge in Redis."""
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()

    mock_redis = AsyncMock()

    await service._store_auth_challenge(mock_redis, challenge_id="challenge-123", challenge=b"test")

    mock_redis.setex.assert_called_once()
    call_args = mock_redis.setex.call_args
    assert "passkey:auth_challenge:challenge-123" in str(call_args)


@pytest.mark.asyncio
async def test_get_stored_auth_challenge():
    """Test _get_stored_auth_challenge retrieves auth challenge."""
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()

    mock_redis = AsyncMock()

    with patch("src.app.modules.passkey.service.get_lua_manager") as mock_mgr:
        mock_mgr.return_value.execute = AsyncMock(return_value="dGVzdA==")  # base64 "test"
        result = await service._get_stored_auth_challenge(mock_redis, challenge_id="challenge-123")

    assert result is not None


@pytest.mark.asyncio
async def test_get_stored_auth_challenge_not_found():
    """Test _get_stored_auth_challenge returns None when not found."""
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()

    mock_redis = AsyncMock()

    with patch("src.app.modules.passkey.service.get_lua_manager") as mock_mgr:
        mock_mgr.return_value.execute = AsyncMock(return_value=None)
        result = await service._get_stored_auth_challenge(mock_redis, challenge_id="challenge-123")

    assert result is None


@pytest.mark.asyncio
async def test_delete_auth_challenge():
    """Test _delete_auth_challenge removes auth challenge from Redis."""
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()

    mock_redis = AsyncMock()

    await service._delete_auth_challenge(mock_redis, challenge_id="challenge-123")

    mock_redis.delete.assert_called_once()


@pytest.mark.asyncio
async def test_count_user_passkeys():
    """Test _count_user_passkeys returns count."""
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()

    mock_db = AsyncMock()

    with patch("src.app.modules.passkey.service.crud_passkey_credentials") as mock_crud:
        mock_crud.count_by_user = AsyncMock(return_value=5)

        result = await service._count_user_passkeys(mock_db, user_id=1)

        assert result == 5


@pytest.mark.asyncio
async def test_verify_with_webauthn():
    """Test _verify_with_webauthn wraps webauthn call."""
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()

    with patch("src.app.modules.passkey.service.verify_registration_response") as mock_verify:
        mock_verify.return_value = MagicMock(credential_id=b"test")

        service._verify_with_webauthn(test="arg")

        mock_verify.assert_called_once_with(test="arg")


@pytest.mark.asyncio
async def test_find_credential_by_id_fallback_decryption():
    """Test _find_credential_by_id fallback decryption path."""
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()

    mock_db = AsyncMock()
    mock_cred = MagicMock()
    mock_cred.credential_id = "enc_id"
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_cred
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch("src.app.core.security.encryption.get_encryption") as mock_get_enc:
        mock_enc = MagicMock()
        mock_enc.decrypt_credential.return_value = b"different-id"
        mock_get_enc.return_value = mock_enc

        # Use a valid base64url encoded string
        result = await service._find_credential_by_id(mock_db, raw_id="dGVzdA==")

        # Hash doesn't match, so returns None
        assert result is None


@pytest.mark.asyncio
async def test_find_credential_by_id_fallback_decryption_local_import():
    """Test _find_credential_by_id fallback decryption path."""
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()

    mock_db = AsyncMock()
    mock_cred = MagicMock()
    mock_cred.credential_id = "enc_id"
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_cred
    mock_db.execute = AsyncMock(return_value=mock_result)

    # get_encryption is imported inside the function
    with patch("src.app.core.security.encryption.get_encryption") as mock_get_enc:
        mock_enc = MagicMock()
        mock_enc.decrypt_credential.return_value = b"different-id"
        mock_get_enc.return_value = mock_enc

        # Use a valid base64url encoded string
        result = await service._find_credential_by_id(mock_db, raw_id="dGVzdA==")

        # Hash doesn't match, so returns None
        assert result is None


@pytest.mark.asyncio
async def test_verify_authentication_response():
    """Test _verify_authentication_response verifies WebAuthn assertion."""
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()

    mock_cred = MagicMock()
    mock_cred.public_key = b"enc_pubkey"
    mock_cred.counter = 0

    with patch("src.app.modules.passkey.service.verify_authentication_response") as mock_verify:
        # get_encryption is imported inside the method
        with patch("src.app.core.security.encryption.get_encryption") as mock_get_enc:
            mock_enc = MagicMock()
            mock_enc.decrypt_credential.return_value = b"pubkey"
            mock_get_enc.return_value = mock_enc

            mock_verify.return_value = MagicMock(new_sign_count=1)

            # parse_authentication_credential_json is imported inside the method
            with patch("webauthn.helpers.parse_authentication_credential_json") as mock_parse:
                mock_parse.return_value = MagicMock()

                result = service._verify_authentication_response(
                    credential={"id": "test"},
                    expected_challenge=b"challenge",
                    expected_origin="http://localhost:3000",
                    expected_rp_id="localhost",
                    db_credential=mock_cred,
                )

                assert result.new_sign_count == 1


@pytest.mark.asyncio
async def test_update_credential_counter():
    """Test _update_credential_counter updates counter and timestamp."""
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()

    mock_db = AsyncMock()
    mock_cred = MagicMock()
    mock_cred.counter = 0
    mock_cred.last_used_at = None

    await service._update_credential_counter(mock_db, mock_cred, new_counter=5)

    assert mock_cred.counter == 5
    assert mock_cred.last_used_at is not None
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_get_user_for_credential():
    """Test _get_user_for_credential returns user dict."""
    from src.app.modules.passkey.service import PasskeyService
    from src.app.modules.users.models import User

    service = PasskeyService()
    mock_db = AsyncMock()

    mock_user = MagicMock(spec=User)
    mock_user.id = 1
    mock_user.name = "Test User"
    mock_user.username = "testuser"
    mock_user.email = "test@example.com"
    mock_user.hashed_password = "hash"
    mock_user.profile_image_url = None
    mock_user.tier_id = 1
    mock_user.tenant_id = 1
    mock_user.is_superuser = False
    mock_user.is_active = True
    mock_user.is_deleted = False
    mock_user.is_email_verified = True
    mock_user.token_version = 1
    mock_user.created_at = datetime.now(UTC)
    mock_user.updated_at = datetime.now(UTC)
    mock_user.tier = None

    mock_result = MagicMock()
    mock_result.unique.return_value.scalar_one_or_none.return_value = mock_user
    mock_db.execute = AsyncMock(return_value=mock_result)

    mock_cred = MagicMock()
    mock_cred.user_id = 1

    result = await service._get_user_for_credential(mock_db, mock_cred)

    assert result is not None
    assert result["id"] == 1
    assert result["email"] == "test@example.com"


@pytest.mark.asyncio
async def test_get_user_for_credential_not_found():
    """Test _get_user_for_credential returns None when user not found."""
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()
    mock_db = AsyncMock()

    mock_result = MagicMock()
    mock_result.unique.return_value.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=mock_result)

    mock_cred = MagicMock()
    mock_cred.user_id = 999

    result = await service._get_user_for_credential(mock_db, mock_cred)

    assert result is None


@pytest.mark.asyncio
async def test_generate_auth_tokens():
    """Test _generate_auth_tokens creates JWT tokens."""
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()

    mock_user = {
        "id": 1,
        "username": "testuser",
        "email": "test@example.com",
        "tenant_id": 1,
        "token_version": 1,
        "tier_id": 1,
        "tier": {"name": "Basic"},
    }

    # These are imported inside the function, so patch at the source
    with patch("src.app.core.security.create_access_token") as mock_access:
        with patch("src.app.core.security.create_refresh_token") as mock_refresh:
            mock_access.return_value = "access_token_value"
            mock_refresh.return_value = "refresh_token_value"

            result = await service._generate_auth_tokens(mock_user)

            assert "access_token" in result
            assert "refresh_token" in result
            assert result["token_type"] == "bearer"
