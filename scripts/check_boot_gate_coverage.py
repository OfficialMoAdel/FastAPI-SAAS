"""Every boot gate in `src/app` must have a boot-failure test.

A startup gate exists to turn a missing registration into a boot failure. This
script answers one question: is every such gate actually exercised?

It parses `src/app` for top-level `ensure_*_registered` functions and compares
them against the `GATES` table in
`tests/unit/core/test_boot_gates.py`, which is where the "unwire it and assert
it raises" tests live. A gate added to production without a row there is a gate
nobody has proven fires — the failure mode this project has already met twice:
`.importlinter` sat inert because stale ignore entries aborted it before any
edge was evaluated, and the ARQ worker once ran on an import side effect rather
than on a called gate.

This is a script rather than a pytest because it is a discovery sweep over
source files, and the repo ratchets source-text tests DOWN
(`docs/testing/source-text-ratchet.md`). The per-PR visibility the check exists
for is delivered by the CI job, which prints the count every run.

Usage:
    uv run python scripts/check_boot_gate_coverage.py
    uv run python scripts/check_boot_gate_coverage.py --verbose

Exit codes:
    0 - every gate in src/app has a boot-failure test
    1 - at least one gate is unproven, or discovery itself is broken
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "app"
TABLE = ROOT / "tests" / "unit" / "core" / "test_boot_gates.py"

GATE_PREFIX = "ensure_"
GATE_SUFFIX = "_registered"


def _discover_gates(root: Path) -> tuple[set[str], int]:
    """Top-level gate function names, and how many files were parsed."""
    names: set[str] = set()
    parsed = 0
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parsed += 1
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if node.name.startswith(GATE_PREFIX) and node.name.endswith(GATE_SUFFIX):
                names.add(node.name)
    return names, parsed


def _covered_gates(table: Path) -> set[str]:
    """Gate names the test table claims to cover."""
    tree = ast.parse(table.read_text(encoding="utf-8"), filename=str(table))
    covered: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Gate":
            for keyword in node.keywords:
                if keyword.arg == "func" and isinstance(keyword.value, ast.Constant):
                    covered.add(str(keyword.value.value))
    return covered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="also list every covered gate")
    args = parser.parse_args()

    if not SRC.is_dir():
        print(f"error: scan root missing, check is vacuous: {SRC}", file=sys.stderr)
        return 1
    if not TABLE.is_file():
        print(f"error: gate table missing: {TABLE}", file=sys.stderr)
        return 1

    declared, parsed = _discover_gates(SRC)
    covered = _covered_gates(TABLE)

    if parsed < 100:
        print(f"error: discovery parsed only {parsed} files; the scan is broken", file=sys.stderr)
        return 1
    if not declared:
        print("error: no gates discovered; the pattern or root is wrong", file=sys.stderr)
        return 1

    missing = sorted(declared - covered)

    print(f"boot gates with a boot-failure test: {len(covered & declared)}/{len(declared)}")
    if args.verbose:
        for name in sorted(declared):
            mark = "ok " if name in covered else "MISSING"
            print(f"  [{mark}] {name}")

    if missing:
        print(f"\nFAIL: {len(missing)} boot gate(s) with no boot-failure test:")
        for name in missing:
            print(f"  {name}")
        print(
            "\nAdd a row to GATES in tests/unit/core/test_boot_gates.py with the "
            "gate's module, seams, error type, and the fragment its message must name."
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
