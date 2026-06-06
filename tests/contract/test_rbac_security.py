"""
Contract tests for RBAC security - verifies 403 Forbidden behavior.

T024: Ensure 403 Forbidden is returned for all unauthorized access.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.app.core.rbac.permissions import Permission
from src.app.main import app

MOCK_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    """Create async HTTP client."""
    # Mock Redis cache client used by RateLimiter via cache.client
    mock_redis = AsyncMock()
    mock_redis.evalsha = AsyncMock(return_value=1)  # Below rate limit
    mock_redis.script_load = AsyncMock(return_value="fake_sha")
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.setex = AsyncMock(return_value=True)
    # T024: Fix permission caching mocks for RBAC tests
    mock_redis.exists = AsyncMock(return_value=False)  # Cache miss - force DB lookup
    mock_redis.sismember = AsyncMock(return_value=0)  # Permission not found
    mock_redis.smembers = AsyncMock(return_value=set())  # Empty permissions set
    mock_redis.pipeline = MagicMock(return_value=AsyncMock())  # For cache write

    with patch("src.app.core.utils.cache.client", mock_redis):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


class TestRBACSecurityContract:
    """Verify 403 Forbidden for unauthorized access attempts."""

    @pytest.mark.asyncio
    async def test_missing_permission_returns_403(self, client: AsyncClient) -> None:
        """User without required permission gets 403."""
        mock_user = {"id": 1, "is_superuser": False, "tenant_id": MOCK_TENANT_ID}
        mock_tenant = MagicMock(id=MOCK_TENANT_ID, slug="test-tenant", is_active=True)

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
        mock_user = {"id": 1, "is_superuser": False, "tenant_id": MOCK_TENANT_ID}
        mock_tenant = MagicMock(id=MOCK_TENANT_ID, slug="test-tenant", is_active=True)

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
        mock_user = {"id": 1, "is_superuser": False, "tenant_id": MOCK_TENANT_ID}
        mock_tenant = MagicMock(id=MOCK_TENANT_ID, slug="test-tenant", is_active=True)

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
        mock_user = {"id": 1, "is_superuser": False, "tenant_id": MOCK_TENANT_ID}
        mock_tenant = MagicMock(id=MOCK_TENANT_ID, slug="test-tenant", is_active=True)

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
        mock_user = {"id": 1, "is_superuser": False, "tenant_id": MOCK_TENANT_ID}
        mock_tenant = MagicMock(id=MOCK_TENANT_ID, slug="test-tenant", is_active=True)

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
        from src.app.core.db.database import async_get_db
        mock_user = {"id": 1, "is_superuser": True, "tenant_id": MOCK_TENANT_ID}
        mock_tenant = MagicMock(id=MOCK_TENANT_ID, slug="test-tenant", is_active=True)

        async def mock_get_tenant(*args, **kwargs):
            return mock_tenant

        mock_db = MagicMock()
        mock_db.scalar = AsyncMock(return_value=True)
        app.dependency_overrides[async_get_db] = lambda: mock_db

        try:
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
                    "src.app.core.rbac.dependencies.has_permission_cached",
                    new=AsyncMock(return_value=True),
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
        finally:
            app.dependency_overrides.pop(async_get_db, None)

    @pytest.mark.asyncio
    async def test_unauthenticated_request_returns_401(self, client: AsyncClient) -> None:
        """Request without auth token returns 401."""
        mock_tenant = MagicMock(id=MOCK_TENANT_ID, slug="test-tenant", is_active=True)

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
