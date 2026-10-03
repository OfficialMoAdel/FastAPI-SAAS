"""Grant ``tasks:manage`` to a tenant role (operator script).

Why this exists instead of a migration
--------------------------------------
The ``/tasks`` routes are gated by ``requires_permission(Permission.TASKS_MANAGE)``
(finding S-38 — they previously had no permission gate at all, so any
authenticated principal on any tier could enqueue background jobs).

``requires_permission`` grants superusers an unconditional bypass, so the operator
who owns a fresh installation can already use ``/tasks`` with no setup.

For tenant-scoped delegation to a non-superuser, the grant has to be made per
tenant, and that is operator work rather than migration work:

* ``Role.tenant_id`` is ``NOT NULL`` with a FK to ``tenants``, and roles are unique
  per ``(tenant_id, name)``;
* a migration runs before any tenant exists, so it has nothing to attach a role to;
* migration ``03_access_control`` makes this explicit — roles are created EMPTY and
  "operators grant them per role afterwards";
* a global/tenant-blind "admin" role would breach the isolation every other gate in
  this codebase is built to enforce.

So this script follows the same convention as ``create_first_superuser.py`` and
``create_first_tier.py``: an idempotent, operator-invoked action.

Usage
-----
    uv run python -m src.scripts.grant_tasks_manage --tenant <uuid> --role <name>

``--role`` may name an EXISTING role (granted, then reported), or pass
``--create-role`` to create the role in that tenant if it is missing. Everything is
idempotent: re-running grants nothing twice and raises nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

# S-38: relationships are declared by STRING name, so every model on both ends of
# one must be in SQLAlchemy's registry before its mapper is configured. Touching
# Role reaches User, which reaches Tenant, whose own relationships reach further
# still -- importing them one by one worked for exactly one hop and broke on the
# next (`KeyError: 'Notification'`). A piecemeal import list is also a landmine:
# it silently rots as models are added.
#
# Importing the v1 API aggregator is the stable seam. It pulls in every feature
# router, and every feature router imports its own models, which registers the
# whole graph in one shot -- the same path the running application takes.
# Nothing here is used at runtime; it is purely a registry side effect.
from src.app.api.v1 import router as _api_router  # noqa: F401
from src.app.core.db.database import AsyncSession, local_session
from src.app.core.logger import get_logger
from src.app.modules.rbac.models import Permission, Role, RolePermission

logging.basicConfig(level=logging.INFO)
logger = get_logger(__name__)

PERMISSION_SLUG = "tasks:manage"


class GrantError(RuntimeError):
    """The grant could not be satisfied. Never silent — a silent no-op here
    leaves an operator believing a permission was delegated when it was not."""


@dataclass(frozen=True, slots=True)
class GrantOutcome:
    granted: bool
    permission_id: int
    role_id: int
    role_created: bool
    reason: str


async def grant_tasks_manage(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    role_name: str,
    create_role: bool = False,
) -> GrantOutcome:
    """Grant ``tasks:manage`` to ``role_name`` inside ``tenant_id``.

    Idempotent: an existing grant is reported, not duplicated.
    """
    permission = (
        await session.execute(select(Permission).where(Permission.slug == PERMISSION_SLUG))
    ).scalar_one_or_none()
    if permission is None:
        raise GrantError(
            f"Permission {PERMISSION_SLUG!r} is not in the permissions table. "
            "Run `uv run alembic -c src/alembic.ini upgrade head` (layer 03 derives "
            "the catalogue from the Permission enum), or boot the app once so "
            "reconcile_permission_catalog adds it."
        )

    role = (
        await session.execute(select(Role).where(Role.tenant_id == tenant_id, Role.name == role_name))
    ).scalar_one_or_none()

    role_created = False
    if role is None:
        if not create_role:
            raise GrantError(
                f"Role {role_name!r} does not exist in tenant {tenant_id}. "
                "Create it first, or pass --create-role."
            )
        role = Role(tenant_id=tenant_id, name=role_name, description=f"Grants {PERMISSION_SLUG}")
        session.add(role)
        await session.flush()
        role_created = True

    existing = (
        await session.execute(
            select(RolePermission).where(
                RolePermission.role_id == role.id,
                RolePermission.permission_id == permission.id,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        logger.info(
            "grant_already_present",
            permission=PERMISSION_SLUG,
            role=role_name,
            tenant_id=str(tenant_id),
        )
        return GrantOutcome(
            granted=False,
            permission_id=permission.id,
            role_id=role.id,
            role_created=role_created,
            reason="already granted",
        )

    session.add(RolePermission(role_id=role.id, permission_id=permission.id))
    await session.commit()
    logger.info(
        "permission_granted",
        permission=PERMISSION_SLUG,
        role=role_name,
        tenant_id=str(tenant_id),
        role_created=role_created,
    )
    return GrantOutcome(
        granted=True,
        permission_id=permission.id,
        role_id=role.id,
        role_created=role_created,
        reason="granted",
    )


async def _main(tenant: str, role: str, create_role: bool) -> int:
    try:
        tenant_id = UUID(tenant)
    except ValueError as e:
        logger.error("invalid_tenant_uuid", tenant=tenant, error=str(e))
        return 2

    async with local_session() as session:
        try:
            outcome = await grant_tasks_manage(
                session, tenant_id=tenant_id, role_name=role, create_role=create_role
            )
        except GrantError as e:
            logger.error("grant_failed", error=str(e))
            return 1

    logger.info(
        "grant_outcome",
        granted=outcome.granted,
        reason=outcome.reason,
        permission_id=outcome.permission_id,
        role_id=outcome.role_id,
        role_created=outcome.role_created,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Grant {PERMISSION_SLUG} to a tenant role.")
    parser.add_argument("--tenant", required=True, help="Tenant UUID the role lives in")
    parser.add_argument("--role", required=True, help="Role name (unique per tenant)")
    parser.add_argument(
        "--create-role",
        action="store_true",
        help="Create the role in the tenant if it does not exist",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_main(args.tenant, args.role, args.create_role))


if __name__ == "__main__":
    raise SystemExit(main())
