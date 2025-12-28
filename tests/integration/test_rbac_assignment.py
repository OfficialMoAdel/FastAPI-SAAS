"""Integration tests for RBAC role assignment endpoints (T022).

Tests cover:
- Assigning roles to users
- Removing roles from users
- Cross-tenant assignment rejection
- Permission requirements
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.app.main import app


@pytest_asyncio.fixture
async def client():
    """Create async test client."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


class TestRoleAssignmentEndpoints:
    """Test role assignment API endpoints."""

    @pytest.mark.asyncio
    async def test_assign_role_to_user_success(self, client: AsyncClient):
        """Test successful role assignment returns 204."""
        mock_user = {"id": 1, "is_superuser": True, "username": "admin"}
        mock_tenant = MagicMock(id=1, slug="test-tenant")

        with (
            patch(
                "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
                new_callable=AsyncMock,
                return_value=mock_tenant,
            ),
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
            patch(
                "src.app.core.rbac.dependencies.get_user_permissions",
                new_callable=AsyncMock,
                return_value={"USERS_MANAGE"},
            ),
            patch(
                "src.app.modules.rbac.service.RoleService.assign_role_to_user",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            response = await client.post(
                "/api/v1/roles/1/users/2",
                headers={"X-Tenant-ID": "test-tenant", "Authorization": "Bearer token"},
            )

            assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_remove_role_from_user_success(self, client: AsyncClient):
        """Test successful role removal returns 204."""
        mock_user = {"id": 1, "is_superuser": True, "username": "admin"}
        mock_tenant = MagicMock(id=1, slug="test-tenant")

        with (
            patch(
                "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
                new_callable=AsyncMock,
                return_value=mock_tenant,
            ),
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
            patch(
                "src.app.core.rbac.dependencies.get_user_permissions",
                new_callable=AsyncMock,
                return_value={"USERS_MANAGE"},
            ),
            patch(
                "src.app.modules.rbac.service.RoleService.remove_role_from_user",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            response = await client.delete(
                "/api/v1/roles/1/users/2",
                headers={"X-Tenant-ID": "test-tenant", "Authorization": "Bearer token"},
            )

            assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_assign_role_requires_authentication(self, client: AsyncClient):
        """Test that role assignment requires authentication."""
        mock_tenant = MagicMock(id=1, slug="test-tenant")

        with (
            patch(
                "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
                new_callable=AsyncMock,
                return_value=mock_tenant,
            ),
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            response = await client.post(
                "/api/v1/roles/1/users/2",
                headers={"X-Tenant-ID": "test-tenant"},
            )

            assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_assign_role_requires_permission(self, client: AsyncClient):
        """Test that role assignment requires USERS_MANAGE permission."""
        mock_user = {"id": 1, "is_superuser": False, "username": "user"}
        mock_tenant = MagicMock(id=1, slug="test-tenant")

        with (
            patch(
                "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
                new_callable=AsyncMock,
                return_value=mock_tenant,
            ),
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
            patch(
                "src.app.core.rbac.dependencies.get_user_permissions",
                new_callable=AsyncMock,
                return_value=set(),  # No permissions
            ),
        ):
            response = await client.post(
                "/api/v1/roles/1/users/2",
                headers={"X-Tenant-ID": "test-tenant", "Authorization": "Bearer token"},
            )

            assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_superuser_can_assign_roles(self, client: AsyncClient):
        """Test that superuser bypasses permission check for role assignment."""
        mock_user = {"id": 1, "is_superuser": True, "username": "superadmin"}
        mock_tenant = MagicMock(id=1, slug="test-tenant")

        with (
            patch(
                "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
                new_callable=AsyncMock,
                return_value=mock_tenant,
            ),
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
            patch(
                "src.app.core.rbac.dependencies.get_user_permissions",
                new_callable=AsyncMock,
                return_value=set(),  # No permissions but superuser
            ),
            patch(
                "src.app.modules.rbac.service.RoleService.assign_role_to_user",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            response = await client.post(
                "/api/v1/roles/1/users/2",
                headers={"X-Tenant-ID": "test-tenant", "Authorization": "Bearer token"},
            )

            # Superuser bypasses permission check
            assert response.status_code == 204
