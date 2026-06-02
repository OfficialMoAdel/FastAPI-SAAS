"""Integration tests for RBAC Role endpoints (T017).

Tests the role CRUD endpoints with properly mocked middleware
to avoid async database connection contention.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from src.app.main import app

# Test user-agent to avoid IP filter blocking
TEST_USER_AGENT = "TestClient/1.0"

MOCK_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def mock_tenant():
    """Mock tenant object."""
    tenant = MagicMock()
    tenant.id = MOCK_TENANT_ID
    tenant.name = "Test Tenant"
    tenant.slug = "test-tenant"
    tenant.is_active = True
    return tenant


@pytest.fixture
def mock_superuser():
    """Mock superuser dict."""
    return {
        "id": 1,
        "username": "admin",
        "email": "admin@test.com",
        "is_superuser": True,
        "tenant_id": MOCK_TENANT_ID,
    }


@pytest.fixture
def mock_regular_user():
    """Mock regular user dict."""
    return {
        "id": 2,
        "username": "user",
        "email": "user@test.com",
        "is_superuser": False,
        "tenant_id": MOCK_TENANT_ID,
    }


@pytest.fixture
def mock_role():
    """Mock role object."""
    role = MagicMock()
    role.id = 1
    role.name = "Test Role"
    role.description = "A test role"
    role.tenant_id = MOCK_TENANT_ID
    role.is_system = False
    return role


class TestRBACRolesAccess:
    """Test permission-based access control for role endpoints."""

    @pytest.mark.asyncio
    async def test_unauthenticated_request_returns_401(self, mock_tenant):
        """Requests without auth should return 401."""
        with patch(
            "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
            new_callable=AsyncMock,
            return_value=mock_tenant,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get(
                    "/api/v1/roles",
                    headers={"X-Tenant-ID": "test-tenant", "User-Agent": TEST_USER_AGENT},
                )
                assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_superuser_bypasses_permission_check(self, mock_superuser, mock_tenant, mock_role):
        """Superusers should bypass permission checks."""
        with (
            patch(
                "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
                new_callable=AsyncMock,
                return_value=mock_tenant,
            ),
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                new_callable=AsyncMock,
                return_value=mock_superuser,
            ),
            patch(
                "src.app.core.rbac.dependencies.has_permission_cached",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "src.app.modules.rbac.service.RoleService.get_roles",
                new_callable=AsyncMock,
                return_value=[mock_role],
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get(
                    "/api/v1/roles",
                    headers={
                        "Authorization": "Bearer test-token",
                        "X-Tenant-ID": "test-tenant",
                        "User-Agent": TEST_USER_AGENT,
                    },
                )
                assert response.status_code == 200
                data = response.json()
                assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_user_without_permission_returns_403(self, mock_regular_user, mock_tenant):
        """Users without ROLES_READ permission should get 403."""
        with (
            patch(
                "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
                new_callable=AsyncMock,
                return_value=mock_tenant,
            ),
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                new_callable=AsyncMock,
                return_value=mock_regular_user,
            ),
            patch(
                "src.app.core.rbac.dependencies.get_user_permissions",
                new_callable=AsyncMock,
                return_value=set(),  # No permissions
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get(
                    "/api/v1/roles",
                    headers={
                        "Authorization": "Bearer test-token",
                        "X-Tenant-ID": "test-tenant",
                        "User-Agent": TEST_USER_AGENT,
                    },
                )
                assert response.status_code == 403


class TestRBACRolesCRUD:
    """Test role CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_role_success(self, mock_superuser, mock_tenant):
        """Test creating a new role."""
        created_role = MagicMock()
        created_role.id = 10
        created_role.name = "New Role"
        created_role.description = "New role description"
        created_role.tenant_id = MOCK_TENANT_ID
        created_role.is_system = False

        with (
            patch(
                "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
                new_callable=AsyncMock,
                return_value=mock_tenant,
            ),
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                new_callable=AsyncMock,
                return_value=mock_superuser,
            ),
            patch(
                "src.app.core.rbac.dependencies.has_permission_cached",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "src.app.modules.rbac.service.RoleService.create_role",
                new_callable=AsyncMock,
                return_value=created_role,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/api/v1/roles",
                    json={"name": "New Role", "description": "New role description"},
                    headers={
                        "Authorization": "Bearer test-token",
                        "X-Tenant-ID": "test-tenant",
                        "User-Agent": TEST_USER_AGENT,
                    },
                )
                assert response.status_code == 201
                data = response.json()
                assert data["name"] == "New Role"
                assert data["id"] == 10

    @pytest.mark.asyncio
    async def test_get_role_by_id(self, mock_superuser, mock_tenant, mock_role):
        """Test getting a role by ID."""
        with (
            patch(
                "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
                new_callable=AsyncMock,
                return_value=mock_tenant,
            ),
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                new_callable=AsyncMock,
                return_value=mock_superuser,
            ),
            patch(
                "src.app.core.rbac.dependencies.has_permission_cached",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "src.app.modules.rbac.service.RoleService.get_role",
                new_callable=AsyncMock,
                return_value=mock_role,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get(
                    "/api/v1/roles/1",
                    headers={
                        "Authorization": "Bearer test-token",
                        "X-Tenant-ID": "test-tenant",
                        "User-Agent": TEST_USER_AGENT,
                    },
                )
                assert response.status_code == 200
                data = response.json()
                assert data["id"] == 1
                assert data["name"] == "Test Role"

    @pytest.mark.asyncio
    async def test_get_nonexistent_role_returns_404(self, mock_superuser, mock_tenant):
        """Test getting a role that doesn't exist."""
        with (
            patch(
                "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
                new_callable=AsyncMock,
                return_value=mock_tenant,
            ),
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                new_callable=AsyncMock,
                return_value=mock_superuser,
            ),
            patch(
                "src.app.core.rbac.dependencies.has_permission_cached",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "src.app.modules.rbac.service.RoleService.get_role",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get(
                    "/api/v1/roles/999",
                    headers={
                        "Authorization": "Bearer test-token",
                        "X-Tenant-ID": "test-tenant",
                        "User-Agent": TEST_USER_AGENT,
                    },
                )
                assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_role_success(self, mock_superuser, mock_tenant):
        """Test updating a role."""
        updated_role = MagicMock()
        updated_role.id = 1
        updated_role.name = "Updated Role"
        updated_role.description = "Updated description"
        updated_role.tenant_id = MOCK_TENANT_ID
        updated_role.is_system = False

        with (
            patch(
                "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
                new_callable=AsyncMock,
                return_value=mock_tenant,
            ),
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                new_callable=AsyncMock,
                return_value=mock_superuser,
            ),
            patch(
                "src.app.core.rbac.dependencies.has_permission_cached",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "src.app.modules.rbac.service.RoleService.update_role",
                new_callable=AsyncMock,
                return_value=updated_role,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.put(
                    "/api/v1/roles/1",
                    json={"name": "Updated Role", "description": "Updated description"},
                    headers={
                        "Authorization": "Bearer test-token",
                        "X-Tenant-ID": "test-tenant",
                        "User-Agent": TEST_USER_AGENT,
                    },
                )
                assert response.status_code == 200
                data = response.json()
                assert data["name"] == "Updated Role"

    @pytest.mark.asyncio
    async def test_delete_role_success(self, mock_superuser, mock_tenant):
        """Test deleting a role."""
        with (
            patch(
                "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
                new_callable=AsyncMock,
                return_value=mock_tenant,
            ),
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                new_callable=AsyncMock,
                return_value=mock_superuser,
            ),
            patch(
                "src.app.core.rbac.dependencies.has_permission_cached",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "src.app.modules.rbac.service.RoleService.delete_role",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.delete(
                    "/api/v1/roles/1",
                    headers={
                        "Authorization": "Bearer test-token",
                        "X-Tenant-ID": "test-tenant",
                        "User-Agent": TEST_USER_AGENT,
                    },
                )
                assert response.status_code == 204


class TestRBACRolesValidation:
    """Test input validation for role endpoints."""

    @pytest.mark.asyncio
    async def test_create_role_missing_name_returns_422(self, mock_superuser, mock_tenant):
        """Test that missing name field returns validation error."""
        with (
            patch(
                "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
                new_callable=AsyncMock,
                return_value=mock_tenant,
            ),
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                new_callable=AsyncMock,
                return_value=mock_superuser,
            ),
            patch(
                "src.app.core.rbac.dependencies.has_permission_cached",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/api/v1/roles",
                    json={"description": "No name provided"},
                    headers={
                        "Authorization": "Bearer test-token",
                        "X-Tenant-ID": "test-tenant",
                        "User-Agent": TEST_USER_AGENT,
                    },
                )
                assert response.status_code == 422
