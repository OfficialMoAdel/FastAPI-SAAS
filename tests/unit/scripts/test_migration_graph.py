"""The Alembic revision graph must stay a single linear chain — checked without a database.

A forked migration history is a DEPLOY-time failure, not a test-time one.
`alembic upgrade head` against a database holding the pre-fork revision
raises ``Multiple head revisions are present``, and the app never boots, so
the failure lands on whoever happens to be deploying during an incident.

The two properties asserted here are static: they are computable from the
revision files alone, with no database and no ``alembic upgrade``. That is
what makes them cheap enough to belong in CI on every push, and it is why
they are worth pinning even though this repository's suite is wired to
SQLite (see ``tests/unit/core/db/test_rls_policy_coherence.py`` for why no
PostgreSQL-backed suite runs here at all).

``scripts/check_migration_graph.py`` is the CI entry point; this module pins
both that it is wired and that it actually blocks.

The checker parses revision identifiers with ``ast`` rather than importing
the migration modules. ``tests/unit/core/db/test_rls_policy_coherence.py``
does import migration 08 directly, and that is sound there because the
module's only work is defining ``TENANT_TABLES``. A migration is not
required to be import-safe in general — ``op.execute(...)`` at module scope
is legal Alembic and raises outside a migration context — so the graph
walker stays purely static.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github/workflows/tests.yml"
CHECKER = REPO / "scripts/check_migration_graph.py"
VERSIONS_DIR = REPO / "src/migrations/versions"

# The ``on:`` trigger block. Restricting to it keeps a ``paths:`` filter
# nested inside a job from being mistaken for a trigger filter.
_ON_BLOCK_RE = re.compile(r"^on:\n(?P<body>(?:[ \t]+.*\n|[ \t]*\n)*)", re.M)
_TOP_LEVEL_KEY_RE = re.compile(r"^[A-Za-z_-]+:", re.M)


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_migration_graph", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def checker():
    return _load_checker()


@pytest.fixture(scope="module")
def real_graph(checker):
    """The graph the checker reads from the real repository."""
    return checker.load_graph(VERSIONS_DIR)


class TestRealGraphIsSound:
    """The properties, asserted against the repository as it actually is.

    These are the assertions CI ultimately exists to protect. They are cheap
    enough to run here precisely because they need no database.
    """

    def test_exactly_one_head(self, checker, real_graph) -> None:
        """THE BINDING PROPERTY.

        Two heads means `alembic upgrade head` fails outright on any
        database that is not already past the fork. Zero heads means the
        history was emptied into a cycle or fully-orphaned, which fails
        just as hard and is quieter.
        """
        heads = checker.find_heads(real_graph)
        assert len(heads) == 1, (
            f"expected exactly 1 Alembic head, found {len(heads)}: {sorted(heads)}. "
            "Multiple heads = forked history = `alembic upgrade head` raises "
            "'Multiple head revisions are present'."
        )

    def test_no_violations_reported_for_the_real_graph(self, checker) -> None:
        """The checker's full verdict on the real tree must be clean.

        Broader than the head count on purpose: it also covers a dangling
        down_revision, a cycle, and a merge revision — the three ways a
        graph can be broken while `find_heads` still returns one element.
        """
        assert checker.check(VERSIONS_DIR) == []

    def test_every_revision_reaches_base(self, checker, real_graph) -> None:
        """No revision may sit in a component no `alembic upgrade` applies.

        A revision nobody can reach is a table that never gets created in
        production, while every test still passes because the suite builds
        its schema with `create_all`, not with the migrations.
        """
        reachable: set[str] = set()
        for head in checker.find_heads(real_graph):
            node: str | None = head
            while node is not None and node not in reachable:
                reachable.add(node)
                if node not in real_graph:
                    break
                node = real_graph[node]["down_revision"]
        unreachable = sorted(set(real_graph) - reachable)
        assert unreachable == [], f"revisions no upgrade path reaches: {unreachable}"

    def test_revision_ids_are_unique(self, real_graph) -> None:
        """A duplicate id means one file silently shadows the other.

        Alembic keys the revision map by id, so a collision makes the
        graph untrustworthy in a way no head count would reveal.
        """
        files = [g["_path"] for g in real_graph.values()]
        assert len(files) == len(set(files))
        assert len(real_graph) == len(files)

    def test_no_merge_revisions(self, checker, real_graph) -> None:
        """The layered history must contain no branch joins.

        `AGENTS.md` defines the convention: "8 named origin layers, not
        date-stamped revisions", with new schema changes extending a
        layer's `upgrade()`. A merge revision means the layers were split
        and re-converged, which is the one shape that leaves a single head
        while still having been a fork at some point.
        """
        merges = checker.find_merges(real_graph)
        assert merges == [], f"layered history must not contain merge revisions: {merges}"

    def test_no_branch_labels(self, real_graph) -> None:
        """No revision may declare a branch label.

        A `branch_labels` entry is Alembic's marker for an INTENTIONAL
        fork. In a linear layered history there is nothing intentional to
        label, so one appearing means an undeclared branch was introduced.
        """
        labelled = sorted(
            rev for rev, g in real_graph.items() if g.get("branch_labels") is not None
        )
        assert labelled == [], f"revisions declaring branch_labels: {labelled}"


class TestTheCheckerActuallyBlocks:
    """Falsification: each property must FAIL when the property is violated.

    A gate that cannot fail is not a gate. Each test writes a synthetic
    versions/ tree that breaks exactly one property and requires the checker
    to report it. The synthetic trees are built by pointing the checker's
    `check()` at a tmp dir, so the real repository is never touched.
    """

    @staticmethod
    def _write_revision(path: Path, revision: str, down_revision: str | None) -> None:
        path.write_text(
            "from alembic import op\n"
            "\n"
            "revision: str = " + repr(revision) + "\n"
            "down_revision: str | None = " + repr(down_revision) + "\n"
            "\n"
            "def upgrade() -> None:\n"
            "    pass\n"
            "\n"
            "def downgrade() -> None:\n"
            "    pass\n",
            encoding="utf-8",
        )

    def test_two_heads_is_caught(self, checker, tmp_path: Path) -> None:
        """A fork must be reported, naming the heads.

        This is the failure mode the bead exists for. The expectation is a
        specific count, not merely "some problem was found", so a checker
        that trips over the synthetic tree for an unrelated reason (a
        parse error, say) cannot pass this test.
        """
        v = tmp_path / "versions"
        v.mkdir()
        self._write_revision(v / "a.py", "a", None)
        self._write_revision(v / "b.py", "b", "a")
        self._write_revision(v / "c.py", "c", "a")
        self._write_revision(v / "d.py", "d", "b")

        problems = checker.check(v)
        assert any("heads" in p for p in problems), f"fork not reported: {problems}"
        assert any("c" in p and "d" in p for p in problems), (
            f"the report must name both extra heads (c, d): {problems}"
        )

    def test_merge_revision_is_caught(self, checker, tmp_path: Path) -> None:
        """A merge has ONE head, so the head count alone must not pass it.

        `alembic merge` is the shape a human creates to RESOLVE a fork: it
        joins two parents into one child, so `get_heads()` reports a single
        head. A checker that only counts heads therefore passes a history
        that really was split and applied to real databases, with the
        reconciliation buried in an empty merge upgrade(). The parent
        count is what catches it.
        """
        v = tmp_path / "versions"
        v.mkdir()
        self._write_revision(v / "a.py", "a", None)
        self._write_revision(v / "b.py", "b", "a")
        self._write_revision(v / "c.py", "c", "a")
        # The merge: one revision whose down_revision is a TUPLE, which is
        # exactly how the alembic merge template writes a branch join.
        (v / "m.py").write_text(
            "from alembic import op\n"
            "\n"
            'revision: str = "m"\n'
            'down_revision: tuple[str, str] = ("b", "c")\n'
            "\n"
            "def upgrade() -> None:\n"
            "    pass\n"
            "\n"
            "def downgrade() -> None:\n"
            "    pass\n",
            encoding="utf-8",
        )

        graph = checker.load_graph(v)
        heads = checker.find_heads(graph)
        assert heads == ["m"], f"a merge must leave exactly one head, got {heads}"

        problems = checker.check(v)
        assert any("merge" in p for p in problems), (
            f"a merge revision must be reported as a branch-join, got: {problems}"
        )
        assert any("b" in p and "c" in p for p in problems), (
            f"the report must name both parents: {problems}"
        )

    def test_branch_point_without_merge_is_caught(self, checker, tmp_path: Path) -> None:
        """Two children of one parent, left unjoined: an OPEN fork.

        The counterpart to the merge case — same shape, no joining
        revision, so the head count alone is the only thing that catches
        it. Both must fail, for different reasons.
        """
        v = tmp_path / "versions"
        v.mkdir()
        self._write_revision(v / "a.py", "a", None)
        self._write_revision(v / "b.py", "b", "a")
        self._write_revision(v / "c.py", "c", "a")

        problems = checker.check(v)
        assert any("heads" in p for p in problems), f"unjoined fork not reported: {problems}"

    def test_dangling_down_revision_is_caught(self, checker, tmp_path: Path) -> None:
        """A down_revision naming a revision that does not exist must fail.

        Alembic raises `KeyError` on this at load time. Catching it here
        turns a confusing error during deploy into a named failure in CI.
        """
        v = tmp_path / "versions"
        v.mkdir()
        self._write_revision(v / "a.py", "a", None)
        self._write_revision(v / "b.py", "b", "ghost")

        problems = checker.check(v)
        assert any("ghost" in p for p in problems), f"dangling reference not reported: {problems}"

    def test_cycle_is_caught(self, checker, tmp_path: Path) -> None:
        """A cycle has no head at all, and no upgrade could ever apply it."""
        v = tmp_path / "versions"
        v.mkdir()
        self._write_revision(v / "a.py", "a", "b")
        self._write_revision(v / "b.py", "b", "a")

        problems = checker.check(v)
        assert any("no head" in p or "cycle" in p for p in problems), f"cycle not reported: {problems}"

    def test_duplicate_revision_id_is_caught(self, checker, tmp_path: Path) -> None:
        """Two files claiming one id makes the revision map ambiguous."""
        v = tmp_path / "versions"
        v.mkdir()
        self._write_revision(v / "a.py", "a", None)
        self._write_revision(v / "dupe.py", "a", None)

        problems = checker.check(v)
        assert any("duplicate" in p for p in problems), f"duplicate id not reported: {problems}"

    def test_sound_graph_passes(self, checker, tmp_path: Path) -> None:
        """The control: a well-formed chain must NOT be reported.

        Without this, a checker that rejects everything would satisfy every
        test above.
        """
        v = tmp_path / "versions"
        v.mkdir()
        self._write_revision(v / "a.py", "a", None)
        self._write_revision(v / "b.py", "b", "a")
        self._write_revision(v / "c.py", "c", "b")

        assert checker.check(v) == []
        assert checker.find_heads(checker.load_graph(v)) == ["c"]

    def test_init_py_is_not_treated_as_a_revision(self, checker, tmp_path: Path) -> None:
        """`__init__.py` is a package marker, not a node with no revision.

        Treating it as a revision would either crash the parse or add a
        phantom head; the real versions/ directory ships one.
        """
        v = tmp_path / "versions"
        v.mkdir()
        (v / "__init__.py").write_text("", encoding="utf-8")
        self._write_revision(v / "a.py", "a", None)

        assert checker.check(v) == []
        assert "a" in checker.load_graph(v)


class TestTheGateIsWiredInCI:
    """The check runs in CI, unfiltered, and is not allowed to fail.

    Follows the convention already established by
    ``tests/unit/scripts/test_doc_gate_wiring.py`` for the doc gate: a
    checker that exists but is never run is a line in a file.
    """

    def _on_block(self) -> str:
        text = WORKFLOW.read_text(encoding="utf-8")
        match = _ON_BLOCK_RE.search(text)
        assert match, "could not locate the `on:` trigger block"
        body = match.group("body")
        nxt = _TOP_LEVEL_KEY_RE.search(body)
        return body[: nxt.start()] if nxt else body

    def _step_block_containing(self, needle: str) -> str:
        text = WORKFLOW.read_text(encoding="utf-8")
        lines = text.splitlines()
        blocks: list[str] = []
        current: list[str] | None = None
        for line in lines:
            if re.match(r"^\s{6}- (name|uses|run|env|id):", line):
                current = [line]
                blocks.append("\n".join(current))
                continue
            if current is not None:
                current.append(line)
                blocks[-1] = "\n".join(current)
        for block in blocks:
            if needle in block:
                return block
        return ""

    def test_workflow_exists(self) -> None:
        assert WORKFLOW.is_file(), f"missing CI workflow: {WORKFLOW}"

    def test_checker_step_is_present(self) -> None:
        """Removing the step drops the gate; that must be a caught change.

        Matched against the step's ``run:`` command rather than raw file
        text, because this workflow documents its gates in header comments:
        a plain substring search would keep passing after the step was
        deleted while the comment survived.
        """
        step = self._step_block_containing("check_migration_graph.py")
        assert step, (
            f"no step in {WORKFLOW.name} runs check_migration_graph.py; "
            "the migration-graph gate has been removed from CI"
        )
        assert "run:" in step, f"the reference is not a runnable step:\n{step}"

    def test_no_paths_filter_on_triggers(self) -> None:
        """THE BINDING PROPERTY for the gate itself.

        A `paths` filter lets a migrations-only change skip the workflow,
        which is exactly the change class this gate guards.
        """
        block = self._on_block()
        for key in ("paths-ignore", "paths"):
            assert not re.search(rf"^\s+{re.escape(key)}\s*:", block, re.M), (
                f"{WORKFLOW.name} trigger has a `{key}:` filter ({block.strip()!r}); "
                "the migration gate must run on every push/PR"
            )

    def test_gate_step_is_not_allowed_to_fail(self) -> None:
        """``continue-on-error`` would make the gate advisory."""
        step = self._step_block_containing("check_migration_graph.py")
        assert step, f"no step in {WORKFLOW.name} runs check_migration_graph.py"
        assert "continue-on-error" not in step, f"the gate step is allowed to fail:\n{step}"


class TestTheCheckerExitsNonZeroWhenBroken:
    """End-to-end proof that the CI command fails, not just the library.

    The CI step runs `python scripts/check_migration_graph.py`, so the exit
    code is the property that actually gates the build. A checker whose
    `check()` returns violations but whose `main()` returns 0 would be
    wired and still block nothing.
    """

    def _run_against(self, tree: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(tree / "scripts" / "check_migration_graph.py")],
            cwd=tree,
            capture_output=True,
            text=True,
            check=False,
        )

    def _tree_with(self, tmp_path: Path) -> Path:
        """A throwaway repo layout with a forked versions/ tree."""
        tree = tmp_path / "repo"
        (tree / "scripts").mkdir(parents=True)
        (tree / "src" / "migrations" / "versions").mkdir(parents=True)
        (tree / "scripts" / "check_migration_graph.py").write_text(
            CHECKER.read_text(encoding="utf-8"), encoding="utf-8"
        )
        v = tree / "src" / "migrations" / "versions"
        TestTheCheckerActuallyBlocks._write_revision(v / "a.py", "a", None)
        TestTheCheckerActuallyBlocks._write_revision(v / "b.py", "b", "a")
        TestTheCheckerActuallyBlocks._write_revision(v / "c.py", "c", "a")
        return tree

    def test_forked_graph_exits_non_zero(self, tmp_path: Path) -> None:
        tree = self._tree_with(tmp_path)
        result = self._run_against(tree)
        assert result.returncode != 0, (
            f"the checker exited 0 on a forked graph; the CI step would pass:\n"
            f"{result.stdout}\n{result.stderr}"
        )
        assert "heads" in (result.stdout + result.stderr)

    def test_sound_graph_exits_zero(self, tmp_path: Path) -> None:
        tree = self._tree_with(tmp_path)
        c = tree / "src" / "migrations" / "versions" / "c.py"
        c.unlink()
        result = self._run_against(tree)
        assert result.returncode == 0, f"the checker rejected a sound graph:\n{result.stderr}"
        assert "OK" in result.stdout
