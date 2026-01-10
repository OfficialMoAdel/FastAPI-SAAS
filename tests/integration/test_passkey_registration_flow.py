"""Integration test for Passkey Registration Flow."""

from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient


# Since client fixture mocks authentication via dependency override,
# we don't need a token, but we DO need Tenant headers.
@pytest.mark.integration
def test_passkey_registration_flow(
    client: TestClient,
    auth_headers: dict[str, str],
    # db: AsyncSession, # We don't need direct DB access if we trust the API
    # redis: Redis,    # We don't need redis if trusting the flow
):
    """
    Test full passkey registration flow:
    1. Start registration (GET options)
    2. Simulate authenticators creating credential (using a patch)
    3. Complete registration (POST credential)
    4. Verify credential exists in list
    """

    # Patch generate_registration_options to avoid "Mock not JSON serializable"
    # This usually happens if webauthn library returns a Mock or if an input is a Mock
    # patching it ensures we get a clean object

    from src.app.modules.passkey.service import passkey_service

    with patch("src.app.modules.passkey.service.generate_registration_options") as mock_generate:
        mock_generate.return_value = MagicMock(
            challenge=b"test-challenge",
            rp={"name": "Test App", "id": "localhost"},
            user={"id": b"1", "name": "test@example.com", "displayName": "Test User"},
            pubKeyCredParams=[],
            timeout=60000,
            excludeCredentials=[],
            authenticatorSelection=None,
            attestation="none",
            extensions=None,
        )

        # We also need to patch options_to_json because it doesn't like Mocks either
        with patch("src.app.modules.passkey.service.options_to_json") as mock_to_json:
            mock_to_json.return_value = '{"challenge": "dGVzdC1jaGFsbGVuZ2U", "publicKey": {}}'

            with patch.object(passkey_service, "_verify_with_webauthn") as mock_verify:
                mock_verify.return_value = MagicMock(
                    credential_id=b"test-cred-id",
                    credential_public_key=b"test-pub-key",
                    sign_count=0,
                    aaguid=UUID("00000000-0000-0000-0000-000000000000"),
                    credential_device_type="Test Device",
                )

                # 1. Start Registration
                response = client.post(
                    "/api/v1/auth/passkey/register", headers=auth_headers, json={"device_type": "Test Device"}
                )
                assert response.status_code == 200, f"Start registration failed: {response.text}"
                options = response.json()
                assert "publicKey" in options or "challenge" in options

                # 3. Complete Registration
                credential_data = {
                    "id": "test-cred-id-b64",  # In real flow this is base64url
                    "rawId": "test-cred-id-b64",
                    "response": {"clientDataJSON": "eyJjaGFsbGVuZ2UiOiAiLi4uIn0=", "attestationObject": "..."},
                    "type": "public-key",
                }

                response = client.post(
                    "/api/v1/auth/passkey/register/verify",
                    json={"credential": credential_data},
                    headers=auth_headers,
                )
                assert response.status_code == 200, f"Complete registration failed: {response.text}"
                data = response.json()
                assert data["id"] is not None

                # 4. List Credentials
                response = client.get(
                    "/api/v1/auth/passkey/credentials",
                    headers=auth_headers,
                )
                assert response.status_code == 200
                creds = response.json()
                assert len(creds) >= 1
                found = False
                for c in creds:
                    if c["device_type"] == "Test Device":
                        found = True
                        break
                assert found, "Created credential not found in list"
