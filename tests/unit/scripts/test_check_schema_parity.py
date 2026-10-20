"""Tests for `scripts/checks/schema_parity.py` (the ORM/migration column gate).

The gate exists because a column mapped on a model but never created by a
migration is invisible to every other check in this repo: `create_all` derives
the schema FROM the ORM, so a create_all database always has the column, and
the test suite runs against such a database. Only a migrated database breaks,
with `UndefinedColumnError`.

Two properties matter and are easy to get wrong:

1. **It must not pass vacuously.** A parser that silently matches nothing
   reports "parity OK" forever. The gate refuses to pass when it parsed no
   models at all — that guard is exercised here.
2. **It must FAIL on drift, not warn.** A gate that only logs is decoration.
   The drift case is asserted by pointing the parsers at a temp tree.

NAMES were not enough, which is `FastAPI_saas-d201` (bead) and `FastAPI_saas-vthy`
(the defect it let through). `api_keys.scopes` was `Text` on the model and
`postgresql.JSONB` in migration 03, and nothing in the tree noticed: this gate
compared names, `create_all` derived the dev schema FROM the ORM so a
create_all database could never show it, and the CI `alembic check` that can
see types carries `continue-on-error: true`. On a migrated database the asyncpg
dialect decodes a jsonb column into a Python object, so `json_loads(key.scopes)`
raised and every `X-API-Key` request 500'd — while minting kept working, because
the INSERT of a JSON string into jsonb is accepted.

So the gate now also compares TYPES, and the comparison is deliberately narrow:
it fails only where a difference is OBSERVABLE at runtime. A wider string in the
migration accepts everything the ORM does, so it is not drift; a jsonb column
behind a non-JSON ORM type hands the application a different Python object, so
it is. The census that settled that (351 columns, 114 raw mismatches, of which
58 were the parser failing to resolve the ORM side at all) is in
`perf-results/RESULT-20261017-vthy/api_keys_column_type_parity.json`.

The properties the type half must keep:

3. **The ORM side must resolve every column**, or the comparison is blind on
   exactly the columns it cannot see. That was 58 of 351 columns.
4. **The allowlist must not become a dumping ground.** Every entry has to state
   why, and a `benign:` claim has to be distinguishable from a `pending` one
   that names a bead; an entry that no longer corresponds to real drift fails.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

SCRIPT = Path("scripts/checks/schema_parity.py")
WORKFLOW = Path(".github/workflows/tests.yml")


def _load():
    spec = importlib.util.spec_from_file_location("check_schema_parity", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gate():
    return _load()


class TestParsersFindRealSchema:
    def test_model_tables_are_parsed(self, gate) -> None:
        tables = gate._model_tables()
        assert len(tables) > 20, f"only parsed {len(tables)} tables — parser is broken"
        assert "pricing" in tables
        assert "currency" in tables["pricing"]

    def test_migration_columns_are_parsed(self, gate) -> None:
        tables = gate._migration_columns()
        assert len(tables) > 20, f"only parsed {len(tables)} migration tables"
        assert "webhook_events" in tables
        # S-03/S-04 added this column; the gate only passes because of it.
        assert "completed_at" in tables["webhook_events"]

    def test_relationships_are_not_treated_as_columns(self, gate) -> None:
        """A relationship must not be demanded of the migration."""
        tables = gate._model_tables()
        assert "user" not in tables.get("api_keys", set()), "relationship leaked into columns"
        assert "tenant" not in tables.get("posts", set()), "relationship leaked into columns"

    def test_real_tree_has_no_drift(self, gate) -> None:
        models = gate._model_tables()
        migrations = gate._migration_columns()
        drift = []
        for table in sorted(models):
            missing = sorted(models[table] - migrations.get(table, set()))
            if missing:
                drift.append(f"{table}: {missing}")
        assert not drift, f"ORM/migration column drift in the repo: {drift}"


class TestGateRefusesToPassVacuously:
    def test_no_models_means_exit_2(self, gate, monkeypatch, tmp_path):
        """The anti-vacuous guard: parsing nothing must FAIL, not pass."""
        monkeypatch.setattr(gate, "SRC", tmp_path)  # no .py files
        monkeypatch.setattr(gate, "MIGRATIONS", tmp_path)
        monkeypatch.setattr(gate, "_migration_columns", dict)
        assert gate.main() == 2

    def test_missing_migrations_dir_means_exit_2(self, gate, monkeypatch, tmp_path):
        monkeypatch.setattr(gate, "MIGRATIONS", tmp_path / "nope")
        assert gate.main() == 2


class TestGateFailsOnDrift:
    def test_missing_column_exits_1(self, gate, monkeypatch):
        """A column on the ORM with no migration counterpart must be an ERROR.

        Guards against the gate degrading into a reporter: if this ever
        returns 0 again, the whole gate is decorative.
        """
        monkeypatch.setattr(gate, "_model_column_types", lambda: {"t": {"present": "Integer", "ghost": "Integer"}})
        monkeypatch.setattr(gate, "_migration_column_types", lambda: {"t": {"present": "Integer"}})
        assert gate.main() == 1

    def test_table_missing_from_migrations_exits_1(self, gate, monkeypatch):
        monkeypatch.setattr(gate, "_model_column_types", lambda: {"orphan": {"id": "Integer"}})
        monkeypatch.setattr(gate, "_migration_column_types", lambda: {"other": {"id": "Integer"}})
        assert gate.main() == 1

    def test_parity_exits_0(self, gate, monkeypatch):
        monkeypatch.setattr(gate, "_model_column_types", lambda: {"t": {"a": "Integer", "b": "String(10)"}})
        monkeypatch.setattr(gate, "_migration_column_types", lambda: {"t": {"a": "Integer", "b": "String(length=10)"}})
        assert gate.main() == 0


class TestScriptRunsAsCIInvokesIt:
    def test_module_runs_and_exits_zero(self) -> None:
        """CI runs `python scripts/checks/schema_parity.py` directly."""
        import subprocess
        import sys

        proc = subprocess.run(
            [sys.executable, "scripts/checks/schema_parity.py"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert proc.returncode == 0, f"stdout={proc.stdout} stderr={proc.stderr}"


class TestTheOrmSideResolvesEveryColumn:
    """Property 3: a type comparison is blind on the columns it cannot resolve.

    Fifty-eight of 351 columns were invisible to the type half before this:
    `mapped_column()` with no positional type argument, `ForeignKey(...)`
    captured as if it were the type, and a string annotation read literally.
    """

    def test_every_column_the_name_gate_finds_also_has_a_resolved_type(self, gate) -> None:
        names = gate._model_tables()
        types = gate._model_column_types()
        unresolved: list[str] = []
        for table, columns in names.items():
            for column in sorted(columns):
                declared = types.get(table, {}).get(column, "")
                if not declared or declared == "?" or declared.startswith(("ForeignKey(", '"')):
                    unresolved.append(f"{table}.{column}={declared!r}")
        assert not unresolved, f"{len(unresolved)} ORM columns have no resolved type: {unresolved}"

    @pytest.mark.parametrize(
        ("table", "column", "referenced_table", "referenced_column"),
        [
            ("users", "tier_id", "tier", "id"),
            ("user_roles", "role_id", "roles", "id"),
            ("role_permissions", "permission_id", "permissions", "id"),
            ("token_blacklist", "tenant_id", "tenants", "id"),
        ],
    )
    def test_a_foreign_key_resolves_to_its_referenced_column_family(
        self, gate, table: str, column: str, referenced_table: str, referenced_column: str
    ) -> None:
        """An ORM foreign key must resolve like the column it points at.

        Otherwise every ``*.user_id`` compares as the text ``ForeignKey(...)``
        against the migration's integer and the family check is meaningless.
        """
        types = gate._model_column_types()
        fk = gate._column_family(types.get(table, {}).get(column, ""))
        target = gate._column_family(types.get(referenced_table, {}).get(referenced_column, ""))
        assert fk, f"{table}.{column} did not resolve to a family"
        assert fk == target, f"{table}.{column} is {fk} but {referenced_table}.{referenced_column} is {target}"

    @pytest.mark.parametrize("table", ["post", "rate_limit", "tier", "token_blacklist"])
    def test_a_primary_key_without_a_positional_type_resolves_from_its_annotation(self, gate, table: str) -> None:
        """``mapped_column(init=False)`` with no type argument still has a type.

        The annotation is the only source, and the census found four of these
        being read as the literal string ``"id"``.
        """
        family = gate._column_family(gate._model_column_types().get(table, {}).get("id", ""))
        assert family == gate._column_family("Integer"), f"{table}.id resolved to {family!r}"


class TestWhatCountsAsObservableDrift:
    """Property: the gate compares the families, not the spellings."""

    @pytest.mark.parametrize(
        ("orm_type", "migration_type", "expected"),
        [
            # A family mismatch is the vthy class: the driver hands back a
            # different Python object than the ORM promised.
            ("String(5)", "Time(True)", "family_mismatch"),
            ("Text", "JSONB", "family_mismatch"),
            ("JSONB", "Text", "family_mismatch"),
            ("String", "UUID", "family_mismatch"),
            # The ORM states a width and the column is tighter: a value the ORM
            # accepts is rejected or truncated.
            ("String(1024)", "String(500)", "narrower_in_migration"),
            ("Text", "String(500)", "narrower_in_migration"),
            ("String(128)", "String(length=100)", "narrower_in_migration"),
            # The ORM states no width at all. `String` is SQLAlchemy's
            # "unbounded", so the column caps something the model never
            # promised; reported, not drift.
            ("String", "String(255)", "unbounded_orm_width"),
            ("String", "String(length=255)", "unbounded_orm_width"),
            # The column is looser: nothing observable changes.
            ("String(50)", "String(255)", "wider_in_migration"),
            ("String(255)", "Text", "wider_in_migration"),
            # Different spellings, same Python shape.
            ("BYTEA", "LargeBinary", "equivalent_naming"),
            ("DateTime", "DateTime(True)", "equivalent_naming"),
            ("JSON", "JSONB", "equivalent_naming"),
            ("BigInteger", "Integer", "equivalent_naming"),
            ("SmallInteger", "Integer", "equivalent_naming"),
            ("Enum(NotificationStatus, native_enum=False, length=20)", "String(length=20)", "equivalent_naming"),
        ],
    )
    def test_classification(self, gate, orm_type: str, migration_type: str, expected: str) -> None:
        assert gate._classify_type(orm_type, migration_type) == expected

    def test_a_width_written_as_a_keyword_is_still_a_width(self, gate) -> None:
        """`sa.String(length=255)` and `String(255)` are the same cap.

        Reading only the positional form made every migration column look
        unbounded, which silently downgraded every narrowed column to benign.
        """
        assert gate._declared_width("sa.String(length=255)") == 255
        assert gate._declared_width("String(255)") == 255
        assert gate._declared_width("String") is None
        assert gate._declared_width("sa.Text()") is None

    def test_the_json_asymmetry_is_the_whole_lesson(self, gate) -> None:
        """``JSON`` vs ``JSONB`` is benign; ``Text`` vs ``JSONB`` is not.

        The harm is a jsonb column sitting behind an ORM type that is not JSON:
        the ORM's own processing then never runs, and the application receives an
        object where it stored a string. That is precisely `api_keys.scopes`.
        """
        assert gate._classify_type("JSON", "JSONB") == "equivalent_naming"
        assert gate._classify_type("Text", "JSONB") == "family_mismatch"


class TestTheGateFailsOnObservableTypeDrift:
    def test_the_real_tree_has_no_unallowlisted_observable_drift(self, gate) -> None:
        """Every row the type half reports must be either absent or accounted for."""
        drift = gate._type_drift(gate._model_column_types(), gate._migration_column_types())
        unaccounted = [line for line in drift if gate._key(line) not in gate.TYPE_ALLOWLIST]
        assert not unaccounted, f"observable type drift with no allowlist entry: {unaccounted}"

    def test_a_family_mismatch_exits_1(self, gate, monkeypatch, capsys) -> None:
        monkeypatch.setattr(gate, "_model_column_types", lambda: {"t": {"c": "String(5)"}})
        monkeypatch.setattr(gate, "_migration_column_types", lambda: {"t": {"c": "Time(True)"}})
        assert gate.main() == 1

    def test_a_narrowed_column_exits_1(self, gate, monkeypatch) -> None:
        monkeypatch.setattr(gate, "_model_column_types", lambda: {"t": {"c": "String(1024)"}})
        monkeypatch.setattr(gate, "_migration_column_types", lambda: {"t": {"c": "String(500)"}})
        assert gate.main() == 1

    def test_a_wider_column_exits_0(self, gate, monkeypatch) -> None:
        monkeypatch.setattr(gate, "_model_column_types", lambda: {"t": {"c": "String(50)"}})
        monkeypatch.setattr(gate, "_migration_column_types", lambda: {"t": {"c": "String(255)"}})
        assert gate.main() == 0

    def test_an_allowlisted_row_does_not_fail_the_gate_but_is_still_reported(self, gate, monkeypatch, capsys) -> None:
        """Suppressing a row must not make it disappear.

        An allowlist is how a measured-but-unfixed finding stays visible without
        breaking the build, so the row has to be named on stdout either way.
        """
        monkeypatch.setattr(gate, "_model_column_types", lambda: {"t": {"c": "String(5)"}})
        monkeypatch.setattr(gate, "_migration_column_types", lambda: {"t": {"c": "Time(True)"}})
        monkeypatch.setitem(gate.TYPE_ALLOWLIST, ("t", "c"), "benign: asserted by this test")
        assert gate.main() == 0
        assert "t.c" in capsys.readouterr().out, "an allowlisted row was suppressed silently"

    def test_every_allowlist_entry_states_why_and_says_whether_it_is_benign(self, gate) -> None:
        """`benign:` for a triaged claim, a bead id for a deferred one.

        A bare string cannot be audited: the whole failure mode of an allowlist
        is an entry nobody can re-check.
        """
        assert gate.TYPE_ALLOWLIST, "the allowlist is empty; that is suspicious enough to assert"
        for key, reason in gate.TYPE_ALLOWLIST.items():
            assert reason.strip(), f"{key} is allowlisted with no reason"
            assert reason.startswith("benign:") or re.search(r"FastAPI_saas-[0-9a-z]{4}", reason), (
                f"{key} does not say whether it is benign or which bead owns it: {reason!r}"
            )

    def test_an_allowlist_entry_that_no_longer_matches_real_drift_is_a_failure(self, gate) -> None:
        """A stale entry is a false claim about the schema, which is what we are gating."""
        drift = gate._type_drift(gate._model_column_types(), gate._migration_column_types())
        present = {gate._key(line) for line in drift}
        stale = [f"{table}.{column}" for (table, column) in gate.TYPE_ALLOWLIST if (table, column) not in present]
        assert not stale, f"allowlist entries no longer correspond to drift: {stale}"

    def test_the_type_half_refuses_to_pass_vacuously(self, gate, monkeypatch) -> None:
        """If neither type parser resolves anything, exit 2 — never 0."""
        monkeypatch.setattr(gate, "SRC", Path("src/app"))
        monkeypatch.setattr(gate, "_model_column_types", dict)
        monkeypatch.setattr(gate, "_migration_column_types", dict)
        assert gate.main() == 2


class TestAlembicCheckStaysNonBlockingOnPurpose:
    """Decision recorded for `FastAPI_saas-d201`.

    CI already runs a real `alembic check`, which is the only tool here that can
    see types at all. It stays `continue-on-error: true`: making it blocking would
    fail the build on ~395 unexamined diffs, and its remaining value is the part
    the AST gate cannot reach — policies and constraints. The comment has to say
    so, or the next reader will "fix" the continue-on-error.
    """

    def _alembic_step(self) -> str:
        """The comment block plus the step that runs the real ``alembic check``.

        Anchored on the command, not on the word "alembic check", so the window
        covers the step's own ``continue-on-error`` and the reasoning above it.
        """
        text = WORKFLOW.read_text(encoding="utf-8")
        index = text.index("alembic -c src/alembic.ini check")
        return text[max(0, index - 3000) : index + 300]

    def test_the_step_is_still_tolerated(self) -> None:
        assert "continue-on-error: true" in self._alembic_step()

    def test_the_comment_names_the_gate_that_now_covers_types(self) -> None:
        step = self._alembic_step()
        assert "scripts/checks/schema_parity.py" in step, (
            "the alembic check step must name the gate that took over its type coverage, "
            "or a future reader will make it blocking on ~395 untriaged diffs"
        )
