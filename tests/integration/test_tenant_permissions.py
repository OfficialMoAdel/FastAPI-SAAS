import pytest
from fastapi import status
from httpx import AsyncClient


@pytest.mark.asyncio
class TestTenantPermissions:
    """Integration tests for tenant permission enforcement."""

    async def test_list_tenants_forbidden_for_regular_user(
        self, async_client: AsyncClient, regular_user_token_headers: dict
    ) -> None:
        """Test that regular users cannot list all tenants."""
        async_client.headers.update(regular_user_token_headers)
        async_client.headers["User-Agent"] = "TestClient/1.0"
        response = await async_client.get("/api/v1/tenants")
        
        # Currently 200, expecting 403 after fix
        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_create_tenant_forbidden_for_regular_user(
        self, async_client: AsyncClient, regular_user_token_headers: dict
    ) -> None:
        """Test that regular users cannot create tenants."""
        async_client.headers.update(regular_user_token_headers)
        async_client.headers["User-Agent"] = "TestClient/1.0"
        response = await async_client.post(
            "/api/v1/tenants",
            json={"name": "Hacker Tenant", "slug": "hacker-tenant"}
        )
        
        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_deactivate_tenant_forbidden_for_regular_user(
        self, async_client: AsyncClient, regular_user_token_headers: dict
    ) -> None:
        """Test that regular users cannot deactivate tenants."""
        # Using ID 1 which exists in mocks
        async_client.headers.update(regular_user_token_headers)
        async_client.headers["User-Agent"] = "TestClient/1.0"
        response = await async_client.post("/api/v1/tenants/1/deactivate")
        
        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_get_tenant_allowed_for_member(
        self, async_client: AsyncClient, regular_user_token_headers: dict, tenant, get_test_app
    ) -> None:
        """Test that regular users CAN access their own tenant."""
        # Update mock user to match the real tenant ID created by fixture
        from src.app.api.dependencies import get_current_user
        from tests.conftest import MockUser

        fake_regular_user = MockUser(
            id=2,
            username="regular_user",
            email="user@example.com",
            is_superuser=False,
            is_active=True,
            tenant_id=tenant.id,
        )
        
        # Access the app from the fixture to update override
        get_test_app.dependency_overrides[get_current_user] = lambda: fake_regular_user

        async_client.headers.update(regular_user_token_headers)
        async_client.headers["User-Agent"] = "TestClient/1.0"
        
        # Access the specific tenant ID
        response = await async_client.get(f"/api/v1/tenants/{tenant.id}")
        
        assert response.status_code == status.HTTP_200_OK

    async def test_get_tenant_forbidden_for_non_member(
        self, async_client: AsyncClient, regular_user_token_headers: dict, tenant
    ) -> None:
        """Test that regular users cannot access other tenants."""
        async_client.headers.update(regular_user_token_headers)
        async_client.headers["User-Agent"] = "TestClient/1.0"
        
        # Accessing a different tenant ID (tenant.id + 1)
        # MockUser is in tenant 1 (from regular_user_token_headers default)
        # But we should probably rely on the default override here which has tenant_id=1
        # The created tenant likely has ID=1.
        # So we try to access tenant.id + 1.
        
        forbidden_id = tenant.id + 1
        response = await async_client.get(f"/api/v1/tenants/{forbidden_id}")
        
        assert response.status_code == status.HTTP_403_FORBIDDEN