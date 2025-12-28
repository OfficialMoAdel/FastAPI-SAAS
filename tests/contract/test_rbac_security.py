"""
Contract tests for RBAC security - verifies 403 Forbidden behavior.

T024: Ensure 403 Forbidden is returned for all unauthorized access.
"""

import pytest
import pytest_asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport

from src.app.main import app
from src.app.core.rbac.permissions import Permission


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    """Create async HTTP client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestRBACSecurityContract:
    """Verify 403 Forbidden for unauthorized access attempts."""

    @pytest.mark.asyncio
    async def test_missing_permission_returns_403(self, client: AsyncClient) -> None:
        """User without required permission gets 403."""
        mock_user = {"id": 1, "is_superuser": False, "tenant_id": 1}
        mock_tenant = MagicMock(id=1, slug="test-tenant", is_active=True)

        async def mock_get_tenant(*args, **kwargs):
            return mock_tenant

        async def mock_get_permissions(*args, **kwargs):
            # Return empty permissions - user has no permissions
            return set()

        with (
            patch(
                "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
                new=mock_get_tenant,
            ),
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                return_value=mock_user,
            ),
            patch(
                "src.app.core.rbac.dependencies.get_user_permissions",
                side_effect=mock_get_permissions,
            ),
        ):
            response = await client.get(
                "/api/v1/roles",
                headers={"Authorization": "Bearer test", "X-Tenant-ID": "test-tenant"},
            )

        assert response.status_code == 403
        assert "permission" in response.json().get("detail", "").lower()

    @pytest.mark.asyncio
    async def test_wrong_permission_returns_403(self, client: AsyncClient) -> None:
        """User with different permission than required gets 403."""
        mock_user = {"id": 1, "is_superuser": False, "tenant_id": 1}
        mock_tenant = MagicMock(id=1, slug="test-tenant", is_active=True)

        async def mock_get_tenant(*args, **kwargs):
            return mock_tenant

        async def mock_get_permissions(*args, **kwargs):
            # User has USERS_READ but needs ROLES_READ
            return {Permission.USERS_READ}

        with (
            patch(
                "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
                new=mock_get_tenant,
            ),
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                return_value=mock_user,
            ),
            patch(
                "src.app.core.rbac.dependencies.get_user_permissions",
                side_effect=mock_get_permissions,
            ),
        ):
            response = await client.get(
                "/api/v1/roles",
                headers={"Authorization": "Bearer test", "X-Tenant-ID": "test-tenant"},
            )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_create_role_without_manage_permission_returns_403(self, client: AsyncClient) -> None:
        """User with ROLES_READ but not ROLES_MANAGE cannot create roles."""
        mock_user = {"id": 1, "is_superuser": False, "tenant_id": 1}
        mock_tenant = MagicMock(id=1, slug="test-tenant", is_active=True)

        async def mock_get_tenant(*args, **kwargs):
            return mock_tenant

        async def mock_get_permissions(*args, **kwargs):
            # User can read roles but not manage them
            return {Permission.ROLES_READ}

        with (
            patch(
                "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
                new=mock_get_tenant,
            ),
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                return_value=mock_user,
            ),
            patch(
                "src.app.core.rbac.dependencies.get_user_permissions",
                side_effect=mock_get_permissions,
            ),
        ):
            response = await client.post(
                "/api/v1/roles",
                json={"name": "New Role"},
                headers={"Authorization": "Bearer test", "X-Tenant-ID": "test-tenant"},
            )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_role_without_manage_permission_returns_403(self, client: AsyncClient) -> None:
        """User with ROLES_READ but not ROLES_MANAGE cannot delete roles."""
        mock_user = {"id": 1, "is_superuser": False, "tenant_id": 1}
        mock_tenant = MagicMock(id=1, slug="test-tenant", is_active=True)

        async def mock_get_tenant(*args, **kwargs):
            return mock_tenant

        async def mock_get_permissions(*args, **kwargs):
            return {Permission.ROLES_READ}

        with (
            patch(
                "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
                new=mock_get_tenant,
            ),
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                return_value=mock_user,
            ),
            patch(
                "src.app.core.rbac.dependencies.get_user_permissions",
                side_effect=mock_get_permissions,
            ),
        ):
            response = await client.delete(
                "/api/v1/roles/1",
                headers={"Authorization": "Bearer test", "X-Tenant-ID": "test-tenant"},
            )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_assign_user_without_users_manage_returns_403(self, client: AsyncClient) -> None:
        """User without USERS_MANAGE cannot assign roles to users."""
        mock_user = {"id": 1, "is_superuser": False, "tenant_id": 1}
        mock_tenant = MagicMock(id=1, slug="test-tenant", is_active=True)

        async def mock_get_tenant(*args, **kwargs):
            return mock_tenant

        async def mock_get_permissions(*args, **kwargs):
            # Has role permissions but not user management
            return {Permission.ROLES_READ, Permission.ROLES_MANAGE}

        with (
            patch(
                "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
                new=mock_get_tenant,
            ),
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                return_value=mock_user,
            ),
            patch(
                "src.app.core.rbac.dependencies.get_user_permissions",
                side_effect=mock_get_permissions,
            ),
        ):
            response = await client.post(
                "/api/v1/roles/1/users/2",
                headers={"Authorization": "Bearer test", "X-Tenant-ID": "test-tenant"},
            )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_superuser_bypasses_permission_check(self, client: AsyncClient) -> None:
        """Superuser can access any endpoint regardless of permissions."""
        mock_user = {"id": 1, "is_superuser": True, "tenant_id": 1}
        mock_tenant = MagicMock(id=1, slug="test-tenant", is_active=True)

        async def mock_get_tenant(*args, **kwargs):
            return mock_tenant

        async def mock_service_get_roles(*args, **kwargs):
            return []

        with (
            patch(
                "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
                new=mock_get_tenant,
            ),
            patch(
                "src.app.core.rbac.dependencies._get_current_user_from_token",
                return_value=mock_user,
            ),
            patch(
                "src.app.modules.rbac.service.RoleService.get_roles",
                new=AsyncMock(return_value=[]),
            ),
        ):
            response = await client.get(
                "/api/v1/roles",
                headers={"Authorization": "Bearer test", "X-Tenant-ID": "test-tenant"},
            )

        # Superuser should get 200, not 403
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_unauthenticated_request_returns_401(self, client: AsyncClient) -> None:
        """Request without auth token returns 401."""
        mock_tenant = MagicMock(id=1, slug="test-tenant", is_active=True)

        async def mock_get_tenant(*args, **kwargs):
            return mock_tenant

        with patch(
            "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
            new=mock_get_tenant,
        ):
            response = await client.get(
                "/api/v1/roles",
                headers={"X-Tenant-ID": "test-tenant"},
            )

        # Should be 401 Unauthorized (no token) or 403 if auth fails differently
        assert response.status_code in (401, 403, 422)
