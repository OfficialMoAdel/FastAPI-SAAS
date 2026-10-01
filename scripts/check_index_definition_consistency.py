#!/usr/bin/env python3
"""Every index NAME resolves to one consistent DEFINITION across both layers.

`scripts/check_index_predicate_coverage.py` answers "does this hot list query
have a usable index?". This answers the prior question: "do the ORM and the
migration layer actually agree on what the indexes ARE?".

Why this exists
---------------
Alembic compares indexes by NAME, and this repo declares no
``naming_convention`` (``src/app/core/db/base.py`` and
``src/migrations/env.py`` both omit one), so ``alembic revision
--autogenerate`` reports **no drift** for an index whose definition differs
between the two layers. The two shapes that can result:

* a deployment that applied the migrations gets the migration's definition;
* a database built from ``metadata.create_all`` gets the ORM's.

Same model file, two schemas, no error anywhere. The first instance found was
``idx_webhook_endpoint_active``: the migration created ``(tenant_id,
is_active)`` while the ORM declared a single-column partial ``(is_active)
WHERE is_active = true``. A pure name-existence check cannot see that, because
the name is identical by construction.

Both layers are read as AST. No database, no imports, no settings, so this
runs in the lint job.

Usage
-----
    uv run python scripts/check_index_definition_consistency.py
    uv run python scripts/check_index_definition_consistency.py --verbose
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = REPO_ROOT / "src" / "app" / "modules"
MIGRATIONS_DIR = REPO_ROOT / "src" / "migrations" / "versions"

SKIP_DIRS = {"__pycache__"}


@dataclass(frozen=True)
class IndexDef:
    """One index as declared in one layer."""

    name: str
    #: Column names in declared order. The order IS the definition: a btree
    #: only serves a predicate whose leading columns match.
    columns: tuple[str, ...]
    #: ``postgresql_where=...`` text, or "" when the index is not partial.
    partial: str = ""
    #: ``unique=True``. Part of the definition: a unique index and a plain
    #: one are different objects with different failure modes.
    unique: bool = False
    #: Where it was declared, for the failure message.
    origin: str = ""


@dataclass
class Report:
    #: name -> {layer -> IndexDef}, only for names declared in BOTH layers.
    shared: dict[str, dict[str, IndexDef]] = field(default_factory=dict)
    #: (name, orm_def, migration_def) where the two disagree.
    conflicts: list[tuple[str, IndexDef, IndexDef]] = field(default_factory=list)
    #: names seen in only one layer (reported, never fatal)
    orm_only: list[str] = field(default_factory=list)
    migration_only: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ORM layer
# ---------------------------------------------------------------------------


def _string_args(call: ast.Call) -> list[str]:
    out: list[str] = []
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            out.append(arg.value)
    return out


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def parse_orm_indexes(models_py: Path) -> list[IndexDef]:
    """Index() entries from a models.py, via AST."""
    try:
        tree = ast.parse(models_py.read_text(encoding="utf-8"), filename=str(models_py))
    except SyntaxError as exc:  # pragma: no cover - a parse failure is a build break
        print(f"FAIL could not parse {models_py}: {exc}", file=sys.stderr)
        raise

    found: list[IndexDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "Index"):
            continue
        args = _string_args(node)
        if not args:
            continue
        name, columns = args[0], tuple(args[1:])
        where = _keyword(node, "postgresql_where")
        partial = _predicate_literal(where) if where is not None else ""
        unique_kw = _keyword(node, "unique")
        unique = isinstance(unique_kw, ast.Constant) and unique_kw.value is True
        found.append(IndexDef(name, columns, partial, unique, f"ORM {models_py.name}"))
    return found


# ---------------------------------------------------------------------------
# Migration layer
# ---------------------------------------------------------------------------

_CREATE_INDEX = re.compile(
    r'op\.create_index\(\s*'
    r'(?P<q>["\'])(?P<name>[^"\']+)(?P=q)\s*,\s*'
    r'(?P<q2>["\'])(?P<table>[^"\']+)(?P=q2)\s*,\s*'
    r'\[(?P<cols>[^\]]*)\]',
    re.MULTILINE,
)
_INDEX_KW = re.compile(r'index=True|(?:unique|index)\s*=\s*True')

def _predicate_literal(node: ast.expr) -> str:
    """The predicate text of a ``postgresql_where=`` value, else "".

    Three spellings occur in this repo and all three mean the same thing:
    a bare string (``postgresql_where="status = 'failed'"``), ``text(...)``,
    and ``sa.text(...)``. Handling only the Call form made two indexes that
    both layers declare identically look like conflicts, which is worse than
    not checking - a false finding on a correct index trains people to ignore
    the gate.

    Returns "" for a genuinely non-literal predicate rather than guessing.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call) and node.args:
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return ""


def parse_migration_indexes(path: Path) -> list[IndexDef]:
    """op.create_index(...) calls from one migration file, via AST.

    AST rather than regex. Two regex attempts were wrong in ways that would
    have produced false findings: cutting the call at the first ")" truncated
    every ``sa.text(...)`` predicate, and widening the window to the next
    call picked up a predicate belonging to a DIFFERENT index. Comparing
    index definitions with a parser that guesses at call boundaries is worse
    than no check, because its output looks authoritative.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[IndexDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "create_index"):
            continue
        args = _string_args(node)
        if len(args) < 2:
            continue
        cols: tuple[str, ...] = ()
        if len(node.args) >= 3 and isinstance(node.args[2], ast.List):
            cols = tuple(
                elt.value
                for elt in node.args[2].elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            )
        where_node = _keyword(node, "postgresql_where")
        where = _predicate_literal(where_node) if where_node is not None else ""
        unique_kw = _keyword(node, "unique")
        unique = isinstance(unique_kw, ast.Constant) and unique_kw.value is True
        found.append(IndexDef(args[0], cols, where, unique, f"MIGRATION {path.name}"))
    return found




# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def same_definition(a: IndexDef, b: IndexDef) -> bool:
    """Do the two layers declare the same index?

    Three parts, all of them load-bearing: column list IN ORDER (a btree on
    (tenant_id, is_active) does not serve a predicate on (is_active)), the
    partial predicate, and whether the index is UNIQUE. Comparing only the
    column list is what made an earlier version of this script report two
    partial unique indexes as conflicts when both layers declared them
    identically.
    """
    return a.columns == b.columns and a.partial == b.partial and a.unique == b.unique


def build_report(verbose: bool) -> Report:
    orm: dict[str, IndexDef] = {}
    for models_py in sorted(MODELS_DIR.rglob("models.py")):
        if SKIP_DIRS.intersection(models_py.parts):
            continue
        for idx in parse_orm_indexes(models_py):
            # First declaration wins; a duplicate name inside one layer is a
            # separate (rarer) problem and does not need handling here.
            orm.setdefault(idx.name, idx)

    migration: dict[str, IndexDef] = {}
    for mig in sorted(MIGRATIONS_DIR.glob("*.py")):
        for idx in parse_migration_indexes(mig):
            migration.setdefault(idx.name, idx)

    report = Report()
    for name, mig_def in migration.items():
        orm_def = orm.get(name)
        if orm_def is None:
            report.migration_only.append(name)
            continue
        report.shared[name] = {"orm": orm_def, "migration": mig_def}
        if not same_definition(orm_def, mig_def):
            report.conflicts.append((name, orm_def, mig_def))
    report.orm_only = [n for n in orm if n not in migration]
    if verbose:
        print(f"ORM index declarations:      {len(orm)}")
        print(f"migration create_index:     {len(migration)}")
        print(f"names in BOTH layers:       {len(report.shared)}")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args(argv)

    report = build_report(args.verbose)

    for name, orm_def, mig_def in sorted(report.conflicts):
        print(
            f"FAIL {name}\n"
            f"     ORM       columns={list(orm_def.columns)} partial={orm_def.partial or '-'}"
            f"  ({orm_def.origin})\n"
            f"     MIGRATION columns={list(mig_def.columns)} partial={mig_def.partial or '-'}"
            f"  ({mig_def.origin})\n"
            f"     Same name, different definition. Alembic compares by name and this repo\n"
            f"     declares no naming_convention, so autogenerate reports NO drift for this.\n"
            f"     A migrated database and a create_all database get DIFFERENT schemas.",
            file=sys.stderr,
        )

    if report.conflicts:
        print(
            f"\n{len(report.conflicts)} index name(s) resolve to different definitions in the ORM "
            f"and the migration layer. Reconcile them, or rename one if both are genuinely "
            f"wanted (a partial index and a composite rarely want the same name).",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK  {len(report.shared)} shared index name(s) resolve to one consistent definition "
        f"({len(report.orm_only)} ORM-only, {len(report.migration_only)} migration-only, reported not enforced)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
