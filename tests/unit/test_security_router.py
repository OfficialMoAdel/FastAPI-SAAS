"""
Security Router Tests

Unit tests for the security router (passkey wrapper endpoints).
"""

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from src.app.main import app

client = TestClient(app)


class TestSecurityPasskeyEndpoints:
    """Test security router passkey management endpoints."""

    def test_list_passkeys_requires_tenant(self):
        """Should return 400 when tenant header missing."""
        response = client.get("/api/v1/security/passkeys")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Missing required header" in response.json()["detail"]

    def test_delete_passkey_requires_tenant(self):
        """Should return 400 when tenant header missing."""
        response = client.delete("/api/v1/security/passkeys/1")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Missing required header" in response.json()["detail"]

    def test_security_router_registered(self):
        """Security router should be registered in OpenAPI spec."""
        response = client.get("/openapi.json")
        assert response.status_code == 200

        spec = response.json()
        paths = spec["paths"]

        # Check that security endpoints exist
        assert "/api/v1/security/passkeys" in paths
        assert "/api/v1/security/passkeys/{passkey_id}" in paths

        # Check methods
        assert "get" in paths["/api/v1/security/passkeys"]
        assert "delete" in paths["/api/v1/security/passkeys/{passkey_id}"]

    def test_security_endpoint_tags(self):
        """Security endpoints should have correct tags."""
        response = client.get("/openapi.json")
        spec = response.json()

        # Check tags
        passkeys_endpoint = spec["paths"]["/api/v1/security/passkeys"]["get"]
        assert "security" in passkeys_endpoint["tags"]


class TestSecurityRouterCompatibility:
    """Test that security router provides same functionality as passkey router."""

    def test_passkeys_endpoint_mirrors_credentials(self):
        """GET /security/passkeys should mirror /auth/passkey/credentials."""
        # Both should return 400 when tenant header is missing
        security_response = client.get("/api/v1/security/passkeys")
        passkey_response = client.get("/api/v1/auth/passkey/credentials")

        assert security_response.status_code == passkey_response.status_code
        assert security_response.status_code == status.HTTP_400_BAD_REQUEST


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
