"""Integration tests for file management API endpoints.

Tests the file CRUD operations:
- POST /files/upload - Upload file
- GET /files - List files
- GET /files/{id}/info - Get file metadata
- DELETE /files/{id} - Delete file
"""

import io

from fastapi import status

# Headers for tenant context
TENANT_HEADERS = {"X-Tenant-ID": "test-tenant-integration"}


class TestFileUploadAPI:
    """Tests for file upload endpoint."""

    def test_upload_file_no_storage_configured(self, client):
        """POST /files/upload returns error when storage not configured."""
        files = {"file": ("test.txt", io.BytesIO(b"test content"), "text/plain")}
        response = client.post("/api/v1/files/upload", files=files, headers=TENANT_HEADERS)
        # Should fail because no storage settings exist
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]


class TestFileListAPI:
    """Tests for file listing endpoint."""

    def test_list_files_returns_response(self, client):
        """GET /files returns a response."""
        response = client.get("/api/v1/files", headers=TENANT_HEADERS)
        # May return 200 (empty list) or 400 (no storage configured)
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]

    def test_list_files_with_pagination(self, client):
        """GET /files supports pagination parameters."""
        response = client.get("/api/v1/files?page=1&size=10", headers=TENANT_HEADERS)
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]


class TestFileInfoAPI:
    """Tests for file info endpoint."""

    def test_get_file_info_not_found(self, client):
        """GET /files/{id}/info returns 404 for non-existent file."""
        response = client.get("/api/v1/files/99999/info", headers=TENANT_HEADERS)
        # 404 = file not found, 400 = storage not configured
        assert response.status_code in [status.HTTP_404_NOT_FOUND, status.HTTP_400_BAD_REQUEST]

    def test_get_file_info_invalid_id(self, client):
        """GET /files/{id}/info returns 422 for invalid ID format."""
        response = client.get("/api/v1/files/invalid/info", headers=TENANT_HEADERS)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
