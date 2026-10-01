"""After reconcile, the catalog matches the enum — proven against a real DB.

`tests/unit/modules/rbac/test_permission_catalog.py` exercises the reconciler
with doubles, which is right for its edge cases but cannot prove the invariant
the original bug violated: that the permissions TABLE actually ends up holding
every slug the enum declares. Migration 03 used to seed a hand-typed list of 18
slugs against 24 enum values, so 15 declared permissions were ungrantable —
`add_permission_to_role` resolves against the table and raises
`ModelNotFoundError` on a miss. Doubles would not have caught that; only a real
round trip will.

These run against the real (sqlite) session, so they are the behavioural
counter-test to the drift, not a restatement of the unit test.

Every test here creates its own drift explicitly rather than assuming the
table's contents. The test database is shared across the session and is built
with ``create_all`` (no seed), so a test that inferred state from whatever
preceded it would be order-dependent — one was written that way and deleted
for that reason.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.rbac.models import require_permission_graph
from src.app.core.rbac.permissions import Permission
from src.app.modules.rbac.catalog import reconcile_permission_catalog

pytestmark = pytest.mark.asyncio


async def _slugs_in_table(db: AsyncSession) -> set[str]:
    model = require_permission_graph().permission
    result = await db.execute(select(model.slug))
    return set(result.scalars().all())


async def _drop_slug(db: AsyncSession, slug: str) -> None:
    """Simulate the drift this bead is about: a slug the table does not have."""
    model = require_permission_graph().permission
    row = await db.scalar(select(model).where(model.slug == slug))
    if row is not None:
        await db.delete(row)
        await db.commit()


async def test_reconcile_leaves_no_enum_slug_missing(db_session: AsyncSession) -> None:
    """The post-condition: table slugs ⊇ enum slugs.

    This is the invariant whose absence made 15 permissions silently inert.

    NOTE on scope: the test fixture builds the schema with
    ``Base.metadata.create_all`` and seeds NOTHING, so "does a freshly MIGRATED
    database match the enum" cannot be answered here — that needs alembic, and
    belongs in a CI job. What these tests pin is the repair path, and each one
    sets up its own starting state rather than inheriting whatever an earlier
    test left in the shared database.
    """
    await reconcile_permission_catalog(db_session)

    missing = {p.value for p in Permission} - await _slugs_in_table(db_session)
    assert missing == set(), f"enum permissions absent from the table: {sorted(missing)}"


async def test_reconcile_repairs_drift_and_then_converges(db_session: AsyncSession) -> None:
    """A dropped slug is restored, and a second pass has nothing left to do.

    Convergence is what makes the boot-time phase safe to run on every start
    rather than being a one-shot repair. The drift is created explicitly, so
    the test does not depend on what the fixture database happens to contain.
    """
    await reconcile_permission_catalog(db_session)
    await _drop_slug(db_session, "dashboard:read")
    assert "dashboard:read" not in await _slugs_in_table(db_session)

    repaired = await reconcile_permission_catalog(db_session)
    assert "dashboard:read" in repaired.missing
    assert "dashboard:read" in repaired.created
    assert "dashboard:read" in await _slugs_in_table(db_session)

    second = await reconcile_permission_catalog(db_session)
    assert second.missing == (), f"second reconcile still reports missing: {second.missing}"
    assert second.created == ()
    assert not second.changed


async def test_dry_run_reports_without_writing(db_session: AsyncSession) -> None:
    """`dry_run` is the hook a build-time drift check needs.

    It must report the same thing the real call would do, without touching
    the table — that is what lets CI ask "would this catalog drift?" against a
    database it must not modify.
    """
    await reconcile_permission_catalog(db_session)
    await _drop_slug(db_session, "dashboard:read")

    report = await reconcile_permission_catalog(db_session, dry_run=True)

    assert report.missing == ("dashboard:read",), (
        f"dry_run must report the real gap, got {report.missing}"
    )
    assert report.created == (), "dry_run must not claim to have created anything"
    assert not report.changed
    assert "dashboard:read" not in await _slugs_in_table(db_session), (
        "dry_run wrote to the table; a build-time check must not mutate the database"
    )


async def test_reconcile_retains_operator_created_slugs(db_session: AsyncSession) -> None:
    """An unexpected slug is kept, never dropped.

    COUNTER-TEST. A reconciler that "tidied" the table by deleting anything not
    in the enum would be strictly worse than the drift it fixes: it would
    silently revoke permissions an operator created. Retention is the correct
    behaviour, and it is what the boot phase reports as `stale_retained`.
    """
    model = require_permission_graph().permission
    db_session.add(model(slug="operator:custom", description="hand-created"))
    await db_session.commit()

    report = await reconcile_permission_catalog(db_session)

    assert "operator:custom" in report.stale, "an unknown slug must be reported as stale"
    assert "operator:custom" in await _slugs_in_table(db_session), (
        "reconcile deleted an operator-created slug; that is a permission revocation"
    )
