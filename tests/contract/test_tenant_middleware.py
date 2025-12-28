"""Contract tests for tenant middleware header validation.

Tests the middleware behavior for X-Tenant-ID header:
- 400 response for missing header on protected endpoints
- 403 response for invalid/inactive tenant slug
- 200 response for valid tenant slug
"""

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from src.app.middleware.tenant import TenantMiddleware


@pytest.fixture
def test_app() -> FastAPI:
    """Create a test FastAPI app with tenant middleware."""
    app = FastAPI()

    app.add_middleware(TenantMiddleware)

    @app.get("/api/v1/protected")
    async def protected_endpoint() -> dict[str, str]:
        return {"message": "protected"}

    @app.get("/api/v1/health")
    async def health_endpoint() -> dict[str, str]:
        return {"status": "healthy"}

    @app.get("/docs")
    async def docs_endpoint() -> dict[str, str]:
        return {"docs": "available"}

    return app


@pytest.fixture
def client(test_app: FastAPI) -> TestClient:
    """Create a test client."""
    return TestClient(test_app)


class TestTenantMiddlewareContract:
    """Contract tests for tenant middleware."""

    def test_missing_header_returns_400_on_protected_endpoint(self, client: TestClient) -> None:
        """Test that missing X-Tenant-ID header returns 400 Bad Request."""
        response = client.get("/api/v1/protected")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "X-Tenant-ID" in response.json().get("detail", "")

    def test_empty_header_returns_400(self, client: TestClient) -> None:
        """Test that empty X-Tenant-ID header returns 400 Bad Request."""
        response = client.get("/api/v1/protected", headers={"X-Tenant-ID": ""})

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_whitespace_header_returns_400(self, client: TestClient) -> None:
        """Test that whitespace-only X-Tenant-ID header returns 400 Bad Request."""
        response = client.get("/api/v1/protected", headers={"X-Tenant-ID": "   "})

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_invalid_tenant_returns_403(self, client: TestClient) -> None:
        """Test that invalid tenant slug returns 403 Forbidden."""
        response = client.get("/api/v1/protected", headers={"X-Tenant-ID": "nonexistent-tenant"})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "Access denied" in response.json().get("detail", "")

    def test_valid_tenant_returns_200(self, client: TestClient) -> None:
        """Test that valid tenant slug allows request to proceed.

        Note: This test requires a valid tenant in the database.
        For contract testing, we may need to mock the database lookup.
        """
        pytest.skip("Requires database setup with valid tenant - covered in integration tests")

    def test_whitelist_path_health_bypasses_validation(self, client: TestClient) -> None:
        """Test that /api/v1/health bypasses tenant validation."""
        response = client.get("/api/v1/health")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "healthy"}

    def test_whitelist_path_docs_bypasses_validation(self, client: TestClient) -> None:
        """Test that /docs bypasses tenant validation."""
        response = client.get("/docs")

        assert response.status_code == status.HTTP_200_OK
