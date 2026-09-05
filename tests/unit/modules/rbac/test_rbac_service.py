"""Unit tests for RBAC RoleService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules._base.exceptions import DuplicateError, ForbiddenError, ModelNotFoundError
from src.app.modules.rbac.crud import crud_associations
from src.app.modules.rbac.models import Permission, Role, RolePermission, UserRole
from src.app.modules.rbac.schemas import RoleCreate, RoleUpdate
from src.app.modules.rbac.service import RoleService
from src.app.modules.users.session_invalidation import InvalidationReason
from tests.conftest import MockUser


@pytest.fixture
def mock_db():
    """Mock database session."""
    db = AsyncMock()
    # add is synchronous in SQLAlchemy AsyncSession
    db.add = MagicMock()
    return db


@pytest.fixture
def role_service(mock_db):
    """RoleService instance."""
    return RoleService(db=mock_db, tenant_id=MockUser.DEFAULT_TENANT_ID)


@pytest.fixture
def mock_role():
    """Mock Role object."""
    role = Role(
        tenant_id=MockUser.DEFAULT_TENANT_ID,
        name="Test Role",
        description="Test Description",
        is_system=False,
    )
    role.id = 1
    role.users = []  # Fix missing attribute error in tests
    return role


@pytest.fixture
def mock_system_role():
    """Mock System Role object."""
    role = Role(
        tenant_id=MockUser.DEFAULT_TENANT_ID,
        name="Admin",
        description="System Admin",
        is_system=True,
    )
    role.id = 2
    return role


@pytest.fixture
def mock_permission():
    """Mock Permission object."""
    perm = Permission(slug="users:read", description="Can read users")
    perm.id = 1
    perm.created_at = None
    perm.updated_at = None
    return perm


@pytest.fixture(autouse=True)
def clear_l1_cache():
    """Clear L1 cache before each test."""
    from src.app.modules.rbac.service import _L1_RBAC_CACHE

    _L1_RBAC_CACHE.clear()
    yield
    _L1_RBAC_CACHE.clear()


class TestRoleServiceGet:
    """Tests for role retrieval."""

    @pytest.mark.asyncio
    async def test_get_roles_success(self, role_service, mock_db, mock_role):
        """Test getting all roles."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_role]
        mock_db.execute.return_value = mock_result

        result = await role_service.get_roles()

        assert len(result) == 1
        assert result[0].name == "Test Role"
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_role_success(self, role_service, mock_db, mock_role):
        """Test getting a specific role."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_role
        mock_db.execute.return_value = mock_result

        result = await role_service.get_role(1)

        assert result == mock_role

    @pytest.mark.asyncio
    async def test_get_role_not_found(self, role_service, mock_db):
        """Test getting non-existent role raises error."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(ModelNotFoundError):
            await role_service.get_role(999)


class TestRoleServiceCreate:
    """Tests for role creation."""

    @pytest.mark.asyncio
    async def test_create_role_success(self, role_service, mock_db):
        """Test successful role creation."""
        # Mock get_role_by_name returning None (no duplicate)
        # We mock the method on the instance to avoid actual DB query
        role_service.get_role_by_name = AsyncMock(return_value=None)

        role_in = RoleCreate(name="New Role", description="Desc")

        # We use the real Role class, so no patching needed for Role.
        # DB operations are on mock_db which is fine.

        result = await role_service.create_role(role_in)

        assert result.name == "New Role"
        assert result.tenant_id == MockUser.DEFAULT_TENANT_ID
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_role_duplicate(self, role_service, mock_db, mock_role):
        """Test creation fails with duplicate name."""
        # Mock get_role_by_name returning existing role
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_role
        mock_db.execute.return_value = mock_result

        role_in = RoleCreate(name="Test Role", description="Desc")

        with pytest.raises(DuplicateError):
            await role_service.create_role(role_in)


class TestRoleServiceUpdate:
    """Tests for role updates."""

    @pytest.mark.asyncio
    async def test_update_role_success(self, role_service, mock_db, mock_role):
        """Test successful role update."""
        # Mock get_role
        role_service.get_role = AsyncMock(return_value=mock_role)
        # Mock get_role_by_name (for duplicate check) -> None
        role_service.get_role_by_name = AsyncMock(return_value=None)

        role_in = RoleUpdate(name="Updated Role", description="New Desc")

        result = await role_service.update_role(1, role_in)

        assert result.name == "Updated Role"
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_system_role_fails(self, role_service, mock_db, mock_system_role):
        """Test updating system role raises error."""
        role_service.get_role = AsyncMock(return_value=mock_system_role)

        role_in = RoleUpdate(name="Hacked Admin")

        with pytest.raises(ForbiddenError) as exc:
            await role_service.update_role(2, role_in)

        assert exc.value.message == "System roles cannot be modified"

    @pytest.mark.asyncio
    async def test_update_role_duplicate_name(self, role_service, mock_db, mock_role):
        """Test update fails if name exists."""
        role_service.get_role = AsyncMock(return_value=mock_role)
        # Mock collision
        other_role = Role(tenant_id=MockUser.DEFAULT_TENANT_ID, name="Existing Role", description="Desc")
        other_role.id = 2
        role_service.get_role_by_name = AsyncMock(return_value=other_role)

        role_in = RoleUpdate(name="Existing Role")

        with pytest.raises(DuplicateError):
            await role_service.update_role(1, role_in)


class TestRoleServiceDelete:
    """Tests for role deletion."""

    @pytest.mark.asyncio
    async def test_delete_role_success(self, role_service, mock_db, mock_role):
        """Test successful role deletion."""
        role_service.get_role = AsyncMock(return_value=mock_role)

        # The cache-invalidation fix queries affected users before deleting the role.
        mock_affected_result = MagicMock()
        mock_affected_result.scalars.return_value.all.return_value = []  # No users with this role
        mock_db.execute = AsyncMock(return_value=mock_affected_result)

        with patch("src.app.modules.rbac.service.invalidate_user_permissions_cache", new_callable=AsyncMock):
            await role_service.delete_role(1)

        mock_db.delete.assert_called_once_with(mock_role)
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_system_role_fails(self, role_service, mock_db, mock_system_role):
        """Test deleting system role raises error."""
        role_service.get_role = AsyncMock(return_value=mock_system_role)

        with pytest.raises(ForbiddenError):
            await role_service.delete_role(2)


class TestRoleServicePermissions:
    """Tests for permission management."""

    @pytest.mark.asyncio
    async def test_add_permission_success(self, role_service, mock_db, mock_role, mock_permission):
        """Test adding permission to role."""
        role_service.get_role = AsyncMock(return_value=mock_role)

        # Mock permission query
        mock_result_perm = MagicMock()
        mock_result_perm.scalar_one_or_none.return_value = mock_permission

        # Mock existing link query (None = not assigned yet)
        mock_result_link = MagicMock()
        mock_result_link.scalar_one_or_none.return_value = None

        # Mock affected users
        mock_affected_result = MagicMock()
        mock_affected_result.scalars.return_value.all.return_value = []

        # Chain execute results: [permission_query, existing_link_query, affected_users]
        mock_db.execute.side_effect = [mock_result_perm, mock_result_link, mock_affected_result]

        await role_service.add_permission_to_role(1, "users:read")

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_permission_success(self, role_service, mock_db, mock_role, mock_permission):
        """Test removing permission from role (single-statement atomic delete)."""
        role_service.get_role = AsyncMock(return_value=mock_role)

        with (
            patch(
                "src.app.modules.rbac.service.crud_permissions.get_by_slug",
                new_callable=AsyncMock,
                return_value=mock_permission,
            ),
            patch.object(
                crud_associations, "remove_role_permission", new_callable=AsyncMock, return_value=True
            ) as mock_remove,
            patch.object(crud_associations, "get_role_affected_user_ids", new_callable=AsyncMock, return_value=[]),
            patch.object(role_service, "_invalidate_role_cache", new_callable=AsyncMock) as mock_invalidate,
        ):
            await role_service.remove_permission_from_role(1, "users:read")

            # Atomic path: single DELETE statement, no loaded-object db.delete.
            mock_remove.assert_awaited_once_with(mock_db, 1, mock_permission.id)
            mock_db.delete.assert_not_called()
            mock_db.commit.assert_called_once()
            mock_invalidate.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_remove_permission_not_assigned_noop(self, role_service, mock_db, mock_role, mock_permission):
        """Removing a non-assigned permission is a no-op (no commit, no invalidation)."""
        role_service.get_role = AsyncMock(return_value=mock_role)

        with (
            patch(
                "src.app.modules.rbac.service.crud_permissions.get_by_slug",
                new_callable=AsyncMock,
                return_value=mock_permission,
            ),
            patch.object(crud_associations, "remove_role_permission", new_callable=AsyncMock, return_value=False),
            patch.object(role_service, "_invalidate_role_cache", new_callable=AsyncMock) as mock_invalidate,
        ):
            await role_service.remove_permission_from_role(1, "users:read")

            mock_db.commit.assert_not_called()
            mock_invalidate.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_role_permissions(self, role_service, mock_db, mock_role, mock_permission):
        """Test getting role permissions."""
        role_service.get_role = AsyncMock(return_value=mock_role)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_permission]
        mock_db.execute.return_value = mock_result

        result = await role_service.get_role_permissions(1)

        assert len(result) == 1
        assert result[0].slug == "users:read"

    @pytest.mark.asyncio
    async def test_add_permission_system_role_fails(self, role_service, mock_db, mock_system_role):
        """Test adding permission to system role fails."""
        role_service.get_role = AsyncMock(return_value=mock_system_role)

        with pytest.raises(ForbiddenError) as exc:
            await role_service.add_permission_to_role(2, "users:read")

        assert exc.value.message == "System role permissions cannot be modified"

    @pytest.mark.asyncio
    async def test_add_permission_not_found(self, role_service, mock_db, mock_role):
        """Test adding non-existent permission fails."""
        role_service.get_role = AsyncMock(return_value=mock_role)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(ModelNotFoundError):
            await role_service.add_permission_to_role(1, "unknown")

    @pytest.mark.asyncio
    async def test_add_permission_already_exists(self, role_service, mock_db, mock_role, mock_permission):
        """Test adding existing permission is idempotent."""
        role_service.get_role = AsyncMock(return_value=mock_role)

        mock_result_perm = MagicMock()
        mock_result_perm.scalar_one_or_none.return_value = mock_permission

        # Existing link found
        mock_result_link = MagicMock()
        mock_result_link.scalar_one_or_none.return_value = RolePermission(role_id=1, permission_id=1)

        # Mock affected users
        mock_affected_result = MagicMock()
        mock_affected_result.scalars.return_value.all.return_value = []

        mock_db.execute.side_effect = [mock_result_perm, mock_result_link, mock_affected_result]

        await role_service.add_permission_to_role(1, "users:read")

        mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_remove_permission_system_role_fails(self, role_service, mock_db, mock_system_role):
        """Test removing permission from system role fails."""
        role_service.get_role = AsyncMock(return_value=mock_system_role)

        with pytest.raises(ForbiddenError):
            await role_service.remove_permission_from_role(2, "users:read")

    @pytest.mark.asyncio
    async def test_remove_permission_not_found(self, role_service, mock_db, mock_role):
        """Test removing non-existent permission fails."""
        role_service.get_role = AsyncMock(return_value=mock_role)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(ModelNotFoundError):
            await role_service.remove_permission_from_role(1, "unknown")

    @pytest.mark.asyncio
    async def test_add_permission_invalidates_affected_users_in_batch(
        self, role_service, mock_db, mock_role, mock_permission
    ):
        """N3: affected users are projected (no full rows) and invalidated via
        the single-pipeline batch method, not a per-user loop."""
        role_service.get_role = AsyncMock(return_value=mock_role)

        mock_result_perm = MagicMock()
        mock_result_perm.scalar_one_or_none.return_value = mock_permission

        mock_result_link = MagicMock()
        mock_result_link.scalar_one_or_none.return_value = None

        mock_affected_result = MagicMock()
        mock_affected_result.scalars.return_value.all.return_value = [10, 11]

        projected_rows = [("row-10",), ("row-11",)]
        mock_users_result = MagicMock()
        mock_users_result.all.return_value = projected_rows

        # execute order: permission, existing link, affected user ids,
        # projected user columns, token_version UPDATE
        mock_db.execute.side_effect = [
            mock_result_perm,
            mock_result_link,
            mock_affected_result,
            mock_users_result,
            MagicMock(),
        ]

        with (
            patch(
                "src.app.modules.users.service.UserService._invalidate_user_cache_batch",
                new_callable=AsyncMock,
            ) as mock_batch,
            patch(
                "src.app.modules.rbac.service.invalidate_users_permissions_cache_batch",
                new_callable=AsyncMock,
            ),
        ):
            await role_service.add_permission_to_role(1, "users:read")
        mock_batch.assert_awaited_once_with(
            projected_rows, bump_token_version=True, reason=InvalidationReason.CREDENTIAL_CHANGE
        )

        users_stmt = mock_db.execute.call_args_list[3].args[0]
        assert users_stmt.column_descriptions[0]["name"] == "id"
        assert {c["name"] for c in users_stmt.column_descriptions} == {
            "id",
            "email",
            "username",
            "tenant_id",
            "token_version",
        }


class TestRoleServiceUserAssignment:
    """Tests for assigning roles to users."""

    @pytest.mark.asyncio
    async def test_assign_role_success(self, role_service, mock_db, mock_role):
        """Test assigning role to user."""
        role_service.get_role = AsyncMock(return_value=mock_role)

        # Mock user query result (user exists)
        mock_user_result = MagicMock()
        mock_user_result.scalar_one_or_none.return_value = MagicMock(id=123, tenant_id=MockUser.DEFAULT_TENANT_ID)

        # Mock existing assignment query (None - not assigned yet)
        mock_assignment_result = MagicMock()
        mock_assignment_result.scalar_one_or_none.return_value = None

        # Mock token_version UPDATE result (no return value needed)
        mock_update_result = MagicMock()

        # Calls: (1) user check, (2) assignment check, (3) token_version UPDATE
        mock_db.execute = AsyncMock(side_effect=[mock_user_result, mock_assignment_result, mock_update_result])

        # Mock add to accept sync call
        mock_db.add = MagicMock()

        with patch(
            "src.app.modules.rbac.service.invalidate_user_permissions_cache", new_callable=AsyncMock
        ) as mock_cache:
            await role_service.assign_role_to_user(user_id=123, role_id=mock_role.id)

            mock_db.add.assert_called_once()
            # One commit: both UserRole insert and token_version update happen in same transaction
            assert mock_db.commit.call_count == 1
            mock_cache.assert_called_once_with(123, MockUser.DEFAULT_TENANT_ID)

    @pytest.mark.asyncio
    async def test_assign_role_already_exists(self, role_service, mock_db, mock_role):
        """Test assigning already assigned role."""
        role_service.get_role = AsyncMock(return_value=mock_role)

        # Mock user query result (user exists)
        mock_user_result = MagicMock()
        mock_user_result.scalar_one_or_none.return_value = MagicMock(id=123, tenant_id=MockUser.DEFAULT_TENANT_ID)

        # Mock existing assignment query (Found)
        mock_assignment_result = MagicMock()
        mock_assignment_result.scalar_one_or_none.return_value = UserRole(
            user_id=123,
            role_id=mock_role.id,
            tenant_id=MockUser.DEFAULT_TENANT_ID,
        )

        # First call: user check, Second call: assignment check
        mock_db.execute = AsyncMock(side_effect=[mock_user_result, mock_assignment_result])

        await role_service.assign_role_to_user(user_id=123, role_id=mock_role.id)

        mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_user_roles(self, role_service, mock_db, mock_role):
        """Test getting user roles."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_role]
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await role_service.get_user_roles(123)

        assert len(result) == 1
        assert result[0].name == "Test Role"

    @pytest.mark.asyncio
    async def test_remove_role_success(self, role_service, mock_db):
        """Test removing role from user (single-statement atomic delete)."""
        # Mock user tenant-isolation check (user exists in tenant)
        mock_user_result = MagicMock()
        mock_user_result.scalar_one_or_none.return_value = MagicMock(id=123, tenant_id=MockUser.DEFAULT_TENANT_ID)
        mock_db.execute = AsyncMock(return_value=mock_user_result)

        with (
            patch.object(
                crud_associations, "remove_user_role", new_callable=AsyncMock, return_value=True
            ) as mock_remove,
            patch.object(crud_associations, "bump_user_token_version", new_callable=AsyncMock),
            patch(
                "src.app.modules.rbac.service.invalidate_user_permissions_cache", new_callable=AsyncMock
            ) as mock_cache,
            patch("src.app.modules.users.service.UserService") as mock_user_service_cls,
        ):
            mock_user_service_cls.return_value._invalidate_user_cache = AsyncMock()

            await role_service.remove_role_from_user(user_id=123, role_id=1)

            # Atomic path: single DELETE statement, no loaded-object db.delete.
            mock_remove.assert_awaited_once_with(mock_db, role_service.tenant_id, 123, 1)
            mock_db.delete.assert_not_called()
            # Single commit after delete + token_version update
            assert mock_db.commit.call_count == 1
            mock_cache.assert_called_once()
