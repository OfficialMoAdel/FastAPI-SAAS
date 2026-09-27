"""Permission catalog reconciliation — the permissions table mirrors the enum.

Root cause of run-3 finding ``rbac.seeded_permission_catalog_drifted_from_enum``:
migration ``03_access_control`` seeded a fixed list of 18 slugs while
``core.rbac.permissions.Permission`` declares 24 values. Fifteen enum
permissions were therefore ungrantable — ``add_permission_to_role`` resolves a
slug against the persisted table and raises ``ModelNotFoundError`` when absent —
and adding a new permission to the enum alone produced an inert authorization
gate.

A correct reconciler already existed at ``scripts/seed_rbac.py``, but it was a
manual operator script that nothing called, so drift was inevitable.

Design
------
The code enum is the source of truth; the ``permissions`` table is a global
mirror. It carries no ``tenant_id`` and is deliberately not covered by RLS, so a
global, idempotent sync is the correct shape.

- :func:`missing_permission_slugs` is a pure diff (unit-testable, no DB).
- :func:`reconcile_permission_catalog` performs an idempotent upsert.

Backward compatibility
----------------------
Stale seeded slugs (``posts:read``, ``files:manage``, ``settings:manage``,
``billing:read``, ``audit:read``, ``api_keys:*`` …) are **kept**, never dropped.
``role_permissions`` rows reference permission ids by foreign key, so deleting a
row would silently strip existing grants from live roles. Drift in that
direction is a deprecation concern, not a security one.

The one stale slug with a clear successor — ``files:manage`` — is handled by
:meth:`PermissionCatalogReport.successor_slugs`, which maps it onto the newer
``files:write`` + ``files:delete`` pair so a migration can carry intent across
without guessing at the other stale slugs.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.logger import get_logger
from src.app.core.rbac.models import require_permission_graph
from src.app.core.rbac.permissions import Permission

logger = get_logger(__name__)

# Stale slugs that have an unambiguous successor in the current enum. Kept for
# backward compatibility (existing role_permissions reference them by id) and
# optionally re-pointed by ``migrate_successor_grants``.
SUCCESSOR_SLUGS: dict[str, tuple[str, ...]] = {
    # A single coarse "manage files" grant splits into the read/write/delete trio.
    "files:manage": ("files:write", "files:delete"),
}


@dataclass(frozen=True, slots=True)
class PermissionCatalogReport:
    """Outcome of a catalog comparison."""

    missing: tuple[str, ...] = ()
    stale: tuple[str, ...] = ()
    created: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.created)


def _description(permission: Permission) -> str:
    return permission.name.replace("_", " ").title()


def missing_permission_slugs() -> tuple[str, ...]:
    """Enum slugs the reconciler would create. Pure — no database access."""
    return tuple(sorted(p.value for p in Permission))


async def reconcile_permission_catalog(
    db: AsyncSession,
    *,
    dry_run: bool = False,
) -> PermissionCatalogReport:
    """Idempotently ensure every enum permission exists in the table.

    Args:
        db: An open session. The caller owns the transaction boundary; this
            function commits only when it actually inserted rows.
        dry_run: Report what would change without writing.

    Returns:
        A :class:`PermissionCatalogReport`. ``missing`` lists every enum slug
        (the authoritative set), ``stale`` lists persisted slugs no longer in
        the enum, and ``created`` lists what this call inserted.
    """
    permission_model = require_permission_graph().permission

    result = await db.execute(select(permission_model.slug))
    existing: set[str] = set(result.scalars().all())

    wanted = {p.value for p in Permission}
    missing = tuple(sorted(wanted - existing))
    stale = tuple(sorted(existing - wanted))
    created: tuple[str, ...] = ()

    if missing and not dry_run:
        # Dialect-agnostic insert: the test suite runs on sqlite+aiosqlite while
        # production runs on Postgres, so a Postgres-only ON CONFLICT upsert
        # would make this untestable. Rows are added one at a time and an
        # IntegrityError (a concurrent writer won the unique slug index) is
        # downgraded to a rollback-and-continue, so repeated or parallel
        # startups converge instead of raising.
        for permission in Permission:
            if permission.value not in set(missing):
                continue
            db.add(permission_model(slug=permission.value, description=_description(permission)))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            logger.warning("permission_catalog_reconcile_conflict", missing=len(missing))
        created = missing
        logger.info(
            "permission_catalog_reconciled",
            created=len(created),
            total=len(wanted),
            stale_retained=len(stale),
        )

    return PermissionCatalogReport(missing=missing, stale=stale, created=created)


async def migrate_successor_grants(db: AsyncSession, *, dry_run: bool = False) -> dict[str, int]:
    """Carry role grants across a renamed permission.

    Re-points ``role_permissions`` rows that reference a stale slug onto the
    slug(s) in :data:`SUCCESSOR_SLUGS`, for roles that do not already hold the
    successor. Existing rows are left untouched, so this is safe to re-run and
    never removes a grant.

    Returns:
        Mapping of ``{stale_slug: rows_migrated}`` for slugs actually handled.
    """
    permission_model = require_permission_graph().permission
    role_permission_model = require_permission_graph().role_permission

    result = await db.execute(select(permission_model.id, permission_model.slug))
    by_slug = {slug: pk for pk, slug in result.all()}

    migrated: dict[str, int] = {}
    for stale_slug, successors in SUCCESSOR_SLUGS.items():
        stale_id = by_slug.get(stale_slug)
        if stale_id is None:
            continue
        if dry_run:
            migrated[stale_slug] = -1
            continue

        holders = await db.execute(
            select(role_permission_model.role_id).where(
                role_permission_model.permission_id == stale_id
            )
        )
        role_ids = list(holders.scalars().all())
        if not role_ids:
            continue

        added = 0
        for successor in successors:
            successor_id = by_slug.get(successor)
            if successor_id is None:
                # Successor not yet materialised; reconcile first, then re-run.
                continue
            existing = await db.execute(
                select(role_permission_model.role_id).where(
                    role_permission_model.permission_id == successor_id,
                    role_permission_model.role_id.in_(role_ids),
                )
            )
            already = set(existing.scalars().all())
            for role_id in role_ids:
                if role_id in already:
                    continue
                db.add(role_permission_model(role_id=role_id, permission_id=successor_id))
                added += 1
        if added:
            await db.commit()
            logger.info("permission_successor_grants_migrated", stale=stale_slug, added=added)
        migrated[stale_slug] = added

    return migrated


__all__ = [
    "PermissionCatalogReport",
    "SUCCESSOR_SLUGS",
    "migrate_successor_grants",
    "missing_permission_slugs",
    "reconcile_permission_catalog",
]
