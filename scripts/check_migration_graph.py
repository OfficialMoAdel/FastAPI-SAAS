"""CI check: the Alembic revision graph is a single, linear, base-reaching chain.

A forked migration history is a deploy-time failure, not a test-time one.
`alembic upgrade head` against a database with two heads raises
``Multiple head revisions are present`` — and the app never boots, so the
failure lands on whoever is deploying during an incident. A branch that
never merges back is worse still: it is a *permanent* fork, invisible until
someone runs the upgrade.

The two properties asserted here are STATIC. They are computable from the
revision files alone, with no database and no ``alembic upgrade``, which is
what makes them cheap enough to run on every push:

  1. SINGLE HEAD — exactly one revision has no other revision naming it as
     its ``down_revision``. Zero heads means the history was emptied; more
     than one head means it forked.
  2. LINEARITY — every revision is reachable from the head by following
     ``down_revision`` to ``None``, and no revision is reached twice. This
     catches a cycle, a dangling ``down_revision`` pointing at a revision
     that does not exist, and a merge revision (two children pointing at one
     parent) that the single-head check alone would let through, because
     ``get_heads()`` reports one head for a merge.

This checker parses the revision identifiers with ``ast`` rather than
importing the migration modules. Importing them would execute
``from alembic import op`` at module scope and, more importantly, a
migration file is not guaranteed to be importable outside an Alembic run —
``op.execute`` at module level is a legal thing to write and would raise
outside a migration context. The identifiers are module-level string
assignments, so the AST is the right tool and is also the cheapest.

The import-resolution half of the gate lives in
``tests/unit/scripts/test_recovery_script_imports.py``: it checks that the
script's ``from X import Y`` lines actually resolve, which is a different
property (name resolution, not graph shape) and needs the real import
machinery rather than parsing.

Usage:
    uv run python scripts/check_migration_graph.py

Exit codes:
    0 - the graph is a single linear chain reaching base
    1 - the graph forked, is not linear, or does not reach base
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSIONS_DIR = ROOT / "src" / "migrations" / "versions"

# Module-level assignments that name a graph node. Alembic reads these four;
# `branch_labels` and `depends_on` are captured because a branch label is the
# documented way to declare an intentional fork, so its presence is what makes
# a second head explicable rather than accidental.
_REVISION_FIELDS = ("revision", "down_revision", "branch_labels", "depends_on")


class RevisionFileError(Exception):
    """A file in versions/ is not a parseable Alembic revision."""


def parse_revision(path: Path) -> dict[str, object]:
    """Return {field: value} for one revision file, without importing it.

    Raises RevisionFileError if the file is not Python, or is Python but
    declares no `revision`. A missing `down_revision` is NOT an error: it
    means "reaches base", which is the property under test, not a defect.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        raise RevisionFileError(f"{path.name}: not valid Python — {exc}") from exc

    found: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        # `revision: str = "x"` (AnnAssign) and `revision = "x"` (Assign)
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        for target in targets:
            if not isinstance(target, ast.Name) or target.id not in _REVISION_FIELDS:
                continue
            if target.id in found:
                raise RevisionFileError(f"{path.name}: `{target.id}` assigned twice")
            # `ast.literal_eval` is safe here (no arbitrary execution) and
            # returns None for the `...`-style annotations Alembic templates
            # emit, which is the right answer for an undeclared field.
            try:
                found[target.id] = ast.literal_eval(value) if value is not None else None
            except (ValueError, TypeError):
                found[target.id] = None

    if "revision" not in found:
        raise RevisionFileError(f"{path.name}: declares no `revision` — not an Alembic revision file")
    return found


def load_graph(versions_dir: Path) -> dict[str, dict[str, object]]:
    """Return {revision_id: parsed_file} for every revision in versions_dir.

    `__init__.py` is skipped: it is a package marker, not a revision. A
    duplicate revision id across two files is a hard error — Alembic resolves
    the directory into a map keyed by revision id, so the second file would
    silently shadow the first and the graph would be untrustworthy.
    """
    graph: dict[str, dict[str, object]] = {}
    origin: dict[str, str] = {}
    for path in sorted(versions_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        parsed = parse_revision(path)
        rev = parsed["revision"]
        if not isinstance(rev, str) or not rev:
            raise RevisionFileError(f"{path.name}: `revision` is not a non-empty string: {rev!r}")
        if rev in graph:
            raise RevisionFileError(f"duplicate revision id {rev!r} in {path.name} and {origin[rev]}")
        origin[rev] = path.name
        parsed["_path"] = path.name
        graph[rev] = parsed
    return graph


def parents_of(node: dict[str, object]) -> list[str]:
    """Return the revision's parents, normalising Alembic's two encodings.

    Alembic allows `down_revision` to be a single id, a tuple of ids (the
    documented way to write a MERGE revision), or a `str | Sequence[str]`
    annotation. A tuple means the revision has more than one parent, which
    is precisely the branch-join construct this gate rejects: the repo's
    AGENTS.md says history is "8 named origin layers, not date-stamped
    revisions", so a join would mean the layers were split and re-converged.
    """
    raw = node.get("down_revision")
    if raw is None or (isinstance(raw, str) and not raw):
        return []
    if isinstance(raw, tuple | list):
        return [r for r in raw if isinstance(r, str) and r]
    if isinstance(raw, str):
        return [raw]
    return []


def find_heads(graph: dict[str, dict[str, object]]) -> list[str]:
    """Revisions that no other revision names as a parent."""
    referenced: set[str] = set()
    for node in graph.values():
        referenced.update(parents_of(node))
    return [r for r in graph if r not in referenced]


def find_merges(graph: dict[str, dict[str, object]]) -> list[tuple[str, list[str]]]:
    """Revisions declaring more than one parent, with their parent list.

    A merge produced by `alembic merge` is the shape a human creates to
    *resolve* a fork. It leaves exactly one head, so the single-head check
    passes it — yet it means two branches were applied to real databases,
    and the merge revision's own upgrade() is usually an empty no-op. This
    repo has no merges, and adding one silently re-introduces the history
    split the layering was meant to prevent.
    """
    out = []
    for rev, node in graph.items():
        parents = parents_of(node)
        if len(parents) > 1:
            out.append((rev, parents))
    return sorted(out)


def check(versions_dir: Path = VERSIONS_DIR) -> list[str]:
    """Return a list of violations (empty means the graph is sound)."""
    if not versions_dir.is_dir():
        return [f"missing versions directory: {versions_dir}"]

    try:
        graph = load_graph(versions_dir)
    except RevisionFileError as exc:
        return [str(exc)]

    if not graph:
        return [f"no revisions found in {versions_dir}"]

    problems: list[str] = []

    heads = find_heads(graph)
    if len(heads) == 0:
        # Every revision is somebody's parent: a cycle, or a graph that
        # references revisions defined nowhere in versions/.
        problems.append(
            "no head found — every revision is the parent of another "
            "(a cycle, or a reference to a revision that does not exist)"
        )
        return problems
    if len(heads) > 1:
        problems.append(
            f"{len(heads)} heads ({', '.join(sorted(heads))}) — the history has forked. "
            "A database holding the pre-fork revision fails `alembic upgrade head` "
            "with 'Multiple head revisions are present'."
        )

    for rev, parents in find_merges(graph):
        problems.append(
            f"revision {rev!r} is a merge with parents {sorted(parents)} — "
            "this history is layered, not branched, so a join means the layers "
            "were split and re-converged. Note a merge still leaves ONE head, "
            "so the head count alone does not catch it."
        )

    # Linearity: walk from each head down to base, following every parent.
    # A revision reached by two different paths is a branch point; a node
    # revisited inside one path is a cycle.
    reachable: set[str] = set()
    for head in heads:
        seen: set[str] = set()
        stack: list[str | None] = [head]
        while stack:
            node = stack.pop()
            if node is None:
                continue
            if node in seen:
                problems.append(
                    f"cycle in revision graph reached from head {head!r} at {node!r}"
                )
                break
            seen.add(node)
            if node not in graph:
                problems.append(
                    f"revision {node!r} is referenced as a down_revision but is not "
                    f"defined in {versions_dir.name}/ — an `alembic upgrade` would fail on it"
                )
                break
            reachable.add(node)
            stack.extend(parents_of(graph[node]))

    orphans = sorted(set(graph) - reachable)
    if orphans:
        problems.append(
            f"revisions unreachable from any head: {', '.join(orphans)} — "
            "they form a component that no `alembic upgrade` would ever apply"
        )

    return problems



def main() -> int:
    problems = check()
    if problems:
        print("FAIL: the Alembic revision graph is not a single linear chain:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(
            "\nSee src/migrations/AGENTS.md: new schema changes extend a named "
            "layer's upgrade()/downgrade() rather than stacking a new revision.",
            file=sys.stderr,
        )
        return 1
    graph = load_graph(VERSIONS_DIR)
    print(f"OK: {len(graph)} revisions, single head {find_heads(graph)[0]!r}, linear to base.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
