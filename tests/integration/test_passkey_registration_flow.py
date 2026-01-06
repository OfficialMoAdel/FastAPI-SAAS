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

    # Patch the service verification to return success
    # We patch the METHOD on the instance that is likely used by the router
    # Since router imports `passkey_service` singleton.
    from src.app.modules.passkey.service import passkey_service

    with patch.object(passkey_service, "_verify_with_webauthn") as mock_verify:
        mock_verify.return_value = MagicMock(
            credential_id=b"test-cred-id",
            credential_public_key=b"test-pub-key",
            sign_count=0,
            aaguid=UUID("00000000-0000-0000-0000-000000000000"),
            credential_device_type="Test Device",
        )

        # 1. Start Registration
        # Endpoint expects device_type query param or body?
        # Router: router.post("/register", response_model=PublicKeyCredentialCreationOptions)
        # def register_begin(request: Request, user: User = Depends(get_current_user), device_type: str = Body(..., embed=True))
        # Wait, if `device_type` is Body, we must send JSON.

        response = client.post(
            "/api/v1/auth/passkey/register",
            headers=auth_headers,
            json={"device_type": "Test Device"}
        )
        assert response.status_code == 200, f"Start registration failed: {response.text}"
        options = response.json()
        assert "publicKey" in options

        # 3. Complete Registration
        credential_data = {
            "id": "test-cred-id-b64", # In real flow this is base64url
            "rawId": "test-cred-id-b64",
            "response": {
                "clientDataJSON": "eyJjaGFsbGVuZ2UiOiAiLi4uIn0=",
                "attestationObject": "..."
            },
            "type": "public-key"
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
