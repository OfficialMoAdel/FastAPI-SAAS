"""The io15.8 response-model gate must be wired in CI, and must actually BIND.

``scripts/check_mutation_response_models.py`` exists for one reason: the next
mutating endpoint written without a ``response_model`` has to fail the build.
A checker nobody runs is a paragraph, not a gate, and a checker that returns 0
unconditionally is worse -- it certifies a property it never tested. This
module pins the three things that make the gate real:

1. **It is wired** into the ``lint`` job of ``.github/workflows/tests.yml``,
   unconditionally, so it runs on every push and pull request.
2. **It binds.** The checker is copied into a temporary tree which is then
   given a brand-new undeclared mutating endpoint; the checker must exit
   non-zero and name the handler. The same tree, without that endpoint, must
   exit zero. Both halves matter: the first is the gate, the second is what
   stops the first from passing for the wrong reason.
3. **Its allow-list is live, not decorative.** Every ``ALLOWLIST`` key must
   resolve to a real undeclared handler, and every entry must state a reason,
   so an exception cannot outlive the endpoint it was written for or become
   an unexplained hole.

Why the wiring assertions read source text: the property under test for (1) IS
the workflow file. A GitHub Actions ``on:`` block is not runnable outside
Actions, and PyYAML is not a declared dev dependency here, so the trigger
filter is read with ``re`` over stdlib -- the same reasoning, and the same
step-block helper, as ``test_doc_gate_wiring.py``.

Note on (2): the temp tree is the point of the exercise. Falsifying this gate
by editing a real router and editing it back has bitten this repo before;
copying the checker into ``tmp_path`` means the "broken" run touches no
tracked file at all, so there is nothing to restore and nothing left behind.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github/workflows/tests.yml"
CHECKER = "scripts/check_mutation_response_models.py"

_ON_BLOCK_RE = re.compile(r"^on:\n(?P<body>(?:[ \t]+.*\n|[ \t]*\n)*)", re.M)
_TOP_LEVEL_KEY_RE = re.compile(r"^[A-Za-z_-]+:", re.M)

#: A mutation endpoint that declares no response_model, and the same router
#: without it. Written out so the "clean" and "broken" trees differ by exactly
#: one decorator and nothing else, which is what makes the pair a real
#: counter-case rather than two unrelated trees.
UNDECLARED_HANDLER = """\
from fastapi import APIRouter

router = APIRouter(prefix="/widgets")


@router.post("/widgets", response_model=dict)
async def create_widget() -> dict:
    return {"widget": "made"}


@router.delete("/widgets/{widget_id}")
async def delete_widget(widget_id: int) -> dict:
    return {"deleted": widget_id}
"""

CLEAN_HANDLER = """\
from fastapi import APIRouter

router = APIRouter(prefix="/widgets")


@router.post("/widgets", response_model=dict)
async def create_widget() -> dict:
    return {"widget": "made"}
"""


def _on_block() -> str:
    """The raw ``on:`` block, or "" if it cannot be found."""
    match = _ON_BLOCK_RE.search(WORKFLOW.read_text(encoding="utf-8"))
    if not match:
        return ""
    body = match.group("body")
    nxt = _TOP_LEVEL_KEY_RE.search(body)
    return body[: nxt.start()] if nxt else body


def _step_block_containing(needle: str) -> str:
    """The full step entry whose ``run:``/``uses:`` command mentions ``needle``.

    Anchored on the command line, NOT on any line that merely contains the
    text: the checker's name appears in this file's own header comment and in
    the guard's own module docstring, and neither is a workflow step. From the
    command line the block extends back to the step's ``- name:``/``- uses:``
    item and forward to the next item at the same indent, so
    ``continue-on-error`` is caught whether it is written above or below
    ``run:``. Mirrors the helper in ``test_doc_gate_wiring.py``.
    """
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if needle not in stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("run:") or stripped.startswith("uses:"):
            start = index
            break
    if start is None:
        return ""

    dash_indent = None
    first = start
    for i in range(start, -1, -1):
        line = lines[i]
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.lstrip().startswith("-"):
            dash_indent = len(line) - len(line.lstrip())
            first = i
            break
        if len(line) - len(line.lstrip()) == 0:
            break
    if dash_indent is None:
        return ""

    block = list(lines[first : start + 1])
    for follow in lines[start + 1 :]:
        if not follow.strip():
            block.append(follow)
            continue
        if len(follow) - len(follow.lstrip()) <= dash_indent:
            break
        block.append(follow)
    return "\n".join(block)


class TestGateIsWired:
    """The checker runs in CI, on every change class."""

    def test_workflow_has_no_path_filter(self) -> None:
        """No ``paths``/``paths-ignore`` filter may skip the job running the gate."""
        block = _on_block()
        assert block, "could not find the top-level `on:` block"
        assert "paths" not in block, f"a path filter would skip the gate:\n{block}"

    def test_gate_step_is_present_and_mandatory(self) -> None:
        """The step exists, is not allowed to fail, and runs the real checker."""
        step = _step_block_containing("check_mutation_response_models.py")
        assert step, "no workflow step runs the mutation response model gate"
        assert "continue-on-error" not in step, f"the gate is allowed to fail:\n{step}"
        assert "python scripts/check_mutation_response_models.py" in step


class TestGateBinds:
    """A new undeclared mutating endpoint must fail the checker."""

    @staticmethod
    def _copy_with_empty_allowlist(dest: Path) -> None:
        """Copy the checker with its ALLOWLIST replaced by an empty dict.

        The replacement is line-based, not a text split at the ``ALLOWLIST``
        declaration. Everything after that dict IS the gate -- ``Undeclared``,
        ``scan``, ``check``, ``main`` and the ``__main__`` entry point -- so
        truncating the file there produces a copy that does nothing and exits
        0. That failure is silent and would make the "broken" case pass for
        the wrong reason, so the entry point is asserted below.
        """
        lines = (REPO / CHECKER).read_text(encoding="utf-8").splitlines()
        try:
            start = lines.index("ALLOWLIST: dict[str, str] = {")
            end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
        except (ValueError, StopIteration):  # pragma: no cover - guard restructured
            raise AssertionError(
                "the guard no longer declares a top-level ALLOWLIST dict literal"
            ) from None
        text = "\n".join(lines[:start] + ["ALLOWLIST: dict[str, str] = {}"] + lines[end + 1 :])
        assert 'if __name__ == "__main__":' in text, "the trimmed copy lost its entry point"
        dest.write_text(text + "\n", encoding="utf-8")

    @classmethod
    def _make_tree(cls, tmp_path: Path) -> Path:
        """A minimal repo the checker can scan, holding a copy of the checker.

        The checker derives ROOT from its own ``__file__`` and walks
        ``src/app/modules`` and ``src/app/api`` relative to it, so a temp tree
        needs its own copy under ``scripts/`` plus both scan roots existing.
        They are created so the CLEAN case genuinely exits 0 -- otherwise a
        missing scan root would itself be an error and the broken case would
        pass for the wrong reason.
        """
        (tmp_path / "scripts").mkdir()
        cls._copy_with_empty_allowlist(tmp_path / "scripts" / "check_mutation_response_models.py")
        (tmp_path / "src" / "app" / "modules").mkdir(parents=True)
        (tmp_path / "src" / "app" / "api").mkdir(parents=True)
        (tmp_path / "src" / "app" / "modules" / "widgets").mkdir()
        return tmp_path

    @staticmethod
    def _run_checker_in(tree: Path) -> subprocess.CompletedProcess[str]:
        """Run the temp tree's OWN copy of the checker against that tree."""
        return subprocess.run(
            [sys.executable, str(tree / "scripts" / "check_mutation_response_models.py")],
            cwd=tree,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_new_undeclared_endpoint_fails_and_clean_tree_passes(self, tmp_path: Path) -> None:
        """The counter-case: same command, same tree, one handler added.

        Both directions are asserted because a one-sided test cannot tell a
        working gate from a checker that always exits 0 (which is what the
        clean case alone would show) or one that always exits 1 (which is what
        the broken case alone would show). This test fails under either, which
        is the only way to know the exit code actually tracks the tree.
        """
        tree = self._make_tree(tmp_path)
        router = tree / "src" / "app" / "modules" / "widgets" / "router.py"

        router.write_text(CLEAN_HANDLER, encoding="utf-8")
        clean = self._run_checker_in(tree)
        assert clean.returncode == 0, (
            f"precondition: the clean tree must pass, got:\n{clean.stdout}{clean.stderr}"
        )

        router.write_text(UNDECLARED_HANDLER, encoding="utf-8")
        broken = self._run_checker_in(tree)
        assert broken.returncode != 0, "a new undeclared mutation endpoint did not fail the gate"
        assert "delete_widget" in (broken.stdout + broken.stderr)


class TestAllowListIsLive:
    """Every exception must still describe a real undeclared endpoint."""

    @staticmethod
    def _gate():
        """Import the guard under test off the scripts path."""
        sys.path.insert(0, str(REPO / "scripts"))
        try:
            import check_mutation_response_models

            return check_mutation_response_models
        finally:
            sys.path.pop(0)

    def test_no_allowlist_entry_is_stale(self) -> None:
        """A key that resolves to nothing is a permission slip, not a debt record."""
        gate = self._gate()
        _undeclared, covered = gate.scan()
        stale = sorted(set(gate.ALLOWLIST) - covered)
        assert not stale, (
            "allow-list entries that no longer match a live undeclared endpoint "
            f"(delete them, or the endpoint was renamed/moved): {stale}"
        )

    def test_every_allowlist_entry_states_a_reason(self) -> None:
        """An entry with no reason is an undeclared escape hatch."""
        gate = self._gate()
        unreasoned = sorted(key for key, reason in gate.ALLOWLIST.items() if not reason.strip())
        assert not unreasoned, f"allow-list entries with no stated reason: {unreasoned}"

    def test_allowlist_covers_every_undeclared_endpoint(self) -> None:
        """The allow-list is the complete, reviewed set of exceptions.

        Asserting both directions stops the list from quietly becoming
        either a subset (a new hole appears, and the gate no longer explains
        it) or a superset (dead entries accumulate as folklore).
        """
        gate = self._gate()
        undeclared, covered = gate.scan()
        assert not undeclared, f"undeclared mutating endpoints escaped the gate: {undeclared}"
        assert covered == set(gate.ALLOWLIST), "the two directions of the allow-list disagree"
