"""P1-1: role assignment is gated on roles:assign, not users:manage.

Holding users:manage — the permission for creating and editing accounts — must
not let a tenant operator grant roles, including system roles. The two slugs
existed in the enum and the permissions table but were enforced nowhere.

The permission mock derives its answer from the slug the ROUTE asks for, so
these tests fail if the gate is switched back to users:manage.
"""

import inspect
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.app.core.rbac.permissions import Permission
from tests.conftest import MockUser
from tests.helpers.auth import override_test_principal

TEST_USER_AGENT = "TestClient/1.0"
TENANT_HEADERS = {"X-Tenant-ID": str(MockUser.DEFAULT_TENANT_ID)}

ASSIGN_PATH = "/api/v1/roles/1/users/2"
REMOVE_PATH = "/api/v1/roles/1/users/2"


@pytest_asyncio.fixture
async def client(get_test_app):
    async with AsyncClient(
        transport=ASGITransport(app=get_test_app),
        base_url="http://test",
    ) as ac:
        ac.headers["User-Agent"] = TEST_USER_AGENT
        yield ac


def _principal() -> tuple[dict, MagicMock]:
    user = {"id": 2, "is_superuser": False, "username": "operator"}
    tenant = MagicMock(id=MockUser.DEFAULT_TENANT_ID, slug="test-tenant")
    return user, tenant


def _permission_gate(granted: set[str]):
    """Answer the gate from the GRANTED SLUGS, so the test pins the route's slug.

    A constant return_value would let these tests pass no matter which slug the
    route demands. Here the decision is derived from the slug the route asks for.
    """

    async def _has_permission_cached(_user_id, _tenant_id, permission_slug):
        return permission_slug in granted

    return patch(
        "src.app.core.rbac.dependencies.has_permission_cached",
        new_callable=AsyncMock,
        side_effect=_has_permission_cached,
    )


def _granted_slugs(slugs: set[str]):
    return patch(
        "src.app.core.rbac.dependencies.get_user_permissions",
        new_callable=AsyncMock,
        return_value=slugs,
    )


def _tenant_patch(tenant):
    return patch(
        "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
        new_callable=AsyncMock,
        return_value=tenant,
    )


@pytest.mark.parametrize(
    ("path", "method"),
    [(ASSIGN_PATH, "POST"), (REMOVE_PATH, "DELETE")],
)
@pytest.mark.asyncio
async def test_role_mutation_requires_roles_assign(client: AsyncClient, path: str, method: str) -> None:
    """A users:manage-only principal is denied — the gate is roles:assign."""
    user, tenant = _principal()

    with (
        _tenant_patch(tenant),
        override_test_principal(user, tenant),
        _granted_slugs({"USERS_MANAGE"}),
        _permission_gate({"users:manage"}),
    ):
        response = await client.request(method, path, headers=TENANT_HEADERS)

    assert response.status_code == 403, (
        f"{method} {path} allowed a users:manage-only principal: {response.text[:200]}"
    )


@pytest.mark.parametrize(
    ("path", "method"),
    [(ASSIGN_PATH, "POST"), (REMOVE_PATH, "DELETE")],
)
@pytest.mark.asyncio
async def test_role_mutation_allowed_with_roles_assign(client: AsyncClient, path: str, method: str) -> None:
    """A roles:assign holder is allowed — the gate is not over-tightened."""
    user, tenant = _principal()

    with (
        _tenant_patch(tenant),
        override_test_principal(user, tenant),
        _granted_slugs({"ROLES_ASSIGN"}),
        _permission_gate({"roles:assign"}),
        patch("src.app.modules.rbac.service.RoleService.assign_role_to_user", new_callable=AsyncMock),
        patch("src.app.modules.rbac.service.RoleService.remove_role_from_user", new_callable=AsyncMock),
    ):
        response = await client.request(method, path, headers=TENANT_HEADERS)

    assert response.status_code == 204, f"{method} {path} denied a roles:assign holder: {response.text[:200]}"


class TestSystemRoleGuard:
    """assign_role_to_user refuses system roles, like its siblings do."""

    @pytest.mark.asyncio
    async def test_assign_system_role_is_forbidden(self) -> None:
        from src.app.modules.rbac.service import RoleService
        from src.app.shared.exceptions import ForbiddenError

        service = RoleService.__new__(RoleService)
        service.tenant_id = 1
        service.db = MagicMock()

        system_role = MagicMock()
        system_role.is_system = True

        with patch.object(RoleService, "get_role", new_callable=AsyncMock, return_value=system_role):
            with pytest.raises(ForbiddenError):
                await service.assign_role_to_user(user_id=2, role_id=1)

    @pytest.mark.asyncio
    async def test_assign_non_system_role_proceeds(self) -> None:
        """Anti-over-fix: a custom role still reaches the user-membership check."""
        from src.app.modules.rbac.service import RoleService
        from src.app.shared.exceptions import ModelNotFoundError

        service = RoleService.__new__(RoleService)
        service.tenant_id = 1
        service.db = MagicMock()

        custom_role = MagicMock()
        custom_role.is_system = False

        with (
            patch.object(RoleService, "get_role", new_callable=AsyncMock, return_value=custom_role),
            patch("src.app.modules.users.UserDirectory") as directory_cls,
        ):
            directory_cls.return_value.member = AsyncMock(return_value=None)
            # A user outside the tenant must still be rejected downstream.
            with pytest.raises(ModelNotFoundError):
                await service.assign_role_to_user(user_id=2, role_id=1)

    @pytest.mark.asyncio
    async def test_remove_system_role_still_revokes(self) -> None:
        """Revocation must keep working for system roles — removal is not blocked.

        Behavioral, not a docstring probe: seeds a membership on a system role
        and asserts the row is actually deleted. An is_system guard added to
        remove_role_from_user would raise here instead.
        """
        from src.app.modules.rbac.service import RoleService

        service = RoleService.__new__(RoleService)
        service.tenant_id = 1
        service.db = MagicMock()

        user_record = MagicMock()
        user_record.id = 2

        system_role = MagicMock()
        system_role.is_system = True

        # Patching get_role keeps a mutant's await well-formed, so a guard
        # added to this method fails as ForbiddenError (the real reason)
        # rather than a mock TypeError.
        with (
            patch("src.app.modules.users.UserDirectory") as directory_cls,
            patch.object(RoleService, "get_role", new_callable=AsyncMock, return_value=system_role),
            patch(
                "src.app.modules.rbac.service.crud_associations.remove_user_role",
                new_callable=AsyncMock,
                return_value=True,
            ) as remove_mock,
            patch("src.app.modules.users.session_invalidation.invalidate_user_sessions", new_callable=AsyncMock),
        ):
            directory_cls.return_value.member = AsyncMock(return_value=user_record)
            await service.remove_role_from_user(user_id=2, role_id=1)

        # The membership was deleted, not short-circuited by a guard.
        remove_mock.assert_awaited_once()
        assert remove_mock.await_args[0][2:] == (2, 1)


class TestPermissionSlugs:
    """The slugs stay distinct — collapsing them would re-open the hole."""

    def test_slugs_are_distinct(self) -> None:
        assert Permission.ROLES_ASSIGN.value == "roles:assign"
        assert Permission.USERS_MANAGE.value == "users:manage"
        assert Permission.ROLES_ASSIGN != Permission.USERS_MANAGE

    def test_only_role_mutation_routes_consume_roles_assign(self) -> None:
        """Exactly the two role-mutation routes consume roles:assign."""
        from src.app.modules.rbac import router as rbac_router

        source = inspect.getsource(rbac_router)
        consumers = re.findall(r"requires_permission\(Permission\.(\w+)\)", source)
        assert consumers.count("ROLES_ASSIGN") == 2, f"unexpected roles:assign consumers: {consumers}"
