"""``src/scripts/verify_indexes.py`` must catch a missing/wrong pagination index.

The operator script runs against a live database, so its comparison logic is
tested here as a pure function. Both directions matter:

* a MISSING index and an index with the wrong LEADING columns are reported;
* a correct index is not (a gate that cries wolf gets ignored);
* every expected name exists in the migrations or the ORM, so the script
  cannot demand an index nothing creates.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.scripts.verify_indexes import EXPECTED_INDEXES, check_indexes, index_predicate, indexed_columns

_REPO = Path(__file__).resolve().parents[3]


class TestIndexedColumns:
    def test_parses_a_plain_composite(self) -> None:
        assert indexed_columns("CREATE INDEX i ON public.t USING btree (tenant_id, created_at)") == [
            "tenant_id",
            "created_at",
        ]

    def test_sort_order_is_not_a_column(self) -> None:
        """DESC/ASC are stripped: PostgreSQL scans an index backwards anyway."""
        assert indexed_columns("CREATE INDEX i ON public.t USING btree (tenant_id, created_at DESC, id DESC)") == [
            "tenant_id",
            "created_at",
            "id",
        ]

    def test_a_partial_index_still_parses(self) -> None:
        definition = "CREATE INDEX i ON public.t USING btree (tenant_id, created_at DESC) WHERE (is_deleted = false)"
        assert indexed_columns(definition) == ["tenant_id", "created_at"]

    def test_a_partial_index_without_a_trailing_sort_op_still_parses(self) -> None:
        """The WHERE clause's paren is not the column list's.

        ``rpartition(")")`` cut at the LAST paren — the predicate's — and
        returned ``["username", "tenant_id)"]``, so a perfectly correct schema
        produced a spurious "wrong columns" failure. The trailing ``DESC`` in
        the test above is exactly the shape that hid it.
        """
        definition = (
            "CREATE UNIQUE INDEX uq_user_username_tenant ON public.users "
            "USING btree (username, tenant_id) WHERE (is_deleted = false)"
        )
        assert indexed_columns(definition) == ["username", "tenant_id"]

    def test_a_definition_without_a_column_list_is_empty(self) -> None:
        assert indexed_columns("garbage") == []


class TestIndexPredicate:
    def test_a_plain_index_has_no_predicate(self) -> None:
        assert index_predicate("CREATE INDEX i ON public.t USING btree (tenant_id, created_at) ") is None

    def test_a_partial_index_reports_its_where_clause(self) -> None:
        definition = "CREATE INDEX i ON public.t USING btree (tenant_id) WHERE (is_deleted = false)"
        assert index_predicate(definition) == "(is_deleted = false)"


class TestCheckIndexes:
    @staticmethod
    def _definitions(*names: str) -> dict[tuple[str, str], str]:
        return {
            (table, index): f"CREATE INDEX {index} ON public.{table} USING btree ({', '.join(columns)})"
            for table, index, columns in EXPECTED_INDEXES
            if index in names
        }

    def test_all_present_reports_nothing(self) -> None:
        everything = self._definitions(*(index for _table, index, _cols in EXPECTED_INDEXES))
        assert check_indexes(everything) == []

    def test_a_missing_index_is_reported(self) -> None:
        missing_one = self._definitions(
            *(index for _table, index, _cols in EXPECTED_INDEXES[1:])
        )
        problems = check_indexes(missing_one)
        assert problems == [f"missing: {EXPECTED_INDEXES[0][1]} on {EXPECTED_INDEXES[0][0]}"]

    def test_a_partial_index_under_the_expected_name_is_not_a_pass(self) -> None:
        """A predicate the query does not filter on cannot serve it.

        ``idx_files_tenant_active`` is the documented example: same columns as
        the list index, filtered on ``is_deleted``, and the list never filters
        it. Reporting a green SUCCESS there is the false positive this script
        exists to prevent, and the static gate
        (``scripts/checks/index_predicate_coverage.py``) already rejects any
        index carrying a predicate.
        """
        definitions = self._definitions(*(index for _table, index, _cols in EXPECTED_INDEXES))
        table, index, columns = next(entry for entry in EXPECTED_INDEXES if entry[1] == "idx_files_tenant_created")
        definitions[(table, index)] = (
            f"CREATE INDEX {index} ON public.{table} USING btree "
            f"({', '.join(columns)}) WHERE (is_deleted = false)"
        )

        problems = check_indexes(definitions)

        assert len(problems) == 1
        assert problems[0].startswith(f"partial index: {index} on {table}")

    def test_wrong_leading_columns_are_reported_as_wrong(self) -> None:
        """Same name, unusable index: name-only lookup would call this present."""
        definitions = self._definitions(*(index for _table, index, _cols in EXPECTED_INDEXES))
        table, index, _columns = next(
            entry for entry in EXPECTED_INDEXES if entry[1] == "idx_audit_logs_tenant_created_id"
        )
        definitions[(table, index)] = (
            f"CREATE INDEX {index} ON public.{table} USING btree (created_at, tenant_id, id)"
        )

        problems = check_indexes(definitions)
        assert len(problems) == 1
        assert problems[0].startswith(f"wrong columns: {index} on {table}")

    def test_an_empty_catalogue_reports_every_index_missing(self) -> None:
        assert len(check_indexes({})) == len(EXPECTED_INDEXES)


class TestExpectedNamesAreReal:
    """The script must not demand an index that no migration or model creates."""

    @staticmethod
    def _repo_sources() -> str:
        patterns = (
            _REPO / "src" / "migrations" / "versions",
            _REPO / "src" / "app",
        )
        chunks: list[str] = []
        for root in patterns:
            for path in root.rglob("*.py"):
                chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
        return "\n".join(chunks)

    @pytest.mark.parametrize(("_table", "index", "_columns"), EXPECTED_INDEXES)
    def test_the_name_appears_somewhere_in_the_tree(self, _table: str, index: str, _columns: tuple[str, ...]) -> None:
        sources = self._repo_sources()
        assert re.search(rf"\b{re.escape(index)}\b", sources), (
            f"{index} is asserted by verify_indexes.py but nothing in src/ creates it"
        )

    def test_the_audit_keyset_composite_leads_with_the_tenant(self) -> None:
        """The seek is ``tenant_id = ? AND (created_at, id) < (...)``."""
        entry = next(
            item for item in EXPECTED_INDEXES if item[1] == "idx_audit_logs_tenant_created_id"
        )
        assert entry == ("audit_logs", "idx_audit_logs_tenant_created_id", ("tenant_id", "created_at", "id"))

    def test_the_posts_entry_is_the_keyset_composite_not_the_list_index(self) -> None:
        """``get_posts_keyset`` filters (tenant, user, deleted) and orders (created_at, id).

        ``idx_post_tenant_active`` is ``(tenant_id, created_at)`` — it cannot
        serve that seek. Asserting it meant a green SUCCESS while the keyset
        path was unindexed.
        """
        entry = next(item for item in EXPECTED_INDEXES if item[0] == "post")
        assert entry == (
            "post",
            "idx_posts_tenant_user_created_id",
            ("tenant_id", "created_by_user_id", "is_deleted", "created_at", "id"),
        )

    def test_the_files_entry_is_the_unfiltered_list_index(self) -> None:
        """``list_files`` is ``WHERE tenant_id = ? ORDER BY created_at DESC``.

        ``idx_files_tenant_active`` is partial on ``is_deleted = false``, and the
        query never filters ``is_deleted`` (the column is not even on the ORM
        model), so that index cannot serve it. The ORM-declared
        ``idx_files_tenant_created`` is the one the list relies on.
        """
        entry = next(item for item in EXPECTED_INDEXES if item[0] == "files")
        assert entry == ("files", "idx_files_tenant_created", ("tenant_id", "created_at"))
