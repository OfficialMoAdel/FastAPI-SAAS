"""Permission catalog reconciliation tests.

Run-3 finding ``rbac.seeded_permission_catalog_drifted_from_enum``: migration
``03_access_control`` seeded a fixed list of 18 slugs while the ``Permission``
enum declares 24 values, leaving 15 declared permissions ungrantable, because
``add_permission_to_role`` resolves a slug against the persisted table and
raises ``ModelNotFoundError`` when absent.

These tests pin the invariant that makes the dashboard gate reachable: every
enum slug must be insertable into the catalog, and reconciliation must be
idempotent and non-destructive.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.core.rbac.permissions import Permission
from src.app.modules.rbac import catalog as catalog_mod


def _permission_model(slug_attr="slug"):
    model = MagicMock()
    model.slug = slug_attr
    return model


class _FakeResult:
    """Mimics the ``result.scalars().all()`` shape the reconciler consumes."""

    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


def _present_result(slugs):
    """A scalars().all() result shaped like a real ``SELECT permissions.slug``."""
    return _FakeResult(list(slugs))


class TestEnumIsTheSourceOfTruth:
    """Pure, DB-free assertions about what reconciliation should do."""

    def test_every_enum_slug_is_covered(self) -> None:
        slugs = catalog_mod.missing_permission_slugs()
        assert set(slugs) == {p.value for p in Permission}

    def test_reports_every_seed_miss_identified_by_the_audit(self) -> None:
        """The 15 slugs the audit found unseeded must all be enum values.

        If this fails, the enum and the audit's list have diverged and the
        migration may no longer be reconciling what it was written to fix.
        """
        previously_unseeded = {
            "feature_flags:read",
            "feature_flags:manage",
            "notifications:read",
            "notifications:manage",
            "webhooks:create",
            "tenant:update",
            "users:self",
            "users:delete",
            "users:role_assign",
            "products:manage",
            "storage_settings:manage",
            "files:write",
            "files:delete",
            "audit_logs:read",
            "audit_logs:export",
        }
        assert previously_unseeded <= set(catalog_mod.missing_permission_slugs())

    def test_dashboard_read_is_present_for_the_new_gate(self) -> None:
        """The dashboard gate added in the same change must be grantable."""
        assert Permission.DASHBOARD_READ.value in catalog_mod.missing_permission_slugs()

    def test_successor_map_only_names_real_enum_slugs(self) -> None:
        """files:manage is stale; its successors must exist in the enum."""
        enum_values = {p.value for p in Permission}
        for stale, successors in catalog_mod.SUCCESSOR_SLUGS.items():
            assert stale not in enum_values, f"{stale} is no longer stale"
            for successor in successors:
                assert successor in enum_values


class TestReconcilePermissionCatalog:
    @pytest.mark.asyncio
    async def test_dry_run_creates_nothing(self) -> None:
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_present_result([]))
        db.add = MagicMock()
        db.commit = AsyncMock()

        report = await catalog_mod.reconcile_permission_catalog(db, dry_run=True)

        assert report.created == ()
        assert len(report.missing) == len(list(Permission))
        db.add.assert_not_called()
        db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_inserts_only_missing_slugs(self) -> None:
        present = {p.value for p in Permission if p is not Permission.DASHBOARD_READ}
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_present_result(present))
        db.add = MagicMock()
        db.commit = AsyncMock()

        report = await catalog_mod.reconcile_permission_catalog(db)

        assert report.created == (Permission.DASHBOARD_READ.value,)
        assert report.missing == (Permission.DASHBOARD_READ.value,)
        # Exactly one insert; the rest were already present.
        assert db.add.call_count == 1
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_is_idempotent_when_already_complete(self) -> None:
        present = [p.value for p in Permission]
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_present_result(present))
        db.add = MagicMock()
        db.commit = AsyncMock()

        report = await catalog_mod.reconcile_permission_catalog(db)

        assert report.missing == ()
        assert report.created == ()
        assert not report.changed
        db.add.assert_not_called()
        db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_slugs_are_retained_not_deleted(self) -> None:
        """Backward compatibility: never strip a grant from a live role.

        role_permissions references permissions by id, so deleting a stale row
        would silently revoke existing capability.
        """
        present = [p.value for p in Permission] + [
            "posts:read",
            "posts:manage",
            "files:manage",
            "settings:manage",
            "api_keys:manage",
        ]
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_present_result(present))
        db.add = MagicMock()
        db.commit = AsyncMock()

        report = await catalog_mod.reconcile_permission_catalog(db)

        assert set(report.stale) >= {"posts:read", "files:manage", "settings:manage"}
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_conflict_is_downgraded_not_raised(self) -> None:
        """A concurrent writer must not crash startup."""
        from sqlalchemy.exc import IntegrityError

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_present_result([]))
        db.add = MagicMock()
        db.commit = AsyncMock(
            side_effect=IntegrityError("INSERT", {}, Exception("unique slug"))
        )
        db.rollback = AsyncMock()

        report = await catalog_mod.reconcile_permission_catalog(db)

        assert report.created != ()
        db.rollback.assert_awaited_once()


class TestGrantPathAfterReconcile:
    """The user-visible consequence: a valid enum slug is grantable.

    ``add_permission_to_role`` raises ``ModelNotFoundError`` when the slug is
    absent from the catalog. Before this change, granting e.g.
    ``audit_logs:read`` was impossible on a migrated database.
    """

    @pytest.mark.asyncio
    async def test_new_enum_slug_is_grantable_after_reconcile(self) -> None:
        service = MagicMock()
        service.get_role = AsyncMock(
            return_value=MagicMock(is_system=False, id=7)
        )
        db = AsyncMock()

        permission_row = MagicMock(id=99)
        crud_permissions = MagicMock()
        crud_permissions.get_by_slug = AsyncMock(return_value=permission_row)
        crud_associations = _associations_double()

        role_permission_model = MagicMock(return_value=MagicMock())
        service.db = db

        with (
            patch_attr(catalog_mod, "require_permission_graph"),
            patch_rbac_service(
                crud_permissions=crud_permissions,
                crud_associations=crud_associations,
                role_permission_model=role_permission_model,
            ),
        ):
            await _call_add_permission(service, Permission.DASHBOARD_READ.value)

        # The grant resolves to a real permission row instead of raising.
        crud_permissions.get_by_slug.assert_awaited_once_with(db, Permission.DASHBOARD_READ.value)
        db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_grant_is_idempotent(self) -> None:
        service = MagicMock()
        service.get_role = AsyncMock(return_value=MagicMock(is_system=False, id=7))
        db = AsyncMock()

        crud_permissions = MagicMock()
        crud_permissions.get_by_slug = AsyncMock(return_value=MagicMock(id=99))
        crud_associations = _associations_double(existing=MagicMock())  # already assigned
        service.db = db

        with (
            patch_attr(catalog_mod, "require_permission_graph"),
            patch_rbac_service(
                crud_permissions=crud_permissions,
                crud_associations=crud_associations,
                role_permission_model=MagicMock(),
            ),
        ):
            await _call_add_permission(service, Permission.DASHBOARD_READ.value)

        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_system_role_is_still_protected(self) -> None:
        """The existing guard must survive the catalog change."""
        from src.app.shared.exceptions import ForbiddenError

        service = MagicMock()
        service.get_role = AsyncMock(return_value=MagicMock(is_system=True, id=1))
        service.db = AsyncMock()

        with (
            patch_attr(catalog_mod, "require_permission_graph"),
            patch_rbac_service(
                crud_permissions=MagicMock(),
                crud_associations=_associations_double(),
                role_permission_model=MagicMock(),
            ),
            pytest.raises(ForbiddenError),
        ):
            await _call_add_permission(service, Permission.DASHBOARD_READ.value)


# --- small helpers so the grant tests can run without the whole app graph ---

def patch_attr(module, name):
    from unittest.mock import patch

    return patch.object(module, name, MagicMock())


def _associations_double(existing=None):
    """crud_associations double: idempotency lookup + affected-user lookup."""
    crud = MagicMock()
    crud.get_role_permission = AsyncMock(return_value=existing)
    crud.get_role_affected_user_ids = AsyncMock(return_value=[])
    return crud


def patch_rbac_service(*, crud_permissions, crud_associations, role_permission_model):
    from contextlib import ExitStack
    from unittest.mock import patch

    import src.app.modules.rbac.service as rbac_service

    stack = ExitStack()
    stack.enter_context(patch.object(rbac_service, "crud_permissions", crud_permissions))
    stack.enter_context(patch.object(rbac_service, "crud_associations", crud_associations))
    stack.enter_context(
        patch.object(rbac_service, "RolePermission", role_permission_model)
    )
    return stack


async def _call_add_permission(service, slug):
    """Invoke the real grant method against our doubles."""
    from src.app.modules.rbac.service import RoleService

    method = RoleService.add_permission_to_role
    holder = _GrantHarness(service)
    await method(holder, 7, slug)


class _GrantHarness:
    """Minimal stand-in exposing exactly what add_permission_to_role touches."""

    def __init__(self, inner: MagicMock):
        self._inner = inner
        self.db = inner.db
        self.tenant_id = UUID_()

    async def get_role(self, role_id):
        return await self._inner.get_role(role_id)

    async def _invalidate_role_cache(self, role_id):
        """Neutralise the post-commit L1 invalidation (needs the real cache seam)."""
        return None


def UUID_():
    from uuid import UUID

    return UUID("00000000-0000-0000-0000-000000000001")
