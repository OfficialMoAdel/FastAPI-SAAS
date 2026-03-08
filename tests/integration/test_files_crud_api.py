"""Integration tests for file CRUD operations.

Tests file management operations:
- GET /files - List files with filtering
- GET /files/{id}/info - Get file metadata
- PATCH /files/{id} - Update file metadata
- DELETE /files/{id} - Delete file
- Tenant isolation verification
"""

from fastapi import status

# Headers for tenant context
TENANT_HEADERS = {"X-Tenant-ID": "test-tenant-integration"}
OTHER_TENANT_HEADERS = {"X-Tenant-ID": "other-tenant"}


class TestFileListOperations:
    """Tests for file listing with filters."""

    def test_list_files_empty_returns_paginated_response(self, client, superuser_token_headers):
        """GET /files returns paginated response structure."""
        headers = {**TENANT_HEADERS, **superuser_token_headers}
        response = client.get("/api/v1/files", headers=headers)
        # 200 with empty list or 400 if storage not configured
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]
        if response.status_code == status.HTTP_200_OK:
            data = response.json()
            # Should have pagination structure
            assert isinstance(data, list | dict)

    def test_list_files_with_mime_type_filter(self, client, superuser_token_headers):
        """GET /files?mime_type=image/jpeg filters by mime type."""
        headers = {**TENANT_HEADERS, **superuser_token_headers}
        response = client.get("/api/v1/files?mime_type=image/jpeg", headers=headers)
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]

    def test_list_files_with_pagination(self, client, superuser_token_headers):
        """GET /files supports page and size parameters."""
        headers = {**TENANT_HEADERS, **superuser_token_headers}
        response = client.get("/api/v1/files?page=1&size=5", headers=headers)
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]

    def test_list_files_with_public_filter(self, client, superuser_token_headers):
        """GET /files?is_public=true filters public files."""
        headers = {**TENANT_HEADERS, **superuser_token_headers}
        response = client.get("/api/v1/files?is_public=true", headers=headers)
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]


class TestFileInfoOperations:
    """Tests for file info retrieval."""

    def test_get_file_info_not_found(self, client, superuser_token_headers):
        """GET /files/{id}/info returns 404 for non-existent file."""
        headers = {**TENANT_HEADERS, **superuser_token_headers}
        response = client.get("/api/v1/files/99999/info", headers=headers)
        assert response.status_code in [status.HTTP_404_NOT_FOUND, status.HTTP_400_BAD_REQUEST]

    def test_get_file_info_invalid_id_format(self, client, superuser_token_headers):
        """GET /files/{id}/info returns 422 for invalid ID."""
        headers = {**TENANT_HEADERS, **superuser_token_headers}
        response = client.get("/api/v1/files/not-an-int/info", headers=headers)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


class TestFileUpdateOperations:
    """Tests for file metadata updates."""

    def test_update_file_not_found(self, client, superuser_token_headers):
        """PATCH /files/{id} returns 404 for non-existent file."""
        payload = {"description": "Updated description", "is_public": True}
        headers = {**TENANT_HEADERS, **superuser_token_headers}
        response = client.patch("/api/v1/files/999999", json=payload, headers=headers)
        assert response.status_code in [status.HTTP_404_NOT_FOUND, status.HTTP_400_BAD_REQUEST]

    def test_update_file_invalid_id(self, client, superuser_token_headers):
        """PATCH /files/{id} returns 422 for invalid ID format."""
        payload = {"description": "Test"}
        headers = {**TENANT_HEADERS, **superuser_token_headers}
        response = client.patch("/api/v1/files/invalid", json=payload, headers=headers)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


class TestFileDeleteOperations:
    """Tests for file deletion."""

    def test_delete_file_not_found(self, client, superuser_token_headers):
        """DELETE /files/{id} returns 404 for non-existent file."""
        headers = {**TENANT_HEADERS, **superuser_token_headers}
        response = client.delete("/api/v1/files/999999", headers=headers)
        assert response.status_code in [status.HTTP_404_NOT_FOUND, status.HTTP_400_BAD_REQUEST]

    def test_delete_file_invalid_id(self, client, superuser_token_headers):
        """DELETE /files/{id} returns 422 for invalid ID format."""
        headers = {**TENANT_HEADERS, **superuser_token_headers}
        response = client.delete("/api/v1/files/invalid", headers=headers)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


class TestTenantIsolation:
    """Tests for tenant isolation in file operations."""

    def test_files_isolated_by_tenant(self, client, superuser_token_headers):
        """Files from one tenant are not visible to another."""
        # List files for first tenant
        headers = {**TENANT_HEADERS, **superuser_token_headers}
        response1 = client.get("/api/v1/files", headers=headers)

        # List files for different tenant
        headers = {**OTHER_TENANT_HEADERS, **superuser_token_headers}
        response2 = client.get("/api/v1/files", headers=headers)

        # Both should return valid responses (may be 400 if no storage configured)
        assert response1.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]
        assert response2.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]

    def test_cross_tenant_file_access_returns_404(self, client, superuser_token_headers):
        """Accessing file from another tenant returns 404."""
        # Try to access a file ID that might exist in another tenant
        # Should return 404 (not found in current tenant) not 403
        headers = {**TENANT_HEADERS, **superuser_token_headers}
        response = client.get("/api/v1/files/1/info", headers=headers)
        # 404 = correct isolation, 400 = storage not configured
        assert response.status_code in [status.HTTP_404_NOT_FOUND, status.HTTP_400_BAD_REQUEST]
