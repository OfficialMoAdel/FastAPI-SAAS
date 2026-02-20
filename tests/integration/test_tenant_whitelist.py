"""Integration tests for whitelist path bypass in tenant middleware.

Tests that whitelisted paths can be accessed without X-Tenant-ID header:
- /docs
- /redoc
- /openapi.json
- /api/v1/health
- /api/v1/ready
"""

import pytest
import pytest_asyncio
from fastapi import status
from httpx import ASGITransport, AsyncClient

from src.app.main import app

# Test user-agent to avoid IP filter blocking
TEST_USER_AGENT = "TestClient/1.0"


@pytest_asyncio.fixture
async def async_client() -> AsyncClient:
    """Create an async test client with proper User-Agent."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"User-Agent": TEST_USER_AGENT},
    ) as client:
        yield client


@pytest.mark.asyncio
class TestWhitelistPathBypass:
    """Integration tests for whitelist path bypass."""

    async def test_health_endpoint_accessible_without_tenant_header(self, async_client: AsyncClient) -> None:
        """Test that /api/v1/health is accessible without X-Tenant-ID header."""
        response = await async_client.get("/api/v1/health")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "status" in data

    async def test_ready_endpoint_accessible_without_tenant_header(self, async_client: AsyncClient) -> None:
        """Test that /api/v1/ready is accessible without X-Tenant-ID header."""
        response = await async_client.get("/api/v1/ready")

        # May be 200 or 503 depending on service state, but should not be 400/403
        assert response.status_code not in [status.HTTP_400_BAD_REQUEST, status.HTTP_403_FORBIDDEN]

    async def test_docs_endpoint_accessible_without_tenant_header(self, async_client: AsyncClient) -> None:
        """Test that /docs is accessible without X-Tenant-ID header."""
        response = await async_client.get("/docs")

        # Docs endpoint returns HTML, status should be 200
        assert response.status_code == status.HTTP_200_OK

    async def test_redoc_endpoint_accessible_without_tenant_header(self, async_client: AsyncClient) -> None:
        """Test that /redoc is accessible without X-Tenant-ID header."""
        response = await async_client.get("/redoc")

        assert response.status_code == status.HTTP_200_OK

    async def test_openapi_json_accessible_without_tenant_header(self, async_client: AsyncClient) -> None:
        """Test that /openapi.json is accessible without X-Tenant-ID header."""
        response = await async_client.get("/openapi.json")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "openapi" in data

    async def test_protected_endpoint_requires_tenant_header(self, async_client: AsyncClient) -> None:
        """Test that non-whitelisted endpoints require X-Tenant-ID header."""
        response = await async_client.get("/api/v1/users")

        # Should return 400 (missing header) or 401 (auth required)
        # The exact code depends on middleware order, but should not be 200
        assert response.status_code != status.HTTP_200_OK