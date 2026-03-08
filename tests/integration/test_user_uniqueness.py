from unittest.mock import Mock, patch
from uuid import UUID

import pytest
from httpx import AsyncClient
from uuid6 import uuid7


@pytest.mark.asyncio
async def test_per_tenant_username_uniqueness(
    async_client: AsyncClient,
    superuser_token_headers: dict,
):
    """
    Test that usernames are unique per tenant, but can be duplicated across tenants.
    """
    # Define tenants mapping - will be populated with real UUIDs after creation
    tenants_map = {}

    # Fix signature: _get_tenant_by_slug is called with (slug) only
    async def mock_get_tenant_by_slug(slug):
        return tenants_map.get(slug)

    # Override the mock from full_mock_context
    with patch("src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug", side_effect=mock_get_tenant_by_slug):
        # 1. Create Tenant A
        async_client.headers.update(superuser_token_headers)
        async_client.headers["User-Agent"] = "TestClient/1.0"

        tenant_a_slug = f"tenant-a-{uuid7().hex[:8]}"
        resp = await async_client.post("/api/v1/tenants", json={"name": "Tenant A", "slug": tenant_a_slug})
        assert resp.status_code == 201, f"Failed to create Tenant A: {resp.text}"
        tenant_a_data = resp.json()
        tenant_a_id = UUID(tenant_a_data["id"])  # Convert string to UUID object

        # Add to mock map with real ID
        tenants_map[tenant_a_slug] = Mock(id=tenant_a_id, slug=tenant_a_slug, is_active=True)

        # 2. Create Tenant B
        tenant_b_slug = f"tenant-b-{uuid7().hex[:8]}"
        resp = await async_client.post("/api/v1/tenants", json={"name": "Tenant B", "slug": tenant_b_slug})
        assert resp.status_code == 201, f"Failed to create Tenant B: {resp.text}"
        tenant_b_data = resp.json()
        tenant_b_id = UUID(tenant_b_data["id"])  # Convert string to UUID object

        # Add to mock map with real ID
        tenants_map[tenant_b_slug] = Mock(id=tenant_b_id, slug=tenant_b_slug, is_active=True)

        # 3. Create User "dupe" in Tenant A
        user_data = {
            "email": "dupe@example.com",
            "username": "dupeuser",
            "password": "Password123!",
            "name": "Dupe User",
        }

        async_client.headers["X-Tenant-ID"] = tenant_a_slug
        resp = await async_client.post("/api/v1/users", json=user_data)
        assert resp.status_code == 201, f"Failed to create user in Tenant A: {resp.text}"

        # 4. Create User "dupe" in Tenant B (Should SUCCEED)
        async_client.headers["X-Tenant-ID"] = tenant_b_slug
        resp = await async_client.post("/api/v1/users", json=user_data)
        assert resp.status_code == 201, f"Failed to create in Tenant B: {resp.text}"

        # 5. Create User "dupe" in Tenant A AGAIN (Should FAIL)
        async_client.headers["X-Tenant-ID"] = tenant_a_slug
        resp = await async_client.post("/api/v1/users", json=user_data)
        assert resp.status_code == 409, "Should fail with duplicate error in same tenant"
