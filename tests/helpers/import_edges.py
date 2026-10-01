"""Shared AST import-edge helper for wiring assertions.

Several tests assert on a module's IMPORT GRAPH rather than its source text:

* ``core -> modules`` edges (H-5 layering)
* composition-phase registration
* ARQ ``ARQ_FUNCTIONS`` / ``ARQ_CRONJOBS`` registration
* model-registration order

A source grep asserts that some text appears in a file, which any refactor can
break and which says nothing about whether the code works. An import edge
asserts "module A really imports module B" — a stable, behaviour-shaped
property that survives a rewrite of A's internals.

This module is the ONE AST walker for that, so a bug in it cannot silently
invalidate every assertion built on top. Its own unit test
(``test_ast_import_edges.py``) runs against synthetic module pairs, so the
helper is known-correct before any test trusts it.

Why the resolution detail matters: ``from pkg import submodule`` must record
the edge against ``pkg.submodule`` — the module Python actually loads — not
against ``pkg``. Getting that backwards is the classic way an import-edge
test starts passing for the wrong reason.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "ImportEdge",
    "dotted_module_name",
    "imports_in_source",
    "registered_names",
]


@dataclass(frozen=True, slots=True)
class ImportEdge:
    """One resolved import: module ``source`` loads ``target``."""

    source: str
    target: str

    def __iter__(self):
        """Allow ``(src, dst) in edges`` without unpacking the dataclass."""
        yield self.source
        yield self.target


def dotted_module_name(path: Path, root: Path) -> str:
    """``<root>/app/core/tenancy/context.py`` -> ``app.core.tenancy.context``.

    ``__init__.py`` collapses to its package, matching how Python names it.
    """
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_all(node: ast.AST, module: str | None) -> list[str]:
    """Every module name a single import node introduces.

    * ``import a.b.c`` -> ``["a.b.c"]``
    * ``from a import b, c`` -> ``["a.b", "a.c"]`` — a ``from pkg import
      submodule`` loads the submodule, so the edge belongs there.
    * ``from . import x`` resolves against the module's PACKAGE, and each
      extra dot climbs one level further out. In ``pkg.sub.mod``, a single
      dot yields ``pkg.sub.x`` and ``..`` yields ``pkg.x``.
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]

    if isinstance(node, ast.ImportFrom):
        base = node.module or ""
        if node.level and module:
            parts = module.split(".")
            keep = len(parts) - node.level
            base_parts = parts[:keep] if keep > 0 else []
            base = ".".join([*base_parts, base] if base else base_parts)
        if not node.names:
            return [base] if base else []
        if any(alias.name == "*" for alias in node.names):
            # A star import brings in the package, not a submodule of it.
            return [base] if base else []
        return [f"{base}.{alias.name}" if base else alias.name for alias in node.names]

    return []


def imports_in_source(source: str, module: str | None = None) -> set[str]:
    """Every module name imported by ``source``, fully qualified.

    ``module`` is the importing module's own dotted name, used to resolve
    relative imports (``from . import x``).
    """
    tree = ast.parse(source)
    out: set[str] = set()
    for node in ast.walk(tree):
        out.update(t for t in _resolve_all(node, module) if t)
    return out


def registered_names(path: Path, name: str) -> set[str]:
    """Names registered under a module-level constant, e.g. ``ARQ_FUNCTIONS``.

    Registration is a LITERAL dict/list/tuple assignment, not an import, so
    this reads the assignment rather than the import graph. It lives beside
    the import helpers so a test needing both shapes has one place to look.

    A dict contributes its KEYS; a list/tuple/set contributes its string and
    dotted-name elements.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in tree.body:
        targets: list[str] = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        if name not in targets or node.value is None:
            continue
        value = node.value
        if isinstance(value, ast.Dict):
            elements = [k for k in value.keys if k is not None]
        elif isinstance(value, ast.List | ast.Tuple | ast.Set):
            elements = list(value.elts)
        else:
            continue
        for element in elements:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                found.add(element.value)
            elif isinstance(element, ast.Name | ast.Attribute):
                found.add(ast.unparse(element))
    return found
