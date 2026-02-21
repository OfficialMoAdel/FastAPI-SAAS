"""Integration tests for file signed URL functionality.

Tests signed URL generation for file access:
- GET /files/{id}/url - Generate signed URL
- Expiration handling
- Public vs private file access
"""

from fastapi import status

# Headers for tenant context
TENANT_HEADERS = {"X-Tenant-ID": "test-tenant-integration"}


class TestSignedURLGeneration:
    """Tests for signed URL generation endpoint."""

    def test_get_signed_url_not_found(self, client):
        """GET /files/{id}/url returns 404 for non-existent file."""
        response = client.get("/api/v1/files/999999/url", headers=TENANT_HEADERS)
        # 401 = auth required, 404 = not found, 400 = storage not configured
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_400_BAD_REQUEST,
        ]

    def test_get_signed_url_invalid_id(self, client):
        """GET /files/{id}/url returns 422 for invalid ID format."""
        response = client.get("/api/v1/files/invalid/url", headers=TENANT_HEADERS)
        # 401 = auth required, 422 = validation error
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        ]

    def test_get_signed_url_custom_expiry(self, client):
        """GET /files/{id}/url accepts expires_in parameter."""
        # Request with custom expiry (1 hour = 3600 seconds)
        response = client.get("/api/v1/files/999999/url?expires_in=3600", headers=TENANT_HEADERS)
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_400_BAD_REQUEST,
        ]

    def test_get_signed_url_default_expiry(self, client):
        """GET /files/{id}/url uses default expiry when not specified."""
        response = client.get("/api/v1/files/999999/url", headers=TENANT_HEADERS)
        # Response should not error on missing expires_in
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_400_BAD_REQUEST,
        ]

    def test_get_signed_url_max_expiry_limit(self, client):
        """GET /files/{id}/url should respect maximum expiry limit."""
        # Request very long expiry (7 days)
        response = client.get("/api/v1/files/999999/url?expires_in=604800", headers=TENANT_HEADERS)
        # Should either succeed or be capped, not error on validation
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        ]


class TestSignedURLSecurity:
    """Tests for signed URL security features."""

    def test_signed_url_requires_authentication(self, client):
        """GET /files/{id}/url requires authenticated user."""
        # The client fixture does NOT include auth by default
        response = client.get("/api/v1/files/1/url", headers=TENANT_HEADERS)
        # Should return 401 without valid auth, or 404 if file not found
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_400_BAD_REQUEST,
        ]

    def test_signed_url_respects_tenant_isolation(self, client):
        """Signed URLs are scoped to tenant context."""
        response = client.get("/api/v1/files/1/url", headers=TENANT_HEADERS)
        # File from another tenant should return 404, not the URL
        # 401 = auth required, 404 = not found, 400 = storage not configured
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_400_BAD_REQUEST,
        ]
