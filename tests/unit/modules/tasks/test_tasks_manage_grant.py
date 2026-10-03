"""`tasks:manage` must not fail the operator closed — and the grant must be tenant-scoped.

Facts this module pins, because they decide the whole design:

1. **`requires_permission` grants a superuser an unconditional bypass**
   (`core/rbac/dependencies.py`, the `if is_superuser: return` branch). So the
   `/tasks` routes ARE reachable by an operator out of the box, and no seeding is
   required for them to work.

2. **A migration cannot seed an admin role.** `Role.tenant_id` is NOT NULL with a
   FK to `tenants`, and roles are unique per `(tenant_id, name)`. A migration runs
   before any tenant exists, so there is no tenant to attach a role to. Migration
   03 documents this choice explicitly: roles are created EMPTY and "operators grant
   them per role afterwards". Seeding a global admin role would breach the tenant
   isolation the whole codebase is built on.

3. **Superuser does NOT bypass tenant isolation.**
   `enforce_superuser_tenant_rule` runs before the permission check and its
   docstring says "A superuser does not get a pass here — that is the entire
   point." So a superuser acting for another tenant is still refused.

The practical resolution: `/tasks` works for superusers immediately, and
tenant-scoped delegation is granted per tenant by
`src/scripts/grant_tasks_manage.py`, following the same convention as
`create_first_superuser.py` / `create_first_tier.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

PERMISSIONS = Path("src/app/core/rbac/permissions.py")
DEPS = Path("src/app/core/rbac/dependencies.py")
TASKS_ROUTER = Path("src/app/modules/tasks/router.py")
GRANT_SCRIPT = Path("src/scripts/grant_tasks_manage.py")


class TestSuperuserBypassIsReal:
    """The decisive fact: the operator is never locked out of /tasks."""

    def test_requires_permission_returns_early_for_superusers(self) -> None:
        src = DEPS.read_text(encoding="utf-8")
        assert "if is_superuser:" in src, "requires_permission lost its superuser branch"
        assert "Superuser bypass used" in src

    def test_tenant_isolation_is_enforced_BEFORE_the_bypass(self) -> None:
        """A superuser must not get a pass on cross-tenant access."""
        src = DEPS.read_text(encoding="utf-8")
        assert src.index("enforce_superuser_tenant_rule(") < src.index("if is_superuser:"), (
            "tenant isolation must be enforced before the superuser bypass, "
            "otherwise a superuser could act for any tenant"
        )


class TestTasksRoutesUseThePermissionGate:
    def test_both_routes_are_gated(self) -> None:
        tree = ast.parse(TASKS_ROUTER.read_text(encoding="utf-8"))
        gated = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name in ("create_task", "get_task"):
                names = [ast.unparse(d) for d in node.decorator_list]
                assert any("cache" not in n and "router." in n for n in names), names
                src = TASKS_ROUTER.read_text(encoding="utf-8")
                assert "dependencies=_TASK_PERMISSION" in src
                gated += 1
        assert gated == 2, f"expected both /tasks routes gated, found {gated}"

    def test_permission_is_declared(self) -> None:
        src = PERMISSIONS.read_text(encoding="utf-8")
        assert 'TASKS_MANAGE = "tasks:manage"' in src


class TestNoMigrationSeedsAnAdminRole:
    """Guard the decision, so a future change cannot silently breach isolation."""

    def test_no_migration_inserts_a_role(self) -> None:
        for path in Path("src/migrations/versions").glob("*.py"):
            src = path.read_text(encoding="utf-8")
            assert "INSERT INTO roles" not in src, (
                f"{path.name} seeds a role. Role.tenant_id is NOT NULL and no tenant "
                "exists at migration time; this would either fail or create a "
                "tenant-blind role."
            )

    def test_layer_03_states_the_policy(self) -> None:
        src = Path("src/migrations/versions/03_access_control.py").read_text(encoding="utf-8")
        assert "created EMPTY" in src, "the no-seeding rationale must stay documented"


class TestGrantScriptExistsAndIsTenantScoped:
    def test_script_present(self) -> None:
        assert GRANT_SCRIPT.exists(), "operator grant script missing"

    def test_script_is_tenant_scoped(self) -> None:
        src = GRANT_SCRIPT.read_text(encoding="utf-8")
        assert "tenant_id" in src, "the grant must be tenant-scoped"
        # The link is written through the ORM (RolePermission), not raw SQL.
        assert "RolePermission" in src
        # ...and the role lookup must be constrained to the tenant.
        assert "Role.tenant_id == tenant_id" in src, (
            "role lookup must be tenant-scoped; a bare name lookup would grant "
            "across tenants"
        )

    def test_script_grants_the_right_permission(self) -> None:
        src = GRANT_SCRIPT.read_text(encoding="utf-8")
        assert "tasks:manage" in src or "TASKS_MANAGE" in src

    @pytest.mark.asyncio
    async def test_grant_is_idempotent(self) -> None:
        """Re-running must not duplicate the grant or fail."""
        from src.scripts.grant_tasks_manage import grant_tasks_manage

        session = MagicMock()
        # permission exists
        perm = MagicMock()
        perm.id = 26
        perm_by_slug = MagicMock()
        perm_by_slug.scalar_one_or_none.return_value = perm
        # role exists
        role = MagicMock()
        role.id = 5
        role_by_name = MagicMock()
        role_by_name.scalar_one_or_none.return_value = role
        # already granted
        existing = MagicMock()
        existing.scalar_one_or_none.return_value = MagicMock()

        session.execute = AsyncMock(side_effect=[perm_by_slug, role_by_name, existing])
        session.commit = AsyncMock()

        result = await grant_tasks_manage(session, tenant_id=MagicMock(), role_name="ops-admin")

        assert result.granted is False, "an existing grant must be a no-op"
        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_grant_creates_the_link_when_missing(self) -> None:
        from src.scripts.grant_tasks_manage import grant_tasks_manage

        session = MagicMock()
        perm = MagicMock()
        perm.id = 26
        perm_by_slug = MagicMock()
        perm_by_slug.scalar_one_or_none.return_value = perm
        role = MagicMock()
        role.id = 5
        role_by_name = MagicMock()
        role_by_name.scalar_one_or_none.return_value = role
        missing = MagicMock()
        missing.scalar_one_or_none.return_value = None

        session.execute = AsyncMock(side_effect=[perm_by_slug, role_by_name, missing])
        session.commit = AsyncMock()

        result = await grant_tasks_manage(session, tenant_id=MagicMock(), role_name="ops-admin")

        assert result.granted is True
        session.add.assert_called_once()
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_grant_reports_a_missing_permission_loudly(self) -> None:
        """A silent no-op here is how an operator would think it worked."""
        from src.scripts.grant_tasks_manage import GrantError, grant_tasks_manage

        session = MagicMock()
        none_found = MagicMock()
        none_found.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=none_found)

        with pytest.raises(GrantError, match="tasks:manage"):
            await grant_tasks_manage(session, tenant_id=MagicMock(), role_name="ops-admin")
