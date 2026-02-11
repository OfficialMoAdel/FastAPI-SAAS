import pytest
from httpx import AsyncClient
from sqlalchemy import text
from unittest.mock import Mock, patch
from src.app.core.config import settings

@pytest.mark.asyncio
async def test_per_tenant_username_uniqueness(
    async_client: AsyncClient,
    superuser_token_headers: dict,
):
    """
    Test that usernames are unique per tenant, but can be duplicated across tenants.
    """
    # Define tenants mapping
    tenants_map = {
        "test-tenant-integration": Mock(id=1, slug="test-tenant-integration", is_active=True), # Default
        "tenant-a-unique": Mock(id=10, slug="tenant-a-unique", is_active=True),
        "tenant-b-unique": Mock(id=20, slug="tenant-b-unique", is_active=True),
    }

    # Fix signature: _get_tenant_by_slug is called with (slug) only
    async def mock_get_tenant_by_slug(slug):
        return tenants_map.get(slug)

    # Override the mock from full_mock_context
    with patch("src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug", side_effect=mock_get_tenant_by_slug):
        
        # 1. Create Tenant A
        async_client.headers.update(superuser_token_headers)
        async_client.headers["User-Agent"] = "TestClient/1.0"
        
        tenant_a_slug = "tenant-a-unique"
        # We don't rely on POST /tenants response for ID anymore, we force IDs in our mock map
        # But we still need to create them in DB if FK constraints exist?
        # User.tenant_id is a FK. SQLite enforces FKs? By default no, but SQLAlchemy might.
        # But we are mocking the middleware resolution.
        # The DB insert will use the ID from the middleware context (which comes from our mock).
        # So we don't strictly need the Tenant to exist in DB for the middleware to work, 
        # BUT User.tenant_id FK might fail if SQLite has FKs enabled.
        # Let's assume we need to create them.
        
        resp = await async_client.post("/api/v1/tenants", json={"name": "Tenant A", "slug": tenant_a_slug})
        assert resp.status_code == 201
        # We ignore the ID returned by DB because our mock forces ID=10
        tenant_a_id = 10 

        # 2. Create Tenant B
        tenant_b_slug = "tenant-b-unique"
        resp = await async_client.post("/api/v1/tenants", json={"name": "Tenant B", "slug": tenant_b_slug})
        assert resp.status_code == 201
        tenant_b_id = 20

        # 3. Create User "dupe" in Tenant A
        user_data = {
            "email": "dupe@example.com",
            "username": "dupeuser", 
            "password": "Password123!", 
            "name": "Dupe User"
        }

        # Set context to Tenant A using SLUG (Middleware uses slug from X-Tenant-ID usually?)
        # Wait, TenantMiddleware uses X-Tenant-ID header. Is it ID or SLUG?
        # src/app/middleware/tenant.py determines.
        # Usually it expects SLUG in header or subdomain.
        # Let's check test_conftest.py: header is "X-Tenant-ID": "test-tenant-integration".
        # This looks like a slug.
        
        async_client.headers["X-Tenant-ID"] = tenant_a_slug
        resp = await async_client.post("/api/v1/users", json=user_data)
        assert resp.status_code == 201
        
        # 4. Create User "dupe" in Tenant B (Should SUCCEED)
        async_client.headers["X-Tenant-ID"] = tenant_b_slug
        resp = await async_client.post("/api/v1/users", json=user_data)
        assert resp.status_code == 201, f"Failed to create in Tenant B: {resp.text}"

        # 5. Create User "dupe" in Tenant A AGAIN (Should FAIL)
        async_client.headers["X-Tenant-ID"] = tenant_a_slug
        resp = await async_client.post("/api/v1/users", json=user_data)
        assert resp.status_code == 409, "Should fail with duplicate error in same tenant"