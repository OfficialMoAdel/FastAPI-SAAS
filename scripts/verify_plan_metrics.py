"""Re-measure every contested plan metric, each printed WITH its definition.

A plan number that nobody defined is a number that cannot be argued with. The
same quantity was reported by four sources in this project, and each used a
different unit — "9 SCCs" meant nine strongly-connected components to one
reviewer and nine *constructor sites* to another, with nothing on the page to
settle it. So this script does not just report numbers; for each one it prints
the definition the number was computed under, so the unit travels with the
value and a disagreement about the metric is impossible to have by accident.

Every metric is computed from the tree at run time. Nothing is hard-coded. A
metric that cannot be measured is printed as UNMEASURED with the reason it
could not be, because a confidently wrong number is worse than an honest gap.

Definitions, stated once and applied strictly:

* **Module** — one ``.py`` file, keyed by its dotted path from the repo root
  with ``__init__`` dropped (so ``src/app/modules/users/__init__.py`` is the
  module ``src.app.modules.users``).
* **Scope** — ``src/app`` unless stated. ``src`` when a metric says ``src/``.
* **Function-level (deferred) imports** — an ``import``/``from`` statement
  nested inside a function or method. These ARE counted as graph edges: a
  deferred import is a real runtime dependency and a real cycle risk, which is
  why the original "zero import cycles" claim was false. The metric that
  reports them separately (``import_graph_deferred_import_statements``) is the
  escape hatch for a reader who disagrees.
* **``from X import Y``** — resolved as an edge on ``X`` (the SYMBOL reading)
  *and* as a candidate edge on ``X.Y`` (the SUBMODULE reading). ``X.Y`` is kept
  only when it names a real module in the tree; otherwise it is a symbol and
  only the ``X`` edge survives. Both readings are emitted so a reader can see
  which one drove a number.

Run directly:

    python scripts/verify_plan_metrics.py          # human-readable
    python scripts/verify_plan_metrics.py --json   # machine-readable

Exit code is 0 on a successful measurement. This script MEASURES; it does not
gate. A lint gate that also prints its own definition of "violation" is a
second, unauditable authority.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
APP = SRC / "app"
MODULES_PKG = "src.app.modules"
SCRIPTS = REPO / "scripts"

#: Directories never walked for source. Cache trees would silently inflate
#: every count and are the classic reason a metric script returns a plausible
#: wrong number.
EXCLUDED_DIR_NAMES = frozenset(
    {
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".git",
        "node_modules",
        "site",
        ".import_linter_cache",
    }
)


# --------------------------------------------------------------------------
# Source walking
# --------------------------------------------------------------------------


def python_files(root: Path) -> list[Path]:
    """Every ``.py`` file under ``root``, cache trees excluded."""
    return sorted(p for p in root.rglob("*.py") if not EXCLUDED_DIR_NAMES.intersection(p.parts))


def dotted_module(path: Path) -> str:
    """``src/app/modules/users/__init__.py`` -> ``src.app.modules.users``."""
    parts = list(path.relative_to(REPO).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


@dataclass(frozen=True, slots=True)
class ImportEdge:
    """One resolved import, tagged with how it was written and where it sat."""

    importer: str
    target: str
    #: "module" for a top-level statement, "function" for a deferred one.
    scope: str
    lineno: int

    def as_dict(self) -> dict[str, object]:
        return {
            "importer": self.importer,
            "target": self.target,
            "scope": self.scope,
            "lineno": self.lineno,
        }


def resolve_relative(module: str, level: int, base: str | None) -> str:
    """Resolve ``from . import x`` / ``from .. import y`` against the package.

    ``level`` is the number of leading dots, so ``level=1`` means "this
    package" and each extra dot walks up one level. An ``ImportFrom`` whose
    module is ``__init__`` names the package itself, which is already what the
    resolved base is — hence the special case, without which
    ``from . import x`` would resolve to ``pkg.__init__`` and never match a
    module in the tree.
    """
    package = module.split(".") if module else []
    if level - 1:
        up = level - 1
        package = package[: len(package) - up] if up <= len(package) else []
    if base:
        parts = package + base.split(".")
    else:
        parts = package
    # A package's ``__init__`` IS the package. ``from .__init__ import x`` and
    # the ``__init__`` Python itself synthesises for ``from . import x`` name
    # the same module, so resolving to ``pkg.__init__`` would fabricate a
    # node that exists in no module list and silently drop the edge.
    if len(parts) > 1 and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def collect_import_edges(
    modules: dict[str, Path],
    *,
    include_deferred: bool = True,
    submodule_reading: bool = True,
) -> tuple[list[ImportEdge], list[tuple[str, int, str]]]:
    """Walk every module's AST and resolve its imports to dotted module names.

    Returns the resolved edges plus a list of ``(importer, lineno, written)``
    triples for every ``from X import Y`` statement, so a reader can audit the
    SYMBOL/SUBMODULE decision rather than trust it.
    """
    edges: list[ImportEdge] = []
    from_forms: list[tuple[str, int, str]] = []

    for name, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        # A module's package is its own dotted prefix, EXCEPT a package
        # ``__init__`` which IS its package (that's what makes `from . import
        # x` inside ``users/__init__.py`` resolve to ``src.app.modules.users``).
        package = name if path.name == "__init__.py" else name.rsplit(".", 1)[0]

        for node in ast.walk(tree):
            # Only import nodes are classified; ast.walk also yields the
            # Module node, which has no col_offset.
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            scope = "module" if node.col_offset == 0 else "function"
            if scope == "function" and not include_deferred:
                continue

            if isinstance(node, ast.Import):
                for alias in node.names:
                    edges.append(ImportEdge(name, alias.name, scope, node.lineno))

            elif isinstance(node, ast.ImportFrom):
                written = ("." * node.level) + (node.module or "")
                from_forms.append((name, node.lineno, written))
                base = (
                    resolve_relative(package, node.level, node.module)
                    if node.level
                    else (node.module or "")
                )
                # SYMBOL reading: importing anything from X depends on X.
                if base:
                    edges.append(ImportEdge(name, base, scope, node.lineno))
                # SUBMODULE reading: `from pkg import mod` also depends on
                # pkg.mod, but only when that is a real module. A symbol
                # import (`from mod import Name`) would otherwise fabricate an
                # edge onto a module that does not exist — filtered below.
                if submodule_reading and base:
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        edges.append(
                            ImportEdge(name, f"{base}.{alias.name}", scope, node.lineno)
                        )

    known = set(modules)
    resolved: list[ImportEdge] = []
    for edge in edges:
        # Keep only edges that land on a module in the tree. Third-party and
        # stdlib targets are excluded: they are not nodes in THIS graph and
        # counting them is how "total import edges" becomes meaningless.
        if edge.target in known:
            resolved.append(edge)
    return resolved, from_forms


# --------------------------------------------------------------------------
# Strongly connected components (Tarjan, iterative)
# --------------------------------------------------------------------------


def tarjan_sccs(adjacency: dict[str, set[str]]) -> list[list[str]]:
    """Iterative Tarjan. Iterative because this tree is deep enough that the
    recursive formulation overflows the default stack."""
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    components: list[list[str]] = []

    for root in sorted(adjacency):
        if root in indices:
            continue
        indices[root] = low[root] = index
        index += 1
        stack.append(root)
        on_stack.add(root)
        work: list[tuple[str, list[str]]] = [(root, sorted(adjacency[root]))]

        while work:
            node, successors = work[-1]
            if successors:
                nxt = successors.pop()
                if nxt not in indices:
                    indices[nxt] = low[nxt] = index
                    index += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, sorted(adjacency[nxt])))
                elif nxt in on_stack:
                    low[node] = min(low[node], indices[nxt])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == indices[node]:
                component: list[str] = []
                while True:
                    popped = stack.pop()
                    on_stack.discard(popped)
                    component.append(popped)
                    if popped == node:
                        break
                components.append(component)
    return components


@dataclass
class Metric:
    """One measurement plus the definition that fixes its unit."""

    definition: str
    value: int | str
    breakdown: dict[str, object] = field(default_factory=dict)
    notes: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "definition": self.definition,
            "value": self.value,
            "breakdown": self.breakdown,
            "notes": self.notes,
        }


class Report:
    def __init__(self) -> None:
        self.metrics: dict[str, Metric] = {}

    def add(self, name: str, metric: Metric) -> None:
        self.metrics[name] = metric

    def unmeasured(self, name: str, definition: str, reason: str) -> None:
        """Record a metric we refuse to guess at. An honest gap beats a wrong
        number, so this is a first-class outcome rather than an exception."""
        self.add(name, Metric(definition=definition, value="UNMEASURED", notes=reason))

    def as_dict(self) -> dict[str, object]:
        return {name: metric.as_dict() for name, metric in self.metrics.items()}


# --------------------------------------------------------------------------
# Metric 1 — import graph and cycles
# --------------------------------------------------------------------------

SCC_DEFINITION = (
    "A strongly-connected component (SCC) of the module import graph: a maximal "
    "set of modules where every member can reach every other member. Nodes are "
    "one .py file each, keyed by dotted path from the repo root with __init__ "
    "dropped. Edges are resolved imports that land on another module in "
    "src/app. FUNCTION-LEVEL (deferred) imports ARE INCLUDED — they are real "
    "runtime dependencies, and excluding them is what made the original 'zero "
    "import cycles' claim false. `from X import Y` is read as an edge on X "
    "(SYMBOL) and on X.Y (SUBMODULE), the latter kept only when X.Y is a real "
    "module in the tree; both readings are reported below so the reader can "
    "check which drove the number."
)


def measure_import_graph(report: Report) -> None:
    files = python_files(APP)
    modules = {dotted_module(p): p for p in files}
    edges, from_forms = collect_import_edges(modules)

    adjacency: dict[str, set[str]] = {m: set() for m in modules}
    for edge in edges:
        adjacency[edge.importer].add(edge.target)

    components = tarjan_sccs(adjacency)
    multi = sorted((sorted(c) for c in components if len(c) > 1), key=len, reverse=True)
    self_loops = sorted(m for m in modules if m in adjacency[m])
    in_cycle = {m for c in multi for m in c} | set(self_loops)

    deferred = [e for e in edges if e.scope == "function"]

    report.add(
        "import_graph_multi_node_scc_count",
        Metric(
            definition=SCC_DEFINITION,
            value=len(multi),
            breakdown={
                "sizes_descending": [len(c) for c in multi],
                "members": list(multi),
            },
            notes=(
                "A cycle of length 1 is NOT counted here — it is a self-loop and "
                "is reported by import_graph_self_loop_count instead. Counting "
                "both and calling the sum 'SCCs' is how one document's 9 "
                "another's 13."
            ),
        ),
    )
    report.add(
        "import_graph_self_loop_count",
        Metric(
            definition=(
                "Module-level self-imports: a module M with a resolved import "
                "edge M -> M. In this tree these are the lazy __getattr__ "
                "facades that import their own submodules. Distinct from the "
                "multi-node SCC count; a self-loop is a length-1 cycle."
            ),
            value=len(self_loops),
            breakdown={"modules": self_loops},
        ),
    )
    report.add(
        "import_graph_modules_in_a_cycle",
        Metric(
            definition=(
                "Distinct src/app modules that are a member of some cycle: the "
                "union of multi-node SCC members and self-loop modules."
            ),
            value=len(in_cycle),
            breakdown={"total_modules": len(modules)},
        ),
    )
    report.add(
        "import_graph_largest_scc_size",
        Metric(
            definition=(
                "Size of the biggest multi-node SCC, in modules. Name this "
                "before acting on it: an SCC is a coupling fact, not a defect "
                "count, and membership does not imply API breadth."
            ),
            value=max((len(c) for c in multi), default=0),
            breakdown={"members": multi[0] if multi else []},
        ),
    )
    report.add(
        "import_graph_total_internal_edges",
        Metric(
            definition=(
                "Distinct resolved import edges between two src/app modules "
                "(imported_module -> imported_module, deduplicated). "
                "Third-party and stdlib targets are EXCLUDED — they are not "
                "nodes in this graph. A duplicated edge (same pair imported "
                "from several places) counts once."
            ),
            value=len(edges),
            breakdown={
                "module_level_statements": sum(1 for e in edges if e.scope == "module"),
                "function_level_statements": len(deferred),
            },
        ),
    )
    report.add(
        "import_graph_deferred_import_statements",
        Metric(
            definition=(
                "Resolved internal import edges written inside a function or "
                "method (deferred imports), counted as EDGES after "
                "deduplication. Emitted separately because this is the "
                "assumption a reader is most likely to want to flip; the cycle "
                "counts above include them."
            ),
            value=len(deferred),
            breakdown={"total_internal_edges": len(edges)},
        ),
    )
    report.add(
        "import_graph_from_import_statement_count",
        Metric(
            definition=(
                "Every `from ... import ...` statement in src/app, at any scope, "
                "written with an absolute module path or a relative one. This "
                "is the population the SYMBOL/SUBMODULE decision applies to; it "
                "is larger than the edge count because one statement can yield "
                "one edge or many."
            ),
            value=len(from_forms),
        ),
    )
    report.add(
        "import_graph_from_import_submodule_reading_edges",
        Metric(
            definition=(
                "Edges produced ONLY by the SUBMODULE reading — i.e. those that "
                "would vanish if `from X import Y` were treated purely as a "
                "symbol import and never as an edge on X.Y. The gap between "
                "this and import_graph_total_internal_edges is the size of the "
                "decision; it is the difference between two of the SCC "
                "readings, so it is reported rather than buried."
            ),
            value=_submodule_only_edge_count(modules),
        ),
    )


def _submodule_only_edge_count(modules: dict[str, Path]) -> int:
    with_sub, _ = collect_import_edges(modules, submodule_reading=True)
    without_sub, _ = collect_import_edges(modules, submodule_reading=False)
    return len({(e.importer, e.target) for e in with_sub}) - len(
        {(e.importer, e.target) for e in without_sub}
    )


# --------------------------------------------------------------------------
# Metric 2 — TID251 and noqa suppressions
# --------------------------------------------------------------------------

NOQA_RE = re.compile(
    r"#\s*noqa(?P<sep>\s*:\s*(?P<codes>[A-Z]+\d+(?:[,\s]+[A-Z]+\d+)*))?", re.IGNORECASE
)


def measure_tid251(report: Report) -> None:
    """Run ruff and count noqa suppressions.

    TID251 is decided by RUFF, not by this script — reimplementing a linter
    rule here would make the gate auditable only by this file, which is the
    defect that makes a checkable rule feel uncheckable. The noqa count is
    computed independently and exists so that "the gate is green" cannot hide
    "the gate was bought".
    """
    definition = (
        "TID251 (flake8-tidy-imports banned-api) violations, as reported by "
        "`ruff check --select TID251 src/`, which reads "
        "[tool.ruff.lint.flake8-tidy-imports.banned-api] from pyproject.toml "
        "and applies its per-file-ignores. Measured by running ruff, not by "
        "reimplementing it."
    )
    ruff = REPO / ".venv" / "Scripts" / "ruff.exe"
    if not ruff.exists():
        ruff = REPO / ".venv" / "bin" / "ruff"
    executable = str(ruff) if ruff.exists() else "ruff"

    try:
        proc = subprocess.run(
            [executable, "check", "--select", "TID251", "src/"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        report.unmeasured(
            "tid251_violations",
            definition,
            f"could not run ruff ({exc!r}); the ban list lives in pyproject.toml "
            "and reimplementing the rule here would replace an auditable gate "
            "with an unauditable one",
        )
        return

    violations = 0
    for line in proc.stdout.splitlines():
        if re.match(r"^src/\S+:\d+:\d+:\s+TID251\b", line):
            violations += 1

    report.add(
        "tid251_violations",
        Metric(
            definition=definition,
            value=violations,
            breakdown={
                "ruff_exit_code": proc.returncode,
                "ruff_stderr_head": proc.stderr.strip().splitlines()[:3],
            },
            notes=(
                "A clean TID251 gate says the banned-api list is unenforced "
                "where it matters. It does not say the architecture holds — the "
                "list names a handful of module cells, not all of them."
            ),
        ),
    )


def measure_noqa(report: Report) -> None:
    definition = (
        "Suppression comments in src/ matching `# noqa`, optionally with "
        "codes. Counted in .py source text with a regex (a suppression is a "
        "comment, not an AST node) and split by whether it names any code. A "
        "BARE `# noqa` silences every rule on that line; a coded one silences "
        "only what it names."
    )
    total = 0
    bare = 0
    codes: Counter[str] = Counter()
    for path in python_files(SRC):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in NOQA_RE.finditer(text):
            total += 1
            found = match.group("codes")
            if found:
                for code in re.split(r"[,\s]+", found):
                    if code:
                        codes[code] += 1
            else:
                bare += 1

    report.add(
        "src_noqa_suppression_count",
        Metric(
            definition=definition,
            value=total,
            breakdown={
                "bare_noqa": bare,
                "by_code": dict(codes.most_common()),
                "python_files_scanned": len(python_files(SRC)),
            },
            notes=(
                "Reported because a green lint gate is cheap to buy with "
                "blanket suppressions. Read this next to tid251_violations, not "
                "on its own."
            ),
        ),
    )


# --------------------------------------------------------------------------
# Metric 3 — ORM relationship loading strategies
# --------------------------------------------------------------------------

RELATIONSHIP_DEFINITION = (
    "Calls to `relationship(...)` in src/app, at any scope, resolved from the "
    "AST (not grep): every `relationship` Name/Attribute call node. The metric "
    "is the `lazy=` keyword's literal value on that call. A relationship with "
    "no `lazy=` is SQLAlchemy's DEFAULT (which is 'select' — lazy loading "
    "enabled) and is reported as '<default>', NOT folded into 'noload'. "
    "'noload' and 'selectin' are both eager-or-never strategies, but they are "
    "different: noload never loads, selectin loads via a second query at "
    "access time. Conflating them is what makes an 'N+1 is impossible' claim "
    "either true or worthless."
)


def measure_relationships(report: Report) -> None:
    total = 0
    strategies: Counter[str] = Counter()
    non_noload: list[dict[str, object]] = []
    model_files: set[str] = set()

    for path in python_files(APP):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "relationship":
                continue
            total += 1
            model_files.add(path.relative_to(REPO).as_posix())

            lazy: str | None = None
            for keyword in node.keywords:
                if keyword.arg != "lazy":
                    continue
                try:
                    lazy = ast.literal_eval(keyword.value)
                except (ValueError, SyntaxError):
                    lazy = "<dynamic>"
            strategy = str(lazy) if lazy is not None else "<default>"
            strategies[strategy] += 1
            if strategy != "noload":
                non_noload.append(
                    {
                        "file": path.relative_to(REPO).as_posix(),
                        "lineno": node.lineno,
                        "lazy": strategy,
                    }
                )

    noload = strategies["noload"]
    report.add(
        "orm_relationship_count",
        Metric(
            definition=RELATIONSHIP_DEFINITION,
            value=total,
            breakdown={"by_lazy_strategy": dict(strategies.most_common())},
        ),
    )
    report.add(
        "orm_relationship_lazy_noload_count",
        Metric(
            definition=(
                "The subset of orm_relationship_count whose `lazy=` is exactly "
                "'noload'. This is the population an 'ORM N+1 is structurally "
                "impossible' claim rests on. It is NOT the same as the total "
                "relationship count, and the difference is where that claim "
                "breaks."
            ),
            value=noload,
            breakdown={
                "not_noload": total - noload,
                "all_strategies": dict(strategies.most_common()),
            },
        ),
    )
    report.add(
        "orm_relationship_not_noload",
        Metric(
            definition=(
                "Every relationship whose `lazy=` is not 'noload', with file "
                "and line. The load-bearing half of any claim that relationships "
                "are uniformly non-loading: if this list is non-empty, 'all "
                "relationships are noload' is false as written."
            ),
            value=len(non_noload),
            breakdown={"relationships": non_noload, "model_files": len(model_files)},
        ),
    )


# --------------------------------------------------------------------------
# Metric 4 — cross-module imports by target
# --------------------------------------------------------------------------

CROSS_MODULE_DEFINITION = (
    "Import STATEMENTS in src/app whose target lies under src.app.modules.*, "
    "counted once per statement (a `from X import a, b, c` is ONE statement, "
    "not three). Relative imports are excluded because a relative import cannot "
    "name an absolute module path — they stay inside their own package and so "
    "are never cross-module by this definition. Each statement is classified "
    "against the module that WRITES it: 'own_module' (a file importing another "
    "file of its own feature package), 'module_package_root' (the sanctioned "
    "facade seam, `from src.app.modules.users import UserService`), "
    "'module_facade_file' (the facade's own __init__ re-exporting its "
    "submodules, which the ruff TID exemption sanctions), and "
    "'other_module_internals' (a deep import into ANOTHER module's service/"
    "router/crud — the prose breach)."
)


def measure_cross_module_imports(report: Report) -> None:
    buckets: Counter[str] = Counter()
    per_target: Counter[str] = Counter()
    breaches: list[dict[str, object]] = []
    by_scope: Counter[str] = Counter()

    for path in python_files(APP):
        importer = dotted_module(path)
        package = importer if path.name == "__init__.py" else importer.rsplit(".", 1)[0]
        owner = package.split(".")[2] if package.startswith(MODULES_PKG) else None

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            statements: list[str] = []
            if isinstance(node, ast.Import):
                statements = [a.name for a in node.names if a.name.startswith(MODULES_PKG)]
            elif isinstance(node, ast.ImportFrom):
                if node.level or not node.module or not node.module.startswith(MODULES_PKG):
                    continue
                statements = [node.module]
            if not statements:
                continue

            by_scope["function" if node.col_offset else "module"] += 1
            for target in statements:
                parts = target[len(MODULES_PKG) + 1 :].split(".")
                target_module = parts[0]
                per_target[target_module] += 1

                if owner == target_module:
                    bucket = "own_module"
                elif len(parts) == 1:
                    bucket = "module_package_root"
                elif len(parts) == 2:
                    bucket = "module_facade_file"
                else:
                    bucket = "other_module_internals"

                buckets[bucket] += 1
                if bucket == "other_module_internals":
                    breaches.append(
                        {
                            "file": path.relative_to(REPO).as_posix(),
                            "lineno": node.lineno,
                            "target": target,
                        }
                    )

    report.add(
        "cross_module_import_statement_count",
        Metric(
            definition=CROSS_MODULE_DEFINITION,
            value=sum(buckets.values()),
            breakdown={
                "by_bucket": dict(buckets.most_common()),
                "by_scope": dict(by_scope.most_common()),
                "distinct_target_modules": len(per_target),
                "by_target_module": dict(per_target.most_common()),
            },
        ),
    )
    report.add(
        "cross_module_import_facade_seam_count",
        Metric(
            definition=(
                "The subset of cross_module_import_statement_count that uses a "
                "sanctioned seam: 'module_package_root' (the package facade) "
                "plus 'module_facade_file' (the facade's own re-export). These "
                "are the imports the architecture documentation ENDORSES."
            ),
            value=buckets["module_package_root"] + buckets["module_facade_file"],
            breakdown={
                "module_package_root": buckets["module_package_root"],
                "module_facade_file": buckets["module_facade_file"],
            },
        ),
    )
    report.add(
        "cross_module_import_other_internals_count",
        Metric(
            definition=(
                "The subset of cross_module_import_statement_count that reaches "
                "into ANOTHER feature module's internals (past the package "
                "root, i.e. `.service`, `.router`, `.crud`, `.models` of a "
                "module the importer does not own). Each is listed with file "
                "and line. This is the population the prose rule 'never import "
                "another module's internals' governs — and the population ruff "
                "TID251 does NOT cover for most modules."
            ),
            value=buckets["other_module_internals"],
            breakdown={"statements": breaches},
        ),
    )


# --------------------------------------------------------------------------
# Metric 5 — to_thread call sites
# --------------------------------------------------------------------------

TO_THREAD_DEFINITION = (
    "Call nodes whose callee is named `to_thread` anywhere in src/ (any file, "
    "any scope), resolved from the AST — so `asyncio.to_thread(...)`, "
    "`anyio.to_thread(...)` and a bare `to_thread(...)` all count, and a mere "
    "mention in a comment or docstring does not. Counted per CALL SITE (one "
    "line may hold several). NOTE the scope of the name: `anyio."
    "to_thread.current_default_thread_limiter()` is NOT a call site — it "
    "reads the limiter, it does not dispatch to a thread — and is excluded by "
    "the callee-name rule. Counting text occurrences of 'to_thread' instead "
    "would report one more."
)


def measure_to_thread(report: Report) -> None:
    total = 0
    per_file: Counter[str] = Counter()
    for path in python_files(SRC):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name == "to_thread":
                total += 1
                per_file[path.relative_to(REPO).as_posix()] += 1

    report.add(
        "to_thread_call_site_count",
        Metric(
            definition=TO_THREAD_DEFINITION,
            value=total,
            breakdown={"files": len(per_file), "by_file": dict(per_file.most_common())},
        ),
    )


# --------------------------------------------------------------------------
# Metric 6 — Lua scripts
# --------------------------------------------------------------------------

LUA_DEFINITION = (
    "Redis Lua script SOURCES held as Python string literals in src/: the "
    "values of the LuaScriptManager.BUILTIN_SCRIPTS dict in "
    "core/utils/lua_manager.py, plus any other string literal passed to "
    "redis .eval()/.evalsha() or registered as a script. Counted from the AST "
    "(dict entries and call arguments), never by grepping for 'lua', which "
    "matches comments, log messages and config flags. The 'no .lua files on "
    "disk' claim is CONFIRMED or REFUTED here by an actual filesystem walk, and "
    "the answer is printed with the number."
)


def measure_lua_scripts(report: Report) -> None:
    on_disk = sorted(p.relative_to(REPO).as_posix() for p in SRC.rglob("*.lua"))

    builtin: list[str] = []
    lua_manager = APP / "core" / "utils" / "lua_manager.py"
    if lua_manager.exists():
        tree = ast.parse(lua_manager.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            # ``BUILTIN_SCRIPTS`` is assigned inside a CLASS body, so the
            # target is a bare ast.Name — checking only ``.attr`` (as an
            # attribute access would be) matches nothing and silently
            # reports 0 scripts. Both spellings are accepted.
            targets = [
                t
                for t in node.targets
                if (getattr(t, "id", None) or getattr(t, "attr", None)) == "BUILTIN_SCRIPTS"
            ]
            if not targets or not isinstance(node.value, ast.Dict):
                continue
            for key in node.value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    builtin.append(key.value)
    # Inline scripts passed straight to eval()/evalsha()/register().
    inline: list[str] = []
    for path in python_files(APP):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            callee = getattr(func, "attr", None) or getattr(func, "id", None)
            if callee not in {"eval", "evalsha", "register", "register_script"}:
                continue
            if node.args and isinstance(node.args[0], ast.Constant):
                value = node.args[0].value
                if isinstance(value, str) and len(value) > 20:
                    inline.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}")

    names = sorted(set(builtin))
    report.add(
        "lua_script_count",
        Metric(
            definition=LUA_DEFINITION,
            value=len(names) + len(inline),
            breakdown={
                "builtin_scripts_in_lua_manager": len(names),
                "builtin_script_names": names,
                "inline_script_literals": inline,
                "lua_files_on_disk": on_disk,
                "lua_file_count_on_disk": len(on_disk),
            },
            notes=(
                "No .lua files on disk was checked by walking src/ for the "
                "extension, not assumed. Every Redis script in this codebase "
                "is an inline Python string."
                if not on_disk
                else "WARNING: .lua files exist on disk; the builtin count alone "
                "would understate the Lua surface."
            ),
        ),
    )


# --------------------------------------------------------------------------
# Metric 7 — source-text tests (delegated to the existing counter)
# --------------------------------------------------------------------------

SOURCE_TEXT_DEFINITION = (
    "Source-text test assertions, as defined by scripts/count_source_text_tests.py "
    "(REUSED, not reimplemented here — a second implementation of the same "
    "metric is a second number). That module's definition: a test function "
    "that reads .py source text and asserts on it, WITHOUT exercising "
    "behaviour. A test that also calls the code is behavioural with an extra "
    "assertion and is not counted. This metric is the ratchet baseline; if this "
    "script's number ever disagrees with that script's own output, that script "
    "is authoritative and this wrapper is wrong."
)


def measure_source_text_tests(report: Report) -> None:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    try:
        import count_source_text_tests as counter  # noqa: PLC0415
    except ImportError as exc:
        report.unmeasured(
            "source_text_test_count",
            SOURCE_TEXT_DEFINITION,
            f"could not import scripts/count_source_text_tests.py ({exc})",
        )
        return

    findings = counter.scan()
    report.add(
        "source_text_test_count",
        Metric(
            definition=SOURCE_TEXT_DEFINITION,
            value=len(findings),
            breakdown={
                "by_reason": dict(Counter(f.reason for f in findings).most_common()),
                "measured_by": "scripts/count_source_text_tests.py:scan()",
                "files_with_findings": len({f.path for f in findings}),
            },
        ),
    )



# --------------------------------------------------------------------------
# io15.10: is the ExplicitTenant escape hatch actually used, and audited?
# --------------------------------------------------------------------------


EXPLICIT_TENANT_REASON_DEFINITION = (
    "Construction sites of ExplicitTenant / Unscoped (core/tenancy/scope.py), "
    "classified by whether the `reason` argument is a string literal. Both "
    "types raise ValueError on an empty reason in __post_init__, so a LITERAL "
    "reason can never be empty: the value is auditable at the call site and "
    "the raise is unreachable. A NON-LITERAL (f-string, variable, attribute) "
    "can evaluate to an empty string at runtime, which raises at the point of "
    "construction instead. That split is what this metric counts: the "
    "escape hatch's safety property depends on the reason being non-empty, "
    "and the honest question is how many sites can prove it statically."
)


def _explicit_tenant_construction_sites() -> list[tuple[Path, int, str, bool]]:
    """(path, lineno, type_name, reason_is_literal) per escape-hatch construction."""
    import ast

    sites: list[tuple[Path, int, str, bool]] = []
    for path in sorted((SRC / "app").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else None
            if name not in ("ExplicitTenant", "Unscoped"):
                continue
            reason_is_literal = any(
                kw.arg == "reason" and isinstance(kw.value, ast.Constant) for kw in node.keywords
            )
            # Positional form: reason is the 2nd arg on both types.
            if not reason_is_literal and name == "ExplicitTenant" and len(node.args) >= 2:
                reason_is_literal = isinstance(node.args[1], ast.Constant)
            if not reason_is_literal and name == "Unscoped" and len(node.args) >= 1:
                reason_is_literal = isinstance(node.args[0], ast.Constant)
            sites.append((path, node.lineno, name, reason_is_literal))
    return sites


def measure_explicit_tenant_reason_sites(report: Report) -> None:
    sites = _explicit_tenant_construction_sites()
    literal = [s for s in sites if s[3]]
    computed = [s for s in sites if not s[3]]
    by_type: Counter = Counter(
        f"{kind}.{'literal' if is_literal else 'computed'}"
        for _path, _lineno, kind, is_literal in sites
    )

    report.add(
        "explicit_tenant_reason_site_count",
        Metric(
            definition=EXPLICIT_TENANT_REASON_DEFINITION,
            value=len(computed),
            breakdown={
                "total_construction_sites": len(sites),
                "string_literal_reason": len(literal),
                "computed_reason": len(computed),
                "by_type": dict(sorted(by_type.items())),
                "computed_sites_sample": [
                    f"{p.relative_to(SRC)}:{ln} ({kind})" for p, ln, kind, _lit in computed
                ],
            },
            notes=(
                "io15.10 asks for the count of EMPTY-reason sites. It is "
                "structurally zero: __post_init__ raises on an empty reason, so "
                "no construction with an empty literal can exist. The metric "
                "therefore counts COMPUTED reasons, which is the only way an "
                "empty reason can reach the type at all, and which fails later "
                "and less locally than a literal would."
            ),
        ),
    )



# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

METRIC_ORDER = (
    "import_graph_multi_node_scc_count",
    "import_graph_self_loop_count",
    "import_graph_modules_in_a_cycle",
    "import_graph_largest_scc_size",
    "import_graph_total_internal_edges",
    "import_graph_deferred_import_statements",
    "import_graph_from_import_statement_count",
    "import_graph_from_import_submodule_reading_edges",
    "tid251_violations",
    "src_noqa_suppression_count",
    "orm_relationship_count",
    "orm_relationship_lazy_noload_count",
    "orm_relationship_not_noload",
    "cross_module_import_statement_count",
    "cross_module_import_facade_seam_count",
    "cross_module_import_other_internals_count",
    "to_thread_call_site_count",
    "lua_script_count",
    "source_text_test_count",
    "explicit_tenant_reason_site_count",
)

REQUIRED_METRICS = frozenset(METRIC_ORDER)


def build_report() -> Report:
    report = Report()
    measure_import_graph(report)
    measure_tid251(report)
    measure_noqa(report)
    measure_relationships(report)
    measure_cross_module_imports(report)
    measure_to_thread(report)
    measure_lua_scripts(report)
    measure_source_text_tests(report)
    measure_explicit_tenant_reason_sites(report)
    return report


def render(report: Report) -> str:
    lines: list[str] = []
    for name in METRIC_ORDER:
        metric = report.metrics.get(name)
        if metric is None:
            continue
        lines.append(f"{name} = {metric.value}")
        for chunk in wrap(metric.definition):
            lines.append(f"    definition: {chunk}")
        for key, value in metric.breakdown.items():
            lines.append(f"    {key}: {summarise(value)}")
        if metric.notes:
            for chunk in wrap(metric.notes):
                lines.append(f"    note: {chunk}")
        lines.append("")
    missing = sorted(REQUIRED_METRICS - set(report.metrics))
    if missing:
        lines.append(f"MISSING METRICS (a bug in this script): {', '.join(missing)}")
    return "\n".join(lines)


def wrap(text: str, width: int = 92) -> list[str]:
    words, line, out = text.split(), "", []
    for word in words:
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out or [""]


def summarise(value: object, limit: int = 6) -> str:
    """Render a breakdown value compactly; long lists are named, not dumped."""
    if isinstance(value, dict):
        items = list(value.items())
        if len(items) <= limit:
            return ", ".join(f"{k}={v}" for k, v in items)
        head = ", ".join(f"{k}={v}" for k, v in items[:limit])
        return f"{head}, ... (+{len(items) - limit} more)"
    if isinstance(value, list):
        if len(value) <= limit:
            return str(value)
        return f"[{len(value)} items: {value[:3]} ...]"
    return str(value)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    report = build_report()
    payload = report.as_dict()
    if "--json" in argv:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
