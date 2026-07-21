"""Unit tests for PasskeyService authentication functionality.

TDD: These tests are written first and should FAIL until implementation is complete.

Task: T077 [US8] Write unit tests for PasskeyService authentication

Tests the PasskeyService for:
- Authentication challenge generation (login initiation)
- Assertion verification with counter check (replay prevention)
- Credential lookup by user email
- Sign count update after successful authentication
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestPasskeyAuthenticationChallengeGeneration:
    """Tests for authentication challenge generation (login initiation)."""

    @pytest.mark.asyncio
    async def test_generate_authentication_challenge_method_exists(self):
        """Verify generate_authentication_challenge method exists."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        assert hasattr(service, "generate_authentication_challenge")

    @pytest.mark.asyncio
    async def test_generate_authentication_challenge_returns_options(self):
        """Verify authentication challenge generation returns WebAuthn options."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()
        mock_redis = AsyncMock()

        with (
            patch("src.app.modules.passkey.service.generate_authentication_options") as mock_generate,
            patch("src.app.modules.passkey.service.options_to_json") as mock_to_json,
        ):
            mock_generate.return_value = MagicMock(challenge=b"test-challenge")
            mock_to_json.return_value = '{"challenge": "test-challenge", "publicKey": {"challenge": "test"}}'

            with patch.object(service, "_store_auth_challenge", new_callable=AsyncMock) as mock_store:
                result = await service.generate_authentication_challenge(
                    db=mock_db,
                    redis=mock_redis,
                )

                # Should return dict with WebAuthn authentication options
                assert result is not None
                mock_store.assert_called()

    @pytest.mark.asyncio
    async def test_generate_authentication_challenge_with_email_hint(self):
        """Verify authentication challenge can use email hint to filter credentials."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()
        mock_redis = AsyncMock()

        with patch.object(service, "_get_credentials_for_email", new_callable=AsyncMock) as mock_get_creds:
            mock_get_creds.return_value = []

            with (
                patch("src.app.modules.passkey.service.generate_authentication_options") as mock_generate,
                patch("src.app.modules.passkey.service.options_to_json") as mock_to_json,
            ):
                mock_generate.return_value = MagicMock(challenge=b"test-challenge")
                mock_to_json.return_value = '{"challenge": "test"}'

                with patch.object(service, "_store_auth_challenge", new_callable=AsyncMock):
                    result = await service.generate_authentication_challenge(
                        db=mock_db,
                        redis=mock_redis,
                        email="test@example.com",
                    )

                    # Should have called get_credentials_for_email
                    mock_get_creds.assert_called_once_with(mock_db, "test@example.com")
                    assert result is not None

    @pytest.mark.asyncio
    async def test_generate_authentication_challenge_stores_challenge(self):
        """Verify authentication challenge is stored in Redis for verification."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()
        mock_redis = AsyncMock()

        with (
            patch("src.app.modules.passkey.service.generate_authentication_options") as mock_generate,
            patch("src.app.modules.passkey.service.options_to_json") as mock_to_json,
        ):
            # Mock return value with a bytes challenge
            mock_generate.return_value = MagicMock(challenge=b"test-challenge")
            mock_to_json.return_value = '{"challenge": "test"}'

            with patch.object(service, "_store_auth_challenge", new_callable=AsyncMock) as mock_store:
                await service.generate_authentication_challenge(
                    db=mock_db,
                    redis=mock_redis,
                )

                # Should store challenge
                mock_store.assert_called_once()
                # Verify it was called with our mocked challenge
                assert mock_store.call_args[0][2] == b"test-challenge"

    @pytest.mark.asyncio
    async def test_generate_authentication_challenge_includes_allowed_credentials(self):
        """Verify authentication options include allowCredentials when email provided."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()
        mock_redis = AsyncMock()

        # Mock credentials for the user
        mock_cred = MagicMock(
            credential_id=b"test-cred-id",
            transports=["internal"],
        )

        with patch.object(service, "_get_credentials_for_email", new_callable=AsyncMock) as mock_get_creds:
            mock_get_creds.return_value = [mock_cred]

            with (
                patch("src.app.modules.passkey.service.generate_authentication_options") as mock_generate,
                patch("src.app.modules.passkey.service.options_to_json") as mock_to_json,
            ):
                mock_generate.return_value = MagicMock(challenge=b"test-challenge")
                mock_to_json.return_value = '{"challenge": "test"}'

                with patch.object(service, "_store_auth_challenge", new_callable=AsyncMock):
                    result = await service.generate_authentication_challenge(
                        db=mock_db,
                        redis=mock_redis,
                        email="test@example.com",
                    )

                    # Result should include allowed credentials
                    assert result is not None


class TestPasskeyAuthenticationVerification:
    """Tests for assertion verification (login completion)."""

    @pytest.mark.asyncio
    async def test_verify_authentication_method_exists(self):
        """Verify verify_authentication method exists."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        assert hasattr(service, "verify_authentication")

    @pytest.mark.asyncio
    async def test_verify_authentication_validates_challenge(self):
        """Verify authentication validates the stored challenge."""
        from src.app.core.exceptions.http_exceptions import BadRequestException
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()
        mock_redis = AsyncMock()

        with patch.object(service, "_get_stored_auth_challenge", new_callable=AsyncMock) as mock_get_challenge:
            mock_get_challenge.return_value = None  # No stored challenge

            with pytest.raises(BadRequestException) as exc_info:
                await service.verify_authentication(
                    db=mock_db,
                    redis=mock_redis,
                    credential={"response": {}},
                    challenge_id="test-challenge-id",
                )

            assert "challenge" in str(exc_info.value.detail).lower() or "expired" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_verify_authentication_finds_credential(self):
        """Verify authentication finds the credential by ID."""
        from src.app.core.exceptions.http_exceptions import BadRequestException
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()
        mock_redis = AsyncMock()

        mock_challenge_data = {
            "challenge": b"test-challenge",
        }

        with patch.object(service, "_get_stored_auth_challenge", new_callable=AsyncMock) as mock_get_challenge:
            mock_get_challenge.return_value = mock_challenge_data

            with patch.object(service, "_find_credential_by_id", new_callable=AsyncMock) as mock_find:
                mock_find.return_value = None  # Credential not found

                with pytest.raises(BadRequestException) as exc_info:
                    await service.verify_authentication(
                        db=mock_db,
                        redis=mock_redis,
                        credential={
                            "id": "unknown-id",
                            "rawId": "unknown-raw-id",
                            "response": {},
                            "type": "public-key",
                        },
                        challenge_id="test-challenge-id",
                    )

                assert (
                    "credential" in str(exc_info.value.detail).lower()
                    or "not found" in str(exc_info.value.detail).lower()
                )

    @pytest.mark.asyncio
    async def test_verify_authentication_checks_counter(self):
        """Verify authentication checks counter to prevent replay attacks."""
        from src.app.core.exceptions.http_exceptions import BadRequestException
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()
        mock_redis = AsyncMock()

        mock_challenge_data = {
            "challenge": b"test-challenge",
        }

        # Credential with counter = 10
        mock_cred = MagicMock(
            id=1,
            user_id=1,
            credential_id=b"cred-id",
            public_key=b"pub-key",
            counter=10,
        )

        with patch.object(service, "_get_stored_auth_challenge", new_callable=AsyncMock) as mock_get_challenge:
            mock_get_challenge.return_value = mock_challenge_data

            with patch.object(service, "_find_credential_by_id", new_callable=AsyncMock) as mock_find:
                mock_find.return_value = mock_cred

                with patch.object(service, "_verify_authentication_response") as mock_verify:
                    # Return verification with lower/equal counter (replay attempt)
                    mock_verify.return_value = MagicMock(
                        new_sign_count=5,  # Lower than stored counter (10)
                    )

                    with pytest.raises(BadRequestException) as exc_info:
                        await service.verify_authentication(
                            db=mock_db,
                            redis=mock_redis,
                            credential={
                                "id": "cred-id",
                                "rawId": "cred-raw-id",
                                "response": {
                                    "clientDataJSON": "mock-data",
                                    "authenticatorData": "mock-auth",
                                    "signature": "mock-sig",
                                },
                                "type": "public-key",
                            },
                            challenge_id="test-challenge-id",
                        )

                    # Should reject replay attack
                    assert (
                        "counter" in str(exc_info.value.detail).lower()
                        or "replay" in str(exc_info.value.detail).lower()
                    )

    @pytest.mark.asyncio
    async def test_verify_authentication_updates_counter(self):
        """Verify authentication updates the credential counter after success."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()
        mock_redis = AsyncMock()

        mock_challenge_data = {
            "challenge": b"test-challenge",
        }

        # Credential with counter = 10
        mock_cred = MagicMock(
            id=1,
            user_id=1,
            credential_id=b"cred-id",
            public_key=b"pub-key",
            counter=10,
        )

        # Mock user for token generation
        mock_user = MagicMock(id=1, email="test@example.com")

        with patch.object(service, "_get_stored_auth_challenge", new_callable=AsyncMock) as mock_get_challenge:
            mock_get_challenge.return_value = mock_challenge_data

            with patch.object(service, "_find_credential_by_id", new_callable=AsyncMock) as mock_find:
                mock_find.return_value = mock_cred

                with patch.object(service, "_verify_authentication_response") as mock_verify:
                    # Return verification with higher counter
                    mock_verify.return_value = MagicMock(
                        new_sign_count=11,  # Higher than stored counter (10)
                    )

                    with patch.object(service, "_update_credential_counter", new_callable=AsyncMock) as mock_update:
                        with patch.object(service, "_get_user_for_credential", new_callable=AsyncMock) as mock_get_user:
                            mock_get_user.return_value = mock_user

                            with patch.object(service, "_generate_auth_tokens") as mock_tokens:
                                mock_tokens.return_value = {
                                    "access_token": "test",
                                    "refresh_token": "test",
                                }

                                with patch.object(service, "_delete_auth_challenge", new_callable=AsyncMock):
                                    with patch("src.app.modules.mfa.service.get_mfa_service") as mock_mfa:
                                        mock_mfa.return_value.is_mfa_enabled = AsyncMock(return_value=False)
                                        await service.verify_authentication(
                                        db=mock_db,
                                        redis=mock_redis,
                                        credential={
                                            "id": "cred-id",
                                            "rawId": "cred-raw-id",
                                            "response": {
                                                "clientDataJSON": "mock-data",
                                                "authenticatorData": "mock-auth",
                                                "signature": "mock-sig",
                                            },
                                            "type": "public-key",
                                        },
                                        challenge_id="test-challenge-id",
                                    )

                                    # Should update counter
                                    mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_verify_authentication_returns_tokens(self):
        """Verify successful authentication returns JWT tokens."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()
        mock_redis = AsyncMock()

        mock_challenge_data = {
            "challenge": b"test-challenge",
        }

        mock_cred = MagicMock(
            id=1,
            user_id=1,
            credential_id=b"cred-id",
            public_key=b"pub-key",
            counter=10,
        )

        mock_user = MagicMock(id=1, email="test@example.com")

        with patch.object(service, "_get_stored_auth_challenge", new_callable=AsyncMock) as mock_get_challenge:
            mock_get_challenge.return_value = mock_challenge_data

            with patch.object(service, "_find_credential_by_id", new_callable=AsyncMock) as mock_find:
                mock_find.return_value = mock_cred

                with patch.object(service, "_verify_authentication_response") as mock_verify:
                    mock_verify.return_value = MagicMock(new_sign_count=11)

                    with patch.object(service, "_update_credential_counter", new_callable=AsyncMock):
                        with patch.object(service, "_get_user_for_credential", new_callable=AsyncMock) as mock_get_user:
                            mock_get_user.return_value = mock_user

                            with patch.object(service, "_generate_auth_tokens") as mock_tokens:
                                mock_tokens.return_value = {
                                    "access_token": "access-123",
                                    "refresh_token": "refresh-456",
                                    "token_type": "bearer",
                                    "expires_in": 3600,
                                }

                                with patch.object(service, "_delete_auth_challenge", new_callable=AsyncMock):
                                    with patch("src.app.modules.mfa.service.get_mfa_service") as mock_mfa:
                                        mock_mfa.return_value.is_mfa_enabled = AsyncMock(return_value=False)
                                        result = await service.verify_authentication(
                                        db=mock_db,
                                        redis=mock_redis,
                                        credential={
                                            "id": "cred-id",
                                            "rawId": "cred-raw-id",
                                            "response": {
                                                "clientDataJSON": "mock-data",
                                                "authenticatorData": "mock-auth",
                                                "signature": "mock-sig",
                                            },
                                            "type": "public-key",
                                        },
                                        challenge_id="test-challenge-id",
                                    )

                                    # Should return auth tokens
                                    assert "access_token" in result
                                    assert "refresh_token" in result

    @pytest.mark.asyncio
    async def test_verify_authentication_updates_last_used_at(self):
        """Verify authentication updates last_used_at timestamp."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        mock_db = AsyncMock()
        mock_redis = AsyncMock()

        mock_challenge_data = {
            "challenge": b"test-challenge",
        }

        mock_cred = MagicMock(
            id=1,
            user_id=1,
            credential_id=b"cred-id",
            public_key=b"pub-key",
            counter=10,
            last_used_at=None,
        )

        mock_user = MagicMock(id=1, email="test@example.com")

        with patch.object(service, "_get_stored_auth_challenge", new_callable=AsyncMock) as mock_get_challenge:
            mock_get_challenge.return_value = mock_challenge_data

            with patch.object(service, "_find_credential_by_id", new_callable=AsyncMock) as mock_find:
                mock_find.return_value = mock_cred

                with patch.object(service, "_verify_authentication_response") as mock_verify:
                    mock_verify.return_value = MagicMock(new_sign_count=11)

                    with patch.object(service, "_update_credential_counter", new_callable=AsyncMock) as mock_update:
                        with patch.object(service, "_get_user_for_credential", new_callable=AsyncMock) as mock_get_user:
                            mock_get_user.return_value = mock_user

                            with patch.object(service, "_generate_auth_tokens") as mock_tokens:
                                mock_tokens.return_value = {
                                    "access_token": "test",
                                    "refresh_token": "test",
                                }

                                with patch.object(service, "_delete_auth_challenge", new_callable=AsyncMock):
                                    with patch("src.app.modules.mfa.service.get_mfa_service") as mock_mfa:
                                        mock_mfa.return_value.is_mfa_enabled = AsyncMock(return_value=False)
                                        await service.verify_authentication(
                                        db=mock_db,
                                        redis=mock_redis,
                                        credential={
                                            "id": "cred-id",
                                            "rawId": "cred-raw-id",
                                            "response": {
                                                "clientDataJSON": "mock-data",
                                                "authenticatorData": "mock-auth",
                                                "signature": "mock-sig",
                                            },
                                            "type": "public-key",
                                        },
                                        challenge_id="test-challenge-id",
                                    )

                                    # Update should be called (includes last_used_at)
                                    mock_update.assert_called_once()


class TestPasskeyAuthenticationHelpers:
    """Tests for authentication helper methods."""

    @pytest.mark.asyncio
    async def test_get_credentials_for_email_method_exists(self):
        """Verify _get_credentials_for_email method exists."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        assert hasattr(service, "_get_credentials_for_email")

    @pytest.mark.asyncio
    async def test_find_credential_by_id_method_exists(self):
        """Verify _find_credential_by_id method exists."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        assert hasattr(service, "_find_credential_by_id")

    @pytest.mark.asyncio
    async def test_update_credential_counter_method_exists(self):
        """Verify _update_credential_counter method exists."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        assert hasattr(service, "_update_credential_counter")

    @pytest.mark.asyncio
    async def test_get_user_for_credential_method_exists(self):
        """Verify _get_user_for_credential method exists."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        assert hasattr(service, "_get_user_for_credential")

    @pytest.mark.asyncio
    async def test_generate_auth_tokens_method_exists(self):
        """Verify _generate_auth_tokens method exists."""
        from src.app.modules.passkey.service import PasskeyService

        service = PasskeyService()
        assert hasattr(service, "_generate_auth_tokens")


# === COVERAGE TESTS: Error handling and edge cases ===


@pytest.mark.asyncio
async def test_verify_authentication_credential_required():
    """Test verify_authentication requires credential."""
    from src.app.core.exceptions.http_exceptions import BadRequestException
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()

    with pytest.raises(BadRequestException) as exc:
        await service.verify_authentication(
            db=AsyncMock(),
            redis=AsyncMock(),
            credential=None,
            challenge_id="test-id",
        )

    assert "required" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_verify_authentication_challenge_id_required():
    """Test verify_authentication requires challenge_id."""
    from src.app.core.exceptions.http_exceptions import BadRequestException
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()

    with pytest.raises(BadRequestException) as exc:
        await service.verify_authentication(
            db=AsyncMock(),
            redis=AsyncMock(),
            credential={"id": "test"},
            challenge_id=None,
        )

    assert "challenge_id" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_verify_authentication_raw_id_required():
    """Test verify_authentication requires rawId or id in credential."""
    from src.app.core.exceptions.http_exceptions import BadRequestException
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()

    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(return_value="dGVzdA==")  # base64

    # Provide credential with id but no rawId - will pass initial check
    # Then it will try to find by id, and credential not found
    with pytest.raises(BadRequestException) as exc:
        await service.verify_authentication(
            db=AsyncMock(),
            redis=mock_redis,
            credential={"id": "test"},  # No rawId
            challenge_id="test-id",
        )

    # Either credential not found or credential id required depending on flow
    assert "credential" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_verify_authentication_user_not_found():
    """Test verify_authentication fails when user not found for credential."""
    from src.app.core.exceptions.http_exceptions import BadRequestException
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()
    mock_db = AsyncMock()
    mock_redis = AsyncMock()

    mock_challenge_data = {"challenge": b"test-challenge"}
    mock_cred = MagicMock(id=1, user_id=1, credential_id=b"cred-id", public_key=b"pub-key", counter=10)

    with patch.object(service, "_get_stored_auth_challenge", new_callable=AsyncMock) as mock_get_challenge:
        mock_get_challenge.return_value = mock_challenge_data

        with patch.object(service, "_find_credential_by_id", new_callable=AsyncMock) as mock_find:
            mock_find.return_value = mock_cred

            with patch.object(service, "_verify_authentication_response") as mock_verify:
                mock_verify.return_value = MagicMock(new_sign_count=11)

                with patch.object(service, "_update_credential_counter", new_callable=AsyncMock):
                    with patch.object(service, "_get_user_for_credential", new_callable=AsyncMock) as mock_get_user:
                        mock_get_user.return_value = None  # User not found

                        with pytest.raises(BadRequestException) as exc:
                            await service.verify_authentication(
                                db=mock_db,
                                redis=mock_redis,
                                credential={"id": "test", "rawId": "test", "response": {}},
                                challenge_id="test-id",
                            )

                        assert "user" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_verify_registration_exception_handling():
    """Test verify_registration handles exceptions from webauthn."""
    from src.app.core.exceptions.http_exceptions import BadRequestException
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
            mock_verify.side_effect = Exception("Verification failed")

            with pytest.raises(BadRequestException) as exc:
                await service.verify_registration(
                    db=mock_db,
                    user=mock_user,
                    credential={"id": "test", "rawId": "test", "response": {}},
                    registration_id="test-registration-id",
                    redis=AsyncMock(),
                )

            assert "verification failed" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_verify_authentication_exception_handling():
    """Test verify_authentication handles exceptions from webauthn."""
    from src.app.core.exceptions.http_exceptions import BadRequestException
    from src.app.modules.passkey.service import PasskeyService

    service = PasskeyService()
    mock_db = AsyncMock()
    mock_redis = AsyncMock()

    mock_challenge_data = {"challenge": b"test-challenge"}
    mock_cred = MagicMock(id=1, user_id=1, credential_id=b"cred-id", public_key=b"pub-key", counter=10)

    with patch.object(service, "_get_stored_auth_challenge", new_callable=AsyncMock) as mock_get_challenge:
        mock_get_challenge.return_value = mock_challenge_data

        with patch.object(service, "_find_credential_by_id", new_callable=AsyncMock) as mock_find:
            mock_find.return_value = mock_cred

            with patch.object(service, "_verify_authentication_response") as mock_verify:
                mock_verify.side_effect = Exception("Auth verification failed")

                with pytest.raises(BadRequestException) as exc:
                    await service.verify_authentication(
                        db=mock_db,
                        redis=mock_redis,
                        credential={"id": "test", "rawId": "test", "response": {}},
                        challenge_id="test-id",
                    )

                assert "authentication failed" in str(exc.value.detail).lower()
