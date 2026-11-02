"""Verify graphify-out/graph.json still points at current source locations.

The graph is a navigation aid: a node that claims a symbol lives at a line
that no longer exists, or a symbol that was renamed away, sends the reader to
a dead location. That is worse than having no graph entry at all, because the
reader trusts it.

This gate compares every code node in the graph against the current tree:

* the claimed source file must still exist;
* the claimed line must be inside the file (empty files are allowed to claim
  ``L1``, which is how graphify represents the module itself);
* a label ending in ``()`` must still resolve to a function, method, or class
  name in that file.

It is intentionally structural rather than semantic: it does not re-run the
extractor and it does not require an LLM. Re-run the extractor when this gate
fails::

    graphify update . --force

The graph file is gitignored and may be absent on a fresh checkout. In that
case the gate is a no-op (exit 0) rather than a failure.

Usage:
    uv run python scripts/checks/graph_freshness.py
    uv run python scripts/checks/graph_freshness.py --verbose
    uv run python scripts/checks/graph_freshness.py --graph path/to/graph.json

Exit codes:
    0 - graph is fresh, or the graph file does not exist
    1 - one or more graph nodes are stale
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GRAPH = ROOT / "graphify-out" / "graph.json"

_LINE_RE = re.compile(r"^L(\d+)(?:-L?(\d+))?$")


@dataclass(frozen=True)
class Offense:
    """One stale graph location."""

    source_file: str
    label: str
    location: str
    reason: str

    def render(self, root: Path) -> str:
        try:
            location = str(Path(self.source_file).relative_to(root))
        except ValueError:
            location = self.source_file
        return f"  {location}:{self.location}: {self.label}  ({self.reason})"


def _symbols(tree: ast.AST) -> set[str]:
    """Every function, method, and class name defined in ``tree``."""
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }


def _source_index(path: Path) -> tuple[list[str], set[str] | None]:
    """Return the file's lines and its defined symbols, if it parses."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if path.suffix != ".py":
        return lines, None
    try:
        return lines, _symbols(ast.parse(text))
    except SyntaxError:
        return lines, None


def check_graph(root: Path, graph_path: Path) -> list[Offense]:
    """Return every code node whose claimed location is no longer true."""
    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    nodes = payload.get("nodes", [])
    if not isinstance(nodes, list):
        return []

    index: dict[str, tuple[list[str], set[str] | None] | None] = {}
    offenses: list[Offense] = []

    for node in nodes:
        if not isinstance(node, dict) or node.get("file_type") != "code":
            continue

        source_file = node.get("source_file")
        if not isinstance(source_file, str) or not source_file:
            continue

        location = str(node.get("source_location", ""))
        match = _LINE_RE.match(location)
        if match is None:
            continue

        path = Path(source_file)
        if not path.is_absolute():
            path = root / path

        if source_file not in index:
            index[source_file] = _source_index(path) if path.is_file() else None
        entry = index[source_file]
        if entry is None:
            offenses.append(
                Offense(source_file, str(node.get("label", "")), location, "source file no longer exists")
            )
            continue

        lines, symbols = entry
        start = int(match.group(1))
        if start > max(1, len(lines)):
            offenses.append(
                Offense(
                    source_file,
                    str(node.get("label", "")),
                    location,
                    f"line {start} is beyond the file's {len(lines)} lines",
                )
            )
            continue

        label = node.get("label")
        if symbols is None or not isinstance(label, str) or not label.endswith("()"):
            continue

        symbol = label[:-2].lstrip(".").rsplit(".", 1)[-1]
        if symbol and symbol not in symbols:
            offenses.append(
                Offense(
                    source_file,
                    label,
                    location,
                    f"symbol '{symbol}' no longer exists in the file",
                )
            )

    return offenses


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, default=None, help="path to graph.json")
    parser.add_argument("--verbose", action="store_true", help="report the scanned node count")
    args = parser.parse_args(argv)

    graph_path = args.graph if args.graph is not None else DEFAULT_GRAPH
    if not graph_path.is_file():
        print(f"SKIP: {graph_path} does not exist; no graph to check.")
        return 0

    offenses = check_graph(ROOT, graph_path)
    if not offenses:
        print("OK: graphify locations match the current source tree.")
        if args.verbose:
            print(f"Scanned {graph_path}.")
        return 0

    print(f"FAIL: {len(offenses)} graph node(s) point at stale source locations:")
    for offense in offenses:
        print(offense.render(ROOT))
    print("\nFix: re-run the code extractor with `graphify update . --force`.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
