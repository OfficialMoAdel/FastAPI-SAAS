"""Unit tests for RBAC RoleService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.rbac.crud import crud_associations
from src.app.modules.rbac.models import Permission, Role, RolePermission, UserRole
from src.app.modules.rbac.schemas import RoleCreate, RoleUpdate
from src.app.modules.rbac.service import RoleService
from src.app.modules.users.session_invalidation import InvalidationReason
from src.app.shared.exceptions import DuplicateError, ForbiddenError, ModelNotFoundError
from tests.conftest import MockUser


@pytest.fixture
def mock_db():
    """Mock database session."""
    db = AsyncMock()
    # add is synchronous in SQLAlchemy AsyncSession
    db.add = MagicMock()
    # begin_nested() returns a (sync) context manager in SQLAlchemy, not a
    # coroutine — an AsyncMock child would break `async with` in the
    # idempotent-assign savepoint, so wire a mock CM explicitly.
    db.begin_nested = MagicMock(return_value=AsyncMock())
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

        with patch(
            "src.app.modules.users.session_invalidation.invalidate_user_sessions", new_callable=AsyncMock
        ) as mock_seam:
            await role_service.delete_role(1)
        # No affected users in this fixture, so the seam is not crossed; when
        # it is, the permission flush rides along on the same call.
        mock_seam.assert_not_awaited()

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

        # execute order: permission, existing link, affected user ids. The
        # projection select, the token_version UPDATE and the fresh-version
        # read all live inside the seam, which is mocked here.
        mock_db.execute.side_effect = [
            mock_result_perm,
            mock_result_link,
            mock_affected_result,
        ]

        with patch(
            "src.app.modules.users.session_invalidation.invalidate_user_sessions",
            new_callable=AsyncMock,
        ) as mock_invalidate:
            await role_service.add_permission_to_role(1, "users:read")

        # One seam call carries the SQL bump, the commit, the permission flush
        # and the user-cache invalidation, so the affected users arrive as a
        # single batch argument rather than N per-user calls. The previous
        # assertion reached past the seam into UserService's delegate; the
        # batching contract is an interface fact now, so it is asserted here.
        mock_invalidate.assert_awaited_once()
        assert mock_invalidate.await_args.args[1] == [10, 11]
        assert mock_invalidate.await_args.kwargs["flush_permissions"] is True
        assert mock_invalidate.await_args.kwargs["commit"] is True

        # The column projection is the seam's job once the caller hands over
        # ids, and is covered by
        # tests/unit/modules/users/test_session_invalidation.py::
        # test_invalidate_user_sessions_raw_ids_projection. What this caller
        # test owns is that it does not select full user rows itself.
        assert [d.get("name") for d in mock_db.execute.call_args_list[2].args[0].column_descriptions] == ["user_id"]


class TestRoleServiceUserAssignment:
    """Tests for assigning roles to users."""

    @pytest.mark.asyncio
    async def test_assign_role_success(self, role_service, mock_db, mock_role):
        """Test assigning role to user (1B: single seam call owns bump+commit+cache+perm flush)."""
        role_service.get_role = AsyncMock(return_value=mock_role)

        # Membership check goes through the user membership seam
        # (non-deleted, tenant-scoped, no active gate).
        mock_user_record = {"id": 123, "tenant_id": MockUser.DEFAULT_TENANT_ID}

        # Mock existing assignment query (None - not assigned yet)
        mock_assignment_result = MagicMock()
        mock_assignment_result.scalar_one_or_none.return_value = None

        # Calls: (1) assignment check. The user check runs through
        # UserDirectory.member (mocked); the SQL bump + fresh
        # read + commit are owned by the invalidate_user_sessions seam.
        mock_db.execute = AsyncMock(return_value=mock_assignment_result)

        # Mock add to accept sync call
        mock_db.add = MagicMock()

        with (
            patch("src.app.modules.users.UserDirectory") as mock_user_dir_cls,
            patch(
                "src.app.modules.users.session_invalidation.invalidate_user_sessions", new_callable=AsyncMock
            ) as mock_seam,
        ):
            mock_user_dir_cls.return_value.member = AsyncMock(return_value=mock_user_record)
            await role_service.assign_role_to_user(user_id=123, role_id=mock_role.id)

            mock_user_dir_cls.return_value.member.assert_awaited_once_with(
                id=123, tenant_id=MockUser.DEFAULT_TENANT_ID
            )
            mock_db.add.assert_called_once()
            # ONE seam call replaces bump + commit + cache + perm flush.
            mock_seam.assert_awaited_once_with(
                mock_db,
                mock_user_record,
                InvalidationReason.CREDENTIAL_CHANGE,
                tenant_id=MockUser.DEFAULT_TENANT_ID,
                flush_permissions=True,
                commit=True,
            )

    @pytest.mark.asyncio
    async def test_assign_role_user_not_found(self, role_service, mock_db, mock_role):
        """Test assigning role when the user seam finds no tenant user in scope."""
        role_service.get_role = AsyncMock(return_value=mock_role)

        with patch("src.app.modules.users.UserDirectory") as mock_user_dir_cls:
            mock_user_dir_cls.return_value.member = AsyncMock(return_value=None)

            with pytest.raises(ModelNotFoundError):
                await role_service.assign_role_to_user(user_id=999, role_id=mock_role.id)

            mock_user_dir_cls.return_value.member.assert_awaited_once_with(
                id=999, tenant_id=MockUser.DEFAULT_TENANT_ID
            )
            mock_db.add.assert_not_called()
            mock_db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_assign_role_already_exists(self, role_service, mock_db, mock_role):
        """Test assigning already assigned role."""
        role_service.get_role = AsyncMock(return_value=mock_role)

        # Membership check goes through the user membership seam.
        mock_user_record = {"id": 123, "tenant_id": MockUser.DEFAULT_TENANT_ID}

        # Mock existing assignment query (Found)
        mock_assignment_result = MagicMock()
        mock_assignment_result.scalar_one_or_none.return_value = UserRole(
            user_id=123,
            role_id=mock_role.id,
            tenant_id=MockUser.DEFAULT_TENANT_ID,
        )

        # Only call: assignment check (user check is mocked via the seam).
        mock_db.execute = AsyncMock(return_value=mock_assignment_result)

        with patch("src.app.modules.users.UserDirectory") as mock_user_dir_cls:
            mock_user_dir_cls.return_value.member = AsyncMock(return_value=mock_user_record)

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
        """Test removing role from user (1B: single seam call owns bump+commit+cache+perm flush)."""
        # Membership check goes through the user membership seam
        # (non-deleted, tenant-scoped, no active gate).
        mock_user_record = {"id": 123, "tenant_id": MockUser.DEFAULT_TENANT_ID}

        with (
            patch("src.app.modules.users.UserDirectory") as mock_user_dir_cls,
            patch.object(
                crud_associations, "remove_user_role", new_callable=AsyncMock, return_value=True
            ) as mock_remove,
            patch(
                "src.app.modules.users.session_invalidation.invalidate_user_sessions", new_callable=AsyncMock
            ) as mock_seam,
        ):
            mock_user_dir_cls.return_value.member = AsyncMock(return_value=mock_user_record)
            await role_service.remove_role_from_user(user_id=123, role_id=1)

            mock_user_dir_cls.return_value.member.assert_awaited_once_with(
                id=123, tenant_id=MockUser.DEFAULT_TENANT_ID
            )
            # Atomic path: single DELETE statement, no loaded-object db.delete.
            mock_remove.assert_awaited_once_with(mock_db, role_service.tenant_id, 123, 1)
            mock_db.delete.assert_not_called()
            # ONE seam call replaces bump + commit + cache + perm flush.
            mock_seam.assert_awaited_once_with(
                mock_db,
                mock_user_record,
                InvalidationReason.CREDENTIAL_CHANGE,
                tenant_id=MockUser.DEFAULT_TENANT_ID,
                flush_permissions=True,
                commit=True,
            )

    @pytest.mark.asyncio
    async def test_remove_role_user_not_found(self, role_service, mock_db):
        """Test removing a role when the user seam finds no tenant user in scope."""
        with (
            patch("src.app.modules.users.UserDirectory") as mock_user_dir_cls,
            patch.object(crud_associations, "remove_user_role", new_callable=AsyncMock) as mock_remove,
        ):
            mock_user_dir_cls.return_value.member = AsyncMock(return_value=None)

            with pytest.raises(ModelNotFoundError):
                await role_service.remove_role_from_user(user_id=999, role_id=1)

            mock_user_dir_cls.return_value.member.assert_awaited_once_with(
                id=999, tenant_id=MockUser.DEFAULT_TENANT_ID
            )
            mock_remove.assert_not_called()


MUTATION_SITES = ["delete_role", "add_permission_to_role", "remove_permission_from_role"]


def test_every_role_mutation_crosses_the_invalidation_seam_once():
    """No role mutation may spell the transition twice.

    Three of the five role-mutation paths used to call the seam and then
    hand-roll the permission flush as a second call. That left one outcome with
    two spellings: only the two-step spelling could raise out of the flush
    after the SQL bump had already committed, and a reader could not tell which
    paths had been migrated.

    Structural check rather than a behavioural one, because the failure mode is
    structural: a second call site creeping back in.
    """
    import inspect

    from src.app.modules.rbac import service as rbac_service

    source = inspect.getsource(rbac_service)

    # No permission-cache helper is imported or called here any more.
    assert "invalidate_users_permissions_cache_batch" not in source
    assert "invalidate_user_permissions_cache" not in source

    # Every seam call in this module asks for the permission flush.
    calls = source.split("await invalidate_user_sessions(")[1:]
    assert calls, "no seam call sites found — the guard would pass vacuously"
    for block in calls:
        head = block[: block.index(")")]
        assert "flush_permissions=True" in head, f"a seam call omitted the permission flush:\n{head}"
