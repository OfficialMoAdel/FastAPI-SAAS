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
        response = await async_client.post("/api/v1/tenants", json={"name": "Hacker Tenant", "slug": "hacker-tenant"})

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
        # Update principal to match the real tenant ID created by fixture
        from tests.conftest import MockUser
        from tests.helpers.auth import install_test_principal

        fake_regular_user = MockUser(
            id=2,
            username="regular_user",
            email="user@example.com",
            is_superuser=False,
            is_active=True,
            tenant_id=tenant.id,
        )

        # Access the app from the fixture to update override
        install_test_principal(get_test_app, fake_regular_user, tenant.id)

        # Align the middleware tenant context with the REAL fixture tenant
        # (production resolves slug→same row; the stock mock returns a
        # DEFAULT_TENANT_ID that differs from the fixture row, which the
        # soft-404 tenant-match seam correctly treats as cross-tenant).
        from unittest.mock import AsyncMock
        from unittest.mock import patch as _patch

        from src.app.core.tenancy.context import TenantContext
        from src.app.middleware.tenant import TenantMiddleware

        fixture_ctx = TenantContext(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            tenant_name=tenant.name,
            is_active=True,
        )
        with _patch.object(
            TenantMiddleware, "_get_tenant_by_slug", new=AsyncMock(return_value=fixture_ctx)
        ):
            async_client.headers.update(regular_user_token_headers)
            async_client.headers["User-Agent"] = "TestClient/1.0"

            # Access the specific tenant ID
            response = await async_client.get(f"/api/v1/tenants/{tenant.id}")

        assert response.status_code == status.HTTP_200_OK

    async def test_get_tenant_forbidden_for_non_member(
        self, async_client: AsyncClient, regular_user_token_headers: dict, tenant
    ) -> None:
        """Test that regular users cannot access other tenants."""
        import uuid

        async_client.headers.update(regular_user_token_headers)
        async_client.headers["User-Agent"] = "TestClient/1.0"

        # Accessing a different tenant ID (random UUID that doesn't exist)
        # MockUser is in tenant 1 (from regular_user_token_headers default)
        # The created tenant likely has ID=1.
        # So we try to access a random UUID that the user doesn't belong to.

        forbidden_id = uuid.uuid4()  # Random UUID that doesn't match user's tenant
        response = await async_client.get(f"/api/v1/tenants/{forbidden_id}")

        # P1 FIX (code-review): the seam now returns 404 (soft-404,
        # enumeration-safe) for ANY tenant the caller doesn't own —
        # existent or not — instead of a 403 that leaked existence.
        assert response.status_code == status.HTTP_404_NOT_FOUND
