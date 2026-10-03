"""Static ORM <-> migration column parity gate.

A column declared on a SQLAlchemy model but never created (or added) by a
migration is invisible to every other gate in this repo:

* ``create_all`` DERIVES the schema from the ORM, so a database built that way
  always has the column and nothing diverges;
* the index gates compare indexes, not columns;
* the test suite passes, because the tests run against a create_all schema.

The failure mode is a hard one: on a database built by ``alembic upgrade head``
the query raises ``UndefinedColumnError``. ``pricing.currency`` was exactly
this — declared on the ORM, read by ``billing/service.py:189``, and absent from
migration 04, so every Pricing read failed on any migrated database.

This is a pure-AST gate: no database, no ORM import, so it runs in the lint
job and fails fast. The workflow also runs a real ``alembic check`` in the
tests job, which additionally catches policies/constraints Alembic can see and
AST cannot.

Exit codes: 0 = parity, 1 = drift, 2 = gate could not run.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC = Path("src/app")
MIGRATIONS = Path("src/migrations/versions")

# Relationship/association helpers that look like columns to a naive walk.
NON_COLUMN_MARKERS = ("relationship(", "association_proxy(")


def _model_tables() -> dict[str, set[str]]:
    """table name -> mapped column names, from every module defining models."""
    tables: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            tablename: str | None = None
            columns: set[str] = set()
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                    t = stmt.targets[0]
                    if isinstance(t, ast.Name) and t.id == "__tablename__":
                        if isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                            tablename = stmt.value.value
                if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
                    continue
                value = stmt.value
                if value is None:
                    continue
                dumped = ast.dump(value)
                # A column is declared via mapped_column(...). Anything else
                # assigned to an annotated name in a model class (a
                # relationship(), a plain default) is not a column.
                if "mapped_column" in dumped:
                    columns.add(stmt.target.id)
            if tablename and columns:
                tables.setdefault(tablename, set()).update(columns)
    return tables


def _migration_columns() -> dict[str, set[str]]:
    """table name -> columns, from op.create_table and op.add_column."""
    tables: dict[str, set[str]] = {}
    for path in sorted(MIGRATIONS.glob("*.py")):
        try:
            src = path.read_text(encoding="utf-8")
        except (SyntaxError, UnicodeDecodeError):
            continue

        # op.create_table("name", ... sa.Column("col", ...) ...)
        for chunk in src.split("op.create_table(")[1:]:
            import re

            name_match = re.search(r'"([a-z_]+)"', chunk)
            if not name_match:
                continue
            name = name_match.group(1)
            cols = {m.group(1) for m in re.finditer(r'sa\.Column\(\s*"([a-z_]+)"', chunk)}
            tables.setdefault(name, set()).update(cols)

        # op.add_column("table", sa.Column("col", ...))
        for m in re.finditer(r'op\.add_column\(\s*"([a-z_]+)"\s*,\s*sa\.Column\(\s*"([a-z_]+)"', src):
            tables.setdefault(m.group(1), set()).add(m.group(2))
    return tables


def main() -> int:
    if not MIGRATIONS.is_dir():
        print(f"check_schema_parity: {MIGRATIONS} not found", file=sys.stderr)
        return 2

    models = _model_tables()
    migrations = _migration_columns()

    if not models:
        print("check_schema_parity: no models parsed — refusing to pass vacuously", file=sys.stderr)
        return 2

    drift: list[str] = []
    for table in sorted(models):
        if table not in migrations:
            drift.append(f"{table}: model has no create_table/add_column in any migration")
            continue
        missing = sorted(models[table] - migrations[table])
        if missing:
            drift.append(f"{table}: columns on the ORM but not in any migration: {missing}")

    if drift:
        print("check_schema_parity: ORM/migration column drift detected", file=sys.stderr)
        for line in drift:
            print(f"  {line}", file=sys.stderr)
        print(
            "\nA migrated database (alembic upgrade head) will raise UndefinedColumnError for "
            "these columns, while a create_all database will not — so tests pass and production "
            "breaks. Add them to the owning migration layer "
            "(see src/migrations/AGENTS.md: extend the layer, do not stack a new revision).",
            file=sys.stderr,
        )
        return 1

    print(
        f"check_schema_parity: {len(models)} ORM tables match the migrations "
        f"({sum(len(c) for c in models.values())} columns)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
