"""Regression tests for Cluster C (RBAC / permissions) fixes.

Each test pins behaviour that WAS BROKEN before the fix and MUST stay fixed.

C1 — the bitmask singleflight task owns its OWN database session, so a
      loser's await can never resume on the winner's request-scoped session.
C2 — ``update_role`` invalidates the role cache, like every sibling role
      mutation does.
C3 — the permission join's safety bound is LOUD: a truncated permission
      set is refused, never cached as if it were complete.
C4 — the role-permission queries carry the tenant scope IN THE STATEMENT.
C5 — ``UserRoleRead`` describes the association table it names.

C6 (the superuser tenant rule) was verified CLEAN by the review, so there
is nothing to pin here — a test for correct-by-inspection code would only
restate the implementation.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

# =============================================================================
# C1 — the singleflight task owns its own session
# =============================================================================


class TestSingleflightTaskOwnsItsSession:
    """The task is awaited by OTHER requests, so it must not run on one
    request's session."""

    def test_compute_and_cache_bitmask_takes_no_session(self):
        """The task must not accept a session — that IS the defect."""
        from src.app.core.rbac.dependencies import _compute_and_cache_bitmask

        params = inspect.signature(_compute_and_cache_bitmask).parameters
        assert "db" not in params, (
            "C1 regression: _compute_and_cache_bitmask accepts a session again — the "
            "shared task would run on the winner's request-scoped session"
        )
        assert "session" not in params

    def test_public_permission_helpers_take_no_session(self):
        """No session may reach the shared task through the public API."""
        from src.app.core.rbac import dependencies as deps

        for fn in (deps.get_cached_permissions_bitmask, deps.has_permission_cached):
            params = inspect.signature(fn).parameters
            assert "db" not in params, (
                f"C1 regression: {fn.__name__} accepts a session again"
            )

    def test_the_dependency_no_longer_injects_a_session(self):
        """``requires_permission`` must not open a session it never uses."""
        from src.app.core.rbac.dependencies import requires_permission

        checker = requires_permission("users:read")
        params = inspect.signature(checker).parameters
        assert "db" not in params, (
            "C1 regression: requires_permission injects a request-scoped session again"
        )


class TestSingleflightTaskDoesNotShareSessionsAcrossRequests:
    """Two concurrent misses must not make the loser await the winner's
    request-scoped session."""

    @pytest.mark.asyncio
    async def test_concurrent_misses_use_one_task_and_one_session(self):
        import asyncio

        from src.app.core.rbac import dependencies as deps
        from src.app.core.utils import cache as cache_module

        opened: list[int] = []
        queries: list[object] = []

        result = MagicMock()
        result.scalars.return_value.all.return_value = ["users:read"]

        task_db = AsyncMock()

        async def _execute(*args, **kwargs):
            queries.append(args)
            return result

        task_db.execute.side_effect = _execute

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _local_session():
            opened.append(1)
            yield task_db

        fake_redis = AsyncMock()
        fake_redis.get.return_value = None

        deps._bitmask_l1.clear()
        with (
            patch.object(cache_module, "client", fake_redis),
            patch("src.app.core.db.database.local_session", _local_session),
        ):
            tasks = [
                asyncio.create_task(deps.get_cached_permissions_bitmask(1, "tenant-a"))
                for _ in range(5)
            ]
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            masks = await asyncio.gather(*tasks)
        deps._bitmask_l1.clear()

        assert len(set(masks)) == 1
        # ONE session for the whole singleflight — the task opened it itself.
        assert len(opened) == 1, (
            "C1 regression: the singleflight opened more than one session, so the "
            "misses did not actually share one join"
        )
        # ...and ONE join, which is the point of the singleflight.
        assert len(queries) == 1, (
            "C1 regression: the singleflight issued more than one permission join"
        )


# =============================================================================
# C2 — update_role invalidates the role cache
# =============================================================================


class TestUpdateRoleInvalidatesCache:
    @pytest.mark.asyncio
    async def test_update_role_calls_the_invalidator(self):
        """Behavioural: the invalidator is actually invoked."""
        from src.app.modules.rbac.schemas import RoleUpdate
        from src.app.modules.rbac.service import RoleService

        db = AsyncMock()
        role = MagicMock()
        role.is_system = False
        role.name = "old"
        role.description = "old"
        service = RoleService(db=db, tenant_id=MagicMock())

        with (
            patch.object(RoleService, "get_role", AsyncMock(return_value=role)),
            patch.object(RoleService, "get_role_by_name", AsyncMock(return_value=None)),
            patch.object(RoleService, "_invalidate_role_cache", AsyncMock()) as invalidate,
        ):
            await service.update_role(1, RoleUpdate(name="new"))

        invalidate.assert_awaited_once_with(1)


# =============================================================================
# C3 — the safety bound is loud
# =============================================================================


class TestPermissionRowLimitIsLoud:
    def test_the_bound_is_a_named_constant(self):
        from src.app.core.rbac import dependencies as deps

        assert deps._PERMISSION_ROW_LIMIT == 1000

    @pytest.mark.asyncio
    async def test_the_join_counts_distinct_permissions(self):
        """A row is one DISTINCT permission, not one (role, permission) pair.

        Without DISTINCT the bound would fire on a user holding many
        narrowly-scoped roles long before it could fire on an implausibly
        large permission set.
        """
        from sqlalchemy.dialects import postgresql

        from src.app.core.rbac import dependencies as deps

        db = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = ["users:read"]
        db.execute.return_value = result

        await deps.get_user_permissions(1, "tenant-a", db)

        stmt = db.execute.await_args.args[0]
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "SELECT DISTINCT" in sql, (
            "C3 regression: the permission join no longer de-duplicates, so the "
            "safety bound counts (role, permission) pairs instead of permissions"
        )

    @pytest.mark.asyncio
    async def test_hitting_the_bound_raises_instead_of_truncating(self):
        """The behavioural core of C3: a truncated set is never returned."""
        from src.app.core.rbac import dependencies as deps
        from src.app.shared.exceptions import ServiceUnavailableError

        graph = deps.require_permission_graph()
        limit = deps._PERMISSION_ROW_LIMIT

        db = AsyncMock()
        result = MagicMock()
        # Exactly the bound's worth of distinct slugs: the set is at the
        # limit, so it MAY be incomplete and must be refused.
        result.scalars.return_value.all.return_value = [f"p{i}" for i in range(limit)]
        db.execute.return_value = result

        with pytest.raises(ServiceUnavailableError):
            await deps.get_user_permissions(1, "tenant-a", db)

        assert graph is not None

    @pytest.mark.asyncio
    async def test_a_set_below_the_bound_is_returned(self):
        """The bound must not fire on a normal permission set."""
        from src.app.core.rbac import dependencies as deps

        db = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = ["users:read", "roles:read"]
        db.execute.return_value = result

        perms = await deps.get_user_permissions(1, "tenant-a", db)
        assert perms == {"users:read", "roles:read"}


# =============================================================================
# C4 — the role-permission queries carry the tenant scope
# =============================================================================


class TestRolePermissionQueriesAreTenantScoped:
    def test_get_role_permission_requires_a_tenant(self):
        from src.app.modules.rbac.crud import CRUDAssociations

        params = inspect.signature(CRUDAssociations.get_role_permission).parameters
        assert "tenant_id" in params, (
            "C4 regression: get_role_permission no longer takes a tenant — the "
            "tenant safety is back to being the caller's responsibility"
        )

    def test_remove_role_permission_requires_a_tenant(self):
        from src.app.modules.rbac.crud import CRUDAssociations

        params = inspect.signature(CRUDAssociations.remove_role_permission).parameters
        assert "tenant_id" in params, (
            "C4 regression: remove_role_permission no longer takes a tenant"
        )

    def test_the_scope_subquery_filters_by_tenant(self):
        """``RolePermission`` has no tenant_id, so the scope comes from Role."""
        from sqlalchemy.dialects import postgresql

        from src.app.modules.rbac.crud import CRUDAssociations

        tenant_id = uuid4()
        stmt = CRUDAssociations._scoped_role_ids(tenant_id, 7)
        sql = str(
            stmt.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        assert "roles.tenant_id" in sql
        assert str(tenant_id) in sql
        assert "roles.id" in sql

    @pytest.mark.asyncio
    async def test_the_queries_run_under_the_circuit_guard(self):
        """No bare ``db.execute`` without the DB circuit guard."""
        from contextlib import asynccontextmanager

        from src.app.modules.rbac import crud

        entered: list[bool] = []

        @asynccontextmanager
        async def _guard():
            entered.append(True)
            yield

        db = AsyncMock()
        db.execute.return_value = MagicMock()

        with patch.object(crud, "db_circuit_guard", _guard):
            await crud.crud_associations.get_role_permission(db, uuid4(), 7, 3)

        assert entered
        assert db.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_the_service_passes_its_tenant(self):
        from src.app.modules.rbac.service import RoleService

        tenant_id = uuid4()
        role = MagicMock()
        role.is_system = False
        permission = MagicMock()
        permission.id = 99
        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        service = RoleService(db=db, tenant_id=tenant_id)

        with (
            patch.object(RoleService, "get_role", AsyncMock(return_value=role)),
            patch("src.app.modules.rbac.service.crud_permissions") as permissions,
            patch("src.app.modules.rbac.service.crud_associations") as associations,
            patch.object(RoleService, "_invalidate_role_cache", AsyncMock()),
        ):
            permissions.get_by_slug = AsyncMock(return_value=permission)
            associations.get_role_permission = AsyncMock(return_value=None)
            associations.get_role_affected_user_ids = AsyncMock(return_value=[])
            await service.add_permission_to_role(7, "users:read")

        associations.get_role_permission.assert_awaited_once_with(
            db, tenant_id, 7, permission.id
        )


# =============================================================================
# C5 — UserRoleRead describes the association table
# =============================================================================


class TestUserRoleReadMatchesTheModel:
    def test_it_is_not_a_BaseRead(self):
        """``BaseRead`` requires ``id`` and ``created_at``, which the table lacks."""
        from src.app.modules._base.schemas import BaseRead
        from src.app.modules.rbac.schemas import UserRoleRead

        assert not issubclass(UserRoleRead, BaseRead), (
            "C5 regression: UserRoleRead is a BaseRead again — it demands an id and "
            "a created_at that the UserRole association table does not have"
        )

    def test_it_validates_a_real_user_role_row(self):
        """The point of the fix: a real row must validate."""
        from src.app.modules.rbac.schemas import UserRoleRead

        read = UserRoleRead(user_id=7, role_id=3, tenant_id="11111111-1111-1111-1111-111111111111")
        assert read.user_id == 7
        assert read.role_id == 3

    def test_the_model_really_lacks_id_and_created_at(self):
        """Pins WHY the schema changed — if the table gains those columns, the
        BaseRead shape would become valid again."""
        from src.app.modules.rbac.models import UserRole

        columns = UserRole.__table__.columns
        assert "id" not in columns
        assert "created_at" not in columns

    def test_it_carries_the_full_composite_key(self):
        """The triple IS the identity of an association row."""
        from src.app.modules.rbac.schemas import UserRoleRead

        fields = set(UserRoleRead.model_fields)
        assert fields == {"user_id", "role_id", "tenant_id"}
