"""Contract tests for Passkey API."""

# from httpx import AsyncClient - NOT USED for TestClient
from fastapi.testclient import TestClient


def test_passkey_api_endpoints_exist(client: TestClient):
    """Verify that passkey registration endpoints exist."""

    # Try to access start registration (should fail with 401 if unauth, not 404)
    response = client.post("/api/v1/auth/passkey/register")
    assert response.status_code != 404, "Endpoint /register not found"

    # Try to access verify registration
    response = client.post("/api/v1/auth/passkey/register/verify")
    assert response.status_code != 404, "Endpoint /register/verify not found"

    # Try to access list credentials
    response = client.get("/api/v1/auth/passkey/credentials")
    assert response.status_code != 404, "Endpoint /credentials not found"
