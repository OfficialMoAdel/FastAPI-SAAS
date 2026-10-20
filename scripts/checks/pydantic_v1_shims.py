"""Reject Pydantic v1 calling conventions in a Pydantic v2 codebase.

``pyproject.toml`` requires ``pydantic>=2.6.1`` (locked at 2.11.5), so
``parse_obj()``, ``.dict()`` and the other v1-model methods are deprecated
compatibility shims. They are not stylistic noise: under v2 ``.dict()``
excludes nothing by default whereas v1 excluded unset fields, and
``parse_obj()`` validates with v1 coercion rules. A cache serializer or a
response builder that mixes the two produces a payload that looks right and
is subtly wrong.

There is no ruff rule for this (checked against ruff 0.11.13: ``UP006``,
``UP007``, ``UP045`` and ``UP046`` are all annotation-shape rules, none
concerned with v1 model methods), so the gate is a scan, matching the
``scripts/checks/`` convention already used for the comment-grease and
doc-path gates.

AST-based on purpose. ``tokenize`` would match ``.dict()`` inside strings
and comments — including this file's own docstring — which is precisely the
false positive that makes a scan get switched off.

``hasattr(x, "parse_obj")`` is NOT banned. The v1 branch it guarded has been
deleted as unreachable, so such a probe is now a genuine test of an
unreachable hypothesis rather than a shim call.

Usage:
    uv run python scripts/checks/pydantic_v1_shims.py
    uv run python scripts/checks/pydantic_v1_shims.py --verbose

Exit codes:
    0 - no v1 shim calls found in production code
    1 - one or more v1 shim calls found
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Production code only. Tests may legitimately name the shims — a test
# asserting their absence has to — and this script's own docstring and
# table name every one of them.
SCAN_DIRS = ("src/app",)
SKIP_DIR_PARTS = {"__pycache__", ".mypy_cache", ".venv", "node_modules"}

# Attribute name -> why the v2 spelling differs.
#
# Deliberately EXCLUDED: `.copy()`, `.json()`, `.schema()`, `.construct()`.
# Those are v1 shims on a *model*, but they are also ordinary methods on
# `dict`/`set`/`list` (`copy()`), on `httpx`/`requests` responses (`json()`),
# and on third-party libs. Banning the bare attribute name would flag every
# one of those, and a gate that flags correct code gets switched off — which
# is the failure mode this whole class of bead exists to prevent. The list
# below contains only names that are v1-model-specific and appear on nothing
# else.
V1_METHODS = {
    "parse_obj": "model_validate",
    "parse_raw": "model_validate_json",
    "parse_file": "model_validate_json",
    "from_orm": "model_validate",
    "update_forward_refs": "model_rebuild",
    "schema_json": "model_json_schema",
    "schema_of": "model_json_schema",
    "dict": "model_dump",
}

# Classmethod spellings, reached as Name.attr rather than Attribute.attr.
V1_CLASSMETHODS = {"parse_obj": "model_validate", "from_orm": "model_validate", "schema_of": "model_json_schema"}


@dataclass(frozen=True)
class Hit:
    path: Path
    line: int
    called: str
    replacement: str | None

    def render(self, root: Path) -> str:
        rel = self.path.relative_to(root)
        return f"{rel}:{self.line}: `{self.called}` is Pydantic v1; use `{self.replacement}`"


def _python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for rel in SCAN_DIRS:
        base = root / rel
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if SKIP_DIR_PARTS & set(path.parts):
                continue
            files.append(path)
    return sorted(files)


def _is_attribute(node: ast.expr) -> bool:
    return isinstance(node, ast.Attribute)


def scan_file(path: Path) -> list[Hit]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[Hit] = []
    for node in ast.walk(tree):
        if _is_attribute(node):
            attr = node.attr  # type: ignore[union-attr]
            if attr in V1_METHODS:
                hits.append(
                    Hit(path, node.lineno, f".{attr}()", V1_METHODS[attr])
                )
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name in V1_CLASSMETHODS:
                hits.append(
                    Hit(path, node.lineno, f"{name}()", V1_CLASSMETHODS[name] or "model_json_schema")
                )
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="also show the weak rules")
    args = parser.parse_args()

    hits: list[Hit] = []
    for path in _python_files(ROOT):
        try:
            hits.extend(scan_file(path))
        except SyntaxError as exc:
            print(f"error: could not parse {path}: {exc}", file=sys.stderr)
            return 1

    if hits:
        print(f"Pydantic v1 shims found in production code ({len(hits)}):")
        for hit in hits:
            print(f"  {hit.render(ROOT)}")
        if args.verbose:
            print("\nBanned: " + ", ".join(sorted(V1_METHODS)))
        return 1

    print("No Pydantic v1 shims in src/app.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
