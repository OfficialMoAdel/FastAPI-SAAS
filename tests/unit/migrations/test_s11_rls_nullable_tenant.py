"""S-11 — a global (NULL tenant_id) row must survive RLS.

Layer 08 applied ONE uniform predicate to all 25 tenant tables:

    USING      (tenant_id = auth.current_tenant_id())
    WITH CHECK (tenant_id = auth.current_tenant_id())

For a NULL `tenant_id` that expression evaluates to NULL, and SQL treats NULL as
not-true. So under the strict form:

  * a NULL-tenant row is invisible to EVERY tenant, and
  * inserting one fails the WITH CHECK outright.

`notifications.tenant_id` and `ai_token_usage.tenant_id` are nullable — a NULL
there means "global / system", not "corrupt" — so global notifications and
system AI token-usage rows were unpersistable outside a bypass.

The fix is deliberately narrow: only the tables whose column is actually
nullable get the NULL-admitting predicate. Every other table keeps strict
equality, so isolation is unchanged everywhere else. The narrowness is asserted
here, because widening the predicate to all 25 tables would hand every tenant
visibility of any NULL-tenant row in the system.

These are pure-logic tests over the migration source — no database required.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MIGRATION = Path("src/migrations/versions/08_enable_rls_policies.py")


def _load():
    spec = importlib.util.spec_from_file_location("m08_s11", MIGRATION)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def m08():
    return _load()


class TestPredicateSelection:
    def test_nullable_tables_admit_global_rows_on_READ(self, m08) -> None:
        """`USING` admits the global (NULL) rows, so they stay visible."""
        for table in m08.GLOBAL_TENANT_TABLES:
            p = m08._tenant_read_predicate(table)
            assert "tenant_id IS NULL" in p, f"{table} would still reject global rows"
            assert "auth.current_tenant_id()" in p

    def test_nullable_tables_do_NOT_admit_globals_on_WRITE(self, m08) -> None:
        """`WITH CHECK` stays strict, even for the nullable tables.

        This is the privilege boundary. `USING` and `WITH CHECK` answer
        different questions — visibility and authorship. Reusing one relaxed
        string for both granted a WRITE alongside the read: any tenant-bound
        role could INSERT a row with `tenant_id IS NULL`, and `USING` then made
        that row visible to EVERY tenant. That is a way to forge a
        platform-wide notification, or poison the shared AI token-usage ledger,
        from inside a single tenant.

        Global rows stay creatable through `bypass_rls` — the audited path for
        system writers.
        """
        for table in m08.GLOBAL_TENANT_TABLES:
            w = m08._tenant_write_predicate(table)
            assert "IS NULL" not in w, f"{table} would let any tenant author a global row"
            assert w == "tenant_id = auth.current_tenant_id()"

    def test_read_and_write_predicates_are_distinct_helpers(self, m08) -> None:
        """Guards against a future refactor re-merging the two."""
        assert callable(m08._tenant_read_predicate)
        assert callable(m08._tenant_write_predicate)
        assert not hasattr(m08, "_tenant_predicate"), (
            "the single-predicate helper is what caused the write-side grant; "
            "it must not come back"
        )

    def test_nullable_set_is_exactly_the_two_tables(self, m08) -> None:
        """Pinned deliberately — verified against the live schema.

        Widening this set would expose every NULL-tenant row in that table to
        every tenant. Narrowing it would re-break the two named tables.
        """
        assert set(m08.GLOBAL_TENANT_TABLES) == {"notifications", "ai_token_usage"}

    @pytest.mark.parametrize("table", ["users", "posts", "api_keys", "billing_records", "audit_logs"])
    def test_tenant_scoped_tables_keep_strict_equality(self, m08, table: str) -> None:
        """Isolation must be unchanged everywhere else — reads AND writes."""
        for p in (m08._tenant_read_predicate(table), m08._tenant_write_predicate(table)):
            assert p == "tenant_id = auth.current_tenant_id()"
            assert "IS NULL" not in p


class TestEveryRlsTableHasAPredicate:
    def test_all_tenant_tables_are_classified(self, m08) -> None:
        """No table may fall through to an undefined predicate."""
        for table in m08.TENANT_TABLES:
            assert m08._tenant_read_predicate(table), table
            assert m08._tenant_write_predicate(table), table

    def test_global_tables_are_a_subset_of_tenant_tables(self, m08) -> None:
        """A global table outside the RLS set would never get the policy."""
        assert set(m08.GLOBAL_TENANT_TABLES) <= set(m08.TENANT_TABLES)


class TestMigrationUsesTheHelper:
    def test_upgrade_does_not_hardcode_the_strict_predicate(self) -> None:
        """Guards against the helper being defined but never wired in."""
        src = MIGRATION.read_text(encoding="utf-8")
        upgrade = src[src.index("def upgrade") : src.index("def downgrade")]
        # Both helpers must be wired in, and to the right clause.
        assert "_tenant_read_predicate(table)" in upgrade, "upgrade() must use the read predicate for USING"
        assert "_tenant_write_predicate(table)" in upgrade, "upgrade() must use the write predicate for WITH CHECK"
        # The literal strict form must only appear inside the helpers themselves.
        assert upgrade.count("tenant_id = auth.current_tenant_id()") == 0, (
            "upgrade() still hardcodes a predicate for every table"
        )
