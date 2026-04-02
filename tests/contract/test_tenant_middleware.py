"""Contract tests for tenant middleware header validation.

Tests the middleware behavior for X-Tenant-ID header:
- 400 response for missing header on protected endpoints
- 403 response for invalid/inactive tenant slug
- 200 response for valid tenant slug
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from src.app.middleware.tenant import TenantMiddleware

# Skip tests that require real database connection unless explicitly enabled
pytestmark = pytest.mark.skipif(
    os.getenv("TENANT_MIDDLEWARE_TESTS", "0") != "1",
    reason="Tenant middleware tests require proper asyncpg cleanup - run with TENANT_MIDDLEWARE_TESTS=1",
)


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

    def test_invalid_tenant_returns_403(self) -> None:
        """Test that invalid tenant slug returns 403 Forbidden."""
        # Create a fresh app with middleware that always returns None for tenant lookup
        app = FastAPI()

        @app.get("/api/v1/protected")
        async def protected():
            return {"message": "Protected endpoint"}

        # Add middleware with mocked _get_tenant_by_slug
        TenantMiddleware(app)

        # Patch _get_tenant_by_slug at the module level to always return None
        with patch.object(TenantMiddleware, "_get_tenant_by_slug", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None

            # Also mock async_engine to prevent real DB connections
            with patch("src.app.core.config.settings") as mock_engine:
                mock_cm = MagicMock()
                mock_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
                mock_cm.__aexit__ = AsyncMock(return_value=None)
                mock_engine.connect.return_value = mock_cm

                client = TestClient(app)
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
