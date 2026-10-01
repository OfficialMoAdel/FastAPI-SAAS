"""The RLS policies must stay coherent with the models — checked without Postgres.

`tests/integration/core/db/test_rls_isolation.py` is a real DB-level RLS
suite: it goes straight at `db_session` with no application layer, sets the
tenant GUC explicitly, and asserts cross-tenant reads return nothing. That is
exactly the right shape of test.

It also never runs, and it cannot run today, for reasons that have nothing to
do with the tests themselves:

  * `tests/conftest.py` hardwires the suite to SQLite — there is no dialect
    switch — so the PostgreSQL service already declared in `.github/workflows/
    tests.yml` is unused by the test job;
  * RLS is a PostgreSQL feature with no SQLite equivalent;
  * this repository forbids running Docker, so the harness cannot be stood up
    here either.

Rather than leave the whole area unverified, this module covers the part that
IS verifiable without a database: that the policy list and the ORM models
agree. A `tenant_isolation` policy is written as
`USING (tenant_id = auth.current_tenant_id())`, so a table in the policy list
with no `tenant_id` column would fail AT MIGRATION TIME, and a table that
gains a `tenant_id` later but is never added to the list would ship with the
column unprotected and every RLS test would still pass. Both are real defects
that no amount of running the skipped suite would catch on its own.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "migrations"
    / "versions"
    / "08_enable_rls_policies.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_08", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration():
    return _load_migration()


@pytest.fixture(scope="module")
def metadata():
    """The full ORM table registry, with every model imported."""
    import src.app.models  # noqa: F401  (registers every mapper)
    from src.app.core.db.database import Base

    return Base.metadata


class TestPolicyListIsCoherent:
    def test_every_policy_table_exists_in_the_orm(self, migration, metadata) -> None:
        """No policy targets a table the ORM does not know about.

        A stale name here means the migration fails at deploy time — the
        policy list is hand-maintained, exactly like the slug list that
        drifted in `03_access_control`.
        """
        unknown = [t for t in migration.TENANT_TABLES if t not in metadata.tables]
        assert unknown == [], (
            f"RLS policies target tables absent from the ORM: {unknown}. "
            f"Either the table was renamed or the policy list is stale."
        )

    def test_every_policy_table_has_a_tenant_id_column(self, migration, metadata) -> None:
        """Every listed table must actually have the column the policy reads.

        The policy is `USING (tenant_id = auth.current_tenant_id())`. A table
        without that column makes the policy invalid SQL, so the failure would
        surface as a migration error rather than as a test failure.
        """
        without = [t for t in migration.TENANT_TABLES if "tenant_id" not in metadata.tables[t].columns]
        assert without == [], f"tables with an RLS policy but no tenant_id column: {without}"

    def test_policy_list_is_not_empty(self, migration) -> None:
        """A guard against the list being emptied into a no-op.

        `CREATE POLICY` inside a loop over an empty list succeeds silently, so
        a bad merge that truncates the list would deploy with RLS off and
        every test still green.
        """
        assert len(migration.TENANT_TABLES) > 0, "TENANT_TABLES is empty; no table would be protected"

    def test_core_tenant_tables_are_protected(self, migration) -> None:
        """The tables holding tenant data are actually on the list.

        Named explicitly rather than derived, so DELETING one of them from
        `TENANT_TABLES` fails here instead of silently dropping protection.
        """
        protected = set(migration.TENANT_TABLES)
        for table in ("post", "files", "api_keys", "audit_logs", "roles"):
            assert table in protected, f"{table} is not covered by an RLS policy"


class TestTheSkippedSuiteIsHonestlyMarked:
    """The skip marker must tell the truth about why the suite does not run.

    The marker used to read `skipif(True, reason="... Set RLS_TESTS_ENABLED=1
    to run")` while nothing in the repository reads `RLS_TESTS_ENABLED`. A
    reason that names a flag which does nothing is worse than no reason: the
    next person sets the variable, sees no change, and concludes RLS is fine.
    """

    def _skip_markers(self):
        import ast

        path = Path(__file__).resolve().parents[3] / "integration" / "core" / "db" / "test_rls_isolation.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found: list[ast.expr] = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "skipif"
            ):
                for kw in node.keywords:
                    if kw.arg == "reason" and isinstance(kw.value, ast.Constant):
                        found.append(kw.value)
        return [n.value for n in found]

    def test_skip_reason_names_the_real_blocker(self) -> None:
        reasons = self._skip_markers()
        assert reasons, "the RLS suite lost its skip marker; is it running yet?"

        joined = " ".join(reasons)
        assert "SQLite" in joined, (
            f"the skip reason must name SQLite, the actual reason the suite "
            f"cannot run. Got: {joined!r}"
        )

    def test_skip_reason_names_no_flag_that_does_nothing(self) -> None:
        """A skip must not point at an environment variable nobody reads.

        `RLS_TESTS_ENABLED` was named in the reason but read nowhere in the
        repo, so setting it changed nothing and the reason was actively
        misleading. This pins that a named flag actually exists.
        """
        import subprocess
        import sys

        reasons = " ".join(self._skip_markers())
        for token in reasons.replace(".", " ").replace(",", " ").split():
            if not token.isupper() or "=" in token or len(token) < 4:
                continue
            # Only check plausible env-var-looking tokens.
            if not token.endswith("_ENABLED") and not token.endswith("_TEST"):
                continue
            root = Path(__file__).resolve().parents[4]
            hit = subprocess.run(
                ["git", "grep", "-l", token, "--", "src", "tests", "scripts"],
                cwd=root,
                capture_output=True,
                text=True,
            )
            readers = [
                line
                for line in hit.stdout.splitlines()
                if "test_rls_isolation" not in line
            ]
            assert readers, (
                f"the skip reason names {token}, which is read nowhere outside "
                f"the skip marker itself. Either implement the flag or drop it "
                f"from the reason."
            )
        del sys
