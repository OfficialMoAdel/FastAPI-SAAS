"""Tests for `scripts/check_schema_parity.py` (the ORM/migration column gate).

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
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path("scripts/check_schema_parity.py")


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
        monkeypatch.setattr(gate, "_model_tables", lambda: {"t": {"present", "ghost"}})
        monkeypatch.setattr(gate, "_migration_columns", lambda: {"t": {"present"}})
        assert gate.main() == 1

    def test_table_missing_from_migrations_exits_1(self, gate, monkeypatch):
        monkeypatch.setattr(gate, "_model_tables", lambda: {"orphan": {"id"}})
        monkeypatch.setattr(gate, "_migration_columns", lambda: {"other": {"id"}})
        assert gate.main() == 1

    def test_parity_exits_0(self, gate, monkeypatch):
        monkeypatch.setattr(gate, "_model_tables", lambda: {"t": {"a", "b"}})
        monkeypatch.setattr(gate, "_migration_columns", lambda: {"t": {"a", "b"}})
        assert gate.main() == 0


class TestScriptRunsAsCIInvokesIt:
    def test_module_runs_and_exits_zero(self) -> None:
        """CI runs `python scripts/check_schema_parity.py` directly."""
        import subprocess
        import sys

        proc = subprocess.run(
            [sys.executable, "scripts/check_schema_parity.py"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert proc.returncode == 0, f"stdout={proc.stdout} stderr={proc.stderr}"
