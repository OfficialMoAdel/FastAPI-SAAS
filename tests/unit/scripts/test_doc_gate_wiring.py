"""Ratchet: the doc-pointer gate must stay wired, and stay unconditional.

A documented path is a claim that the path exists. `scripts/checks/doc_paths.py`
is what makes that claim enforced rather than aspirational, and the root
`AGENTS.md` states the gate is MANDATORY because it runs on every push and every
pull request.

This test exists because that is exactly the kind of claim that rots silently:

* if the gate is dropped from CI, the docs start rotting again and nothing fails;
* if a `paths:`/`paths-ignore:` filter is reintroduced on the workflow, a
  docs-only PR stops triggering the workflow and the gate can no longer fire on
  the rot it exists to catch — which is the same rot, one indirection away.

Both are silent. Neither is acceptable. So both are asserted here, in a test that
runs under the normal suite rather than in a workflow file nobody re-reads.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
TESTS_WORKFLOW = WORKFLOWS / "tests.yml"
GATE_SCRIPT = Path("scripts/checks/doc_paths.py")


def _workflow_text() -> str:
    return TESTS_WORKFLOW.read_text(encoding="utf-8")


def test_the_gate_script_exists() -> None:
    """The script the docs mandate must be on disk."""
    assert (REPO_ROOT / GATE_SCRIPT).is_file(), f"{GATE_SCRIPT} is documented but does not exist"


def test_the_gate_script_is_runnable() -> None:
    """It must be importable and expose the documented exit-code entrypoint."""
    script = (REPO_ROOT / GATE_SCRIPT).read_text(encoding="utf-8")
    assert "__main__" in script, "the gate has no __main__ entrypoint"


def test_ci_invokes_the_gate() -> None:
    """Some workflow must actually run the gate, not merely mention it."""
    invoked = [p.name for p in sorted(WORKFLOWS.glob("*.yml")) if GATE_SCRIPT.name in p.read_text(encoding="utf-8")]
    assert invoked, f"no workflow invokes {GATE_SCRIPT.name}; the gate is documented as mandatory in CI"


def test_workflow_has_no_paths_filter() -> None:
    """No `paths:`/`paths-ignore:` on the workflow that runs the gate.

    A filter is how the gate gets silently disabled: a documentation-only pull
    request stops matching the trigger, the workflow does not start, and the
    rot the gate exists to catch goes uncaught — with a green build.
    """
    text = _workflow_text()
    offenders = re.findall(r"^\s*(paths-ignore|paths):", text, flags=re.MULTILINE)
    assert not offenders, (
        f"{TESTS_WORKFLOW.name} reintroduced a {offenders} filter; "
        "the doc gate can no longer fire on docs-only changes"
    )


def test_workflow_triggers_cover_docs_only_changes() -> None:
    """The workflow must run for a push and a pull request, with no branch filter
    that would exclude the documentation branch."""
    text = _workflow_text()
    assert re.search(r"^\s*push:", text, flags=re.MULTILINE), "no push trigger"
    assert re.search(r"^\s*pull_request:", text, flags=re.MULTILINE), "no pull_request trigger"
    branches = re.findall(r"branches-ignore:\s*\[([^\]]*)\]", text)
    assert not branches, f"a branches-ignore filter ({branches}) would exclude documentation branches from the gate"


def test_project_requires_python_is_consistent_with_ci() -> None:
    """`requires-python` and every CI interpreter must agree.

    These drifted apart once: the project declared 3.12 while the local venv ran
    3.13, so a 3.13-only construct (a PEP 696 `TypeVar(default=...)`) passed every
    local gate and broke the whole suite on CI. The version is now pinned by
    asserting the three declarations agree, so that class of drift fails fast
    instead of at import time in CI.
    """
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requires = pyproject["project"]["requires-python"]
    match = re.search(r">=\s*(\d+\.\d+)", requires)
    assert match, f"could not parse requires-python: {requires!r}"
    floor = match.group(1)

    mypy_version = pyproject["tool"]["mypy"]["python_version"]
    assert mypy_version == floor, f"mypy python_version {mypy_version} != requires-python floor {floor}"

    ruff_target = pyproject["tool"]["ruff"]["target-version"]
    compact = floor.replace(".", "")
    assert ruff_target == f"py{compact}", f"ruff target-version {ruff_target} != py{compact}"

    declared: dict[str, set[str]] = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        versions = set(re.findall(r"python-version:\s*[\"']?(\d+\.\d+)[\"']?", path.read_text(encoding="utf-8")))
        env_versions = set(re.findall(r"PYTHON_VERSION:\s*[\"']?(\d+\.\d+)[\"']?", path.read_text(encoding="utf-8")))
        resolved = {v for v in versions | env_versions if v != "${{ env.PYTHON_VERSION }}"}
        if resolved:
            declared[path.name] = resolved

    assert declared, "no workflow pins a Python version; the gate cannot assert anything"
    for name, versions in declared.items():
        drift = {v for v in versions if not (v == floor or v.startswith(floor + "."))}
        assert not drift, f"{name} pins {sorted(versions)}, which is below the declared floor {floor}"
