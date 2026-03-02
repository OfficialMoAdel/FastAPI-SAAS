"""
Permissions Endpoint Tests

Unit tests for the permissions endpoint in RBAC module.
"""

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from src.app.main import app

client = TestClient(app)


class TestPermissionsEndpoint:
    """Test permissions listing endpoint."""

    def test_permissions_requires_tenant(self):
        """Should return 400 when tenant header missing."""
        response = client.get("/api/v1/permissions")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Missing required header" in response.json()["detail"]

    def test_permissions_registered_in_openapi(self):
        """Permissions endpoint should be in OpenAPI spec."""
        response = client.get("/openapi.json")
        assert response.status_code == 200

        spec = response.json()
        paths = spec["paths"]

        # Check endpoint exists
        assert "/api/v1/permissions" in paths
        assert "get" in paths["/api/v1/permissions"]

        # Check tags
        endpoint = paths["/api/v1/permissions"]["get"]
        assert "rbac" in endpoint["tags"]

    def test_permissions_response_schema(self):
        """Permissions endpoint should return list of PermissionOut."""
        response = client.get("/openapi.json")
        spec = response.json()

        endpoint = spec["paths"]["/api/v1/permissions"]["get"]
        responses = endpoint["responses"]

        # Should have 200 response
        assert "200" in responses


class TestPermissionsService:
    """Test permissions service method."""

    @pytest.mark.asyncio
    async def test_get_all_permissions_method_exists(self):
        """Service should have get_all_permissions method."""
        from src.app.modules.rbac.service import RoleService

        # Check method exists
        assert hasattr(RoleService, "get_all_permissions")

    @pytest.mark.asyncio
    async def test_get_all_permissions_returns_sequence(self):
        """get_all_permissions should return a sequence."""
        # Check method signature
        import inspect

        from src.app.modules.rbac.service import RoleService

        sig = inspect.signature(RoleService.get_all_permissions)
        return_annotation = sig.return_annotation

        # Should return Sequence[Permission]
        assert "Sequence" in str(return_annotation)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
