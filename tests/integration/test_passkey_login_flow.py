"""Integration test for Passkey Login Flow.

Task: T078 [US8] Write integration test for passkey login
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
def test_passkey_login_flow(
    client: TestClient,
    regular_user_token_headers: dict[str, str],
):
    """
    Test full passkey login flow:
    1. Register a passkey first (prerequisite)
    2. Start login (GET authentication options)
    3. Simulate authenticator response (using patches)
    4. Complete login (POST assertion)
    5. Verify JWT tokens are returned
    """
    from uuid import UUID

    from src.app.modules.passkey.service import passkey_service

    # =========================================================================
    # Step 1: Register a passkey first (prerequisite for login)
    # =========================================================================
    with patch.object(passkey_service, "_verify_with_webauthn") as mock_reg_verify:
        mock_reg_verify.return_value = MagicMock(
            credential_id=b"login-test-cred-id",
            credential_public_key=b"login-test-pub-key",
            sign_count=0,
            aaguid=UUID("00000000-0000-0000-0000-000000000000"),
            credential_device_type="Login Test Device",
        )

        # Also mock _store_credential to return a proper response
        with patch.object(passkey_service, "_store_credential") as mock_store:
            mock_credential = MagicMock()
            mock_credential.id = 1
            mock_credential.device_type = "Login Test Device"
            mock_credential.created_at = "2026-01-01T00:00:00Z"
            mock_credential.last_used_at = None
            mock_store.return_value = mock_credential

            # Start registration
            response = client.post(
                "/api/v1/auth/passkey/register",
                headers=regular_user_token_headers,
                json={"device_type": "Login Test Device"}
            )
            assert response.status_code == 200, f"Start registration failed: {response.text}"

            # Complete registration
            credential_data = {
                "id": "login-test-cred-id-b64",
                "rawId": "login-test-cred-id-b64",
                "response": {
                    "clientDataJSON": "eyJjaGFsbGVuZ2UiOiAiLi4uIn0=",
                    "attestationObject": "..."
                },
                "type": "public-key"
            }

            response = client.post(
                "/api/v1/auth/passkey/register/verify",
                json={"credential": credential_data},
                headers=regular_user_token_headers,
            )
            assert response.status_code == 200, f"Complete registration failed: {response.text}"

    # =========================================================================
    # Step 2: Start Login (Authentication Challenge)
    # =========================================================================
    response = client.post(
        "/api/v1/auth/passkey/login",
        json={},  # No email hint for discoverable credentials
        headers=regular_user_token_headers,
    )
    assert response.status_code == 200, f"Start login failed: {response.text}"
    options = response.json()
    assert "publicKey" in options
    assert "challenge" in options["publicKey"]
    assert "rpId" in options["publicKey"]

    # =========================================================================
    # Step 3 & 4: Complete Login with Mocked Verification
    # =========================================================================
    with patch.object(passkey_service, "_verify_authentication_response") as mock_auth_verify:
        # Mock successful authentication response
        mock_auth_verify.return_value = MagicMock(
            new_sign_count=1,  # Counter must be > 0 (stored counter)
            credential_id=b"login-test-cred-id",
        )

        # Mock _find_credential_by_id to return a mock credential
        with patch.object(passkey_service, "_find_credential_by_id") as mock_find:
            mock_credential = MagicMock()
            mock_credential.id = 1
            mock_credential.counter = 0
            mock_credential.user_id = 1
            mock_find.return_value = mock_credential

            # Mock _get_user_for_credential
            with patch.object(passkey_service, "_get_user_for_credential") as mock_get_user:
                mock_user = MagicMock()
                mock_user.id = 1
                mock_get_user.return_value = mock_user

                # Mock _update_credential_counter
                with patch.object(passkey_service, "_update_credential_counter") as mock_update:
                    mock_update.return_value = None

                    # Mock _generate_auth_tokens
                    with patch.object(passkey_service, "_generate_auth_tokens") as mock_tokens:
                        mock_tokens.return_value = {
                            "access_token": "test-access-token",
                            "refresh_token": "test-refresh-token",
                            "token_type": "bearer",
                            "expires_in": 1800,
                        }

                        # Submit authentication assertion
                        assertion_data = {
                            "id": "login-test-cred-id-b64",
                            "rawId": "login-test-cred-id-b64",
                            "response": {
                                "clientDataJSON": "eyJjaGFsbGVuZ2UiOiAiLi4uIn0=",
                                "authenticatorData": "...",
                                "signature": "...",
                            },
                            "type": "public-key"
                        }

                        response = client.post(
                            "/api/v1/auth/passkey/login/verify",
                            json={"credential": assertion_data},
                            headers=regular_user_token_headers,
                        )
                        assert response.status_code == 200, f"Login verify failed: {response.text}"

                        # =========================================================
                        # Step 5: Verify JWT tokens are returned
                        # =========================================================
                        data = response.json()
                        assert "access_token" in data
                        assert "refresh_token" in data
                        assert data["token_type"] == "bearer"
                        assert data["expires_in"] > 0


@pytest.mark.integration
def test_passkey_login_with_email_hint(
    client: TestClient,
    regular_user_token_headers: dict[str, str],
):
    """Test passkey login with email hint to filter credentials."""
    # Start login with email hint - requires tenant header
    response = client.post(
        "/api/v1/auth/passkey/login",
        json={"email": "test@example.com"},
        headers=regular_user_token_headers,
    )
    assert response.status_code == 200
    options = response.json()
    assert "publicKey" in options
    # allowCredentials may be empty if no credentials exist for that email


@pytest.mark.integration
def test_passkey_login_expired_challenge(
    client: TestClient,
    regular_user_token_headers: dict[str, str],
):
    """Test that expired/missing challenge returns error."""
    from src.app.modules.passkey.service import passkey_service

    with patch.object(passkey_service, "_get_stored_auth_challenge") as mock_challenge:
        mock_challenge.return_value = None  # No challenge found

        assertion_data = {
            "id": "some-cred-id",
            "rawId": "some-cred-id",
            "response": {
                "clientDataJSON": "eyJjaGFsbGVuZ2UiOiAiLi4uIn0=",
                "authenticatorData": "...",
                "signature": "...",
            },
            "type": "public-key"
        }

        response = client.post(
            "/api/v1/auth/passkey/login/verify",
            json={"credential": assertion_data},
            headers=regular_user_token_headers,
        )
        # Should return 400 for expired challenge
        assert response.status_code == 400
        assert "expired" in response.json()["detail"].lower() or "invalid" in response.json()["detail"].lower()


@pytest.mark.integration
def test_passkey_login_replay_attack_prevention(
    client: TestClient,
    regular_user_token_headers: dict[str, str],
):
    """Test that replay attacks (counter not increasing) are rejected."""
    from src.app.modules.passkey.service import passkey_service

    with patch.object(passkey_service, "_get_stored_auth_challenge") as mock_challenge:
        mock_challenge.return_value = b"test-challenge"

        with patch.object(passkey_service, "_find_credential_by_id") as mock_find:
            mock_credential = MagicMock()
            mock_credential.id = 1
            mock_credential.counter = 10  # Current stored counter
            mock_find.return_value = mock_credential

            with patch.object(passkey_service, "_verify_authentication_response") as mock_verify:
                # Return a counter that's NOT greater than stored (replay attempt)
                mock_verify.return_value = MagicMock(
                    new_sign_count=10,  # Same as stored - should be rejected
                )

                assertion_data = {
                    "id": "replay-cred-id",
                    "rawId": "replay-cred-id",
                    "response": {
                        "clientDataJSON": "eyJjaGFsbGVuZ2UiOiAiLi4uIn0=",
                        "authenticatorData": "...",
                        "signature": "...",
                    },
                    "type": "public-key"
                }

                response = client.post(
                    "/api/v1/auth/passkey/login/verify",
                    json={"credential": assertion_data},
                    headers=regular_user_token_headers,
                )
                # Should return 400 for replay attack
                assert response.status_code == 400
                assert "counter" in response.json()["detail"].lower() or "replay" in response.json()["detail"].lower()
