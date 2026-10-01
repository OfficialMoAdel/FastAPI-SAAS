"""s92p.1: the layering gate must be declared, and runnable from anywhere.

A gate a developer cannot run before pushing is a gate that is only
discovered broken in CI — which trains people to ignore it. ``AGENTS.md``
and ``ARCHITECTURE.md`` both cite the import contract as an enforced
invariant, so its runnability is load-bearing for the enforcement thesis.

Two failure modes are pinned here:

* the tool being UNDECLARED (absent from the dev dependency group, so a fresh
  clone has no ``lint-imports`` at all);
* the tool being unrunnable from the repo root, because the config lives at
  ``src/.importlinter`` — where ``lint-imports`` reports "Could not read any
  configuration" and exits 1, indistinguishable from a real violation.

The contract RESULT is deliberately not asserted here. H-5 is pre-existing
and currently red; that is ``s92p.2``. Asserting it green would either fail
today or require silencing the very violations this gate exists to catch.
"""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

# This file is tests/unit/core/<file>.py, so the repo root is parents[3].
REPO = Path(__file__).resolve().parents[3]
CONFIG = REPO / "src" / ".importlinter"
SHIM = REPO / "lint_imports.py"


def _declared_dev_dependencies() -> list[str]:
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    out: list[str] = []
    for deps in data.get("dependency-groups", {}).values():
        out.extend(deps)
    return out


def _run_linter(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SHIM)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=300,
    )


class TestToolIsDeclared:
    """The dependency, not an ad-hoc CI install."""

    def test_import_linter_is_in_the_dev_group(self) -> None:
        assert any("import-linter" in d for d in _declared_dev_dependencies()), (
            "import-linter is undeclared; CI installs it ad-hoc, so a developer "
            "cannot run the contract before pushing"
        )

    def test_it_is_pinned_to_a_minimum_version(self) -> None:
        declared = [d for d in _declared_dev_dependencies() if "import-linter" in d]
        assert declared, "import-linter not declared"
        assert any(c in declared[0] for c in (">=", "~=", "==")), (
            f"import-linter should carry a version bound: {declared[0]!r}"
        )

    def test_ci_no_longer_installs_it_ad_hoc(self) -> None:
        """The ad-hoc install is what made the gate CI-only."""
        workflow = REPO / ".github/workflows/security-supply-chain.yml"
        if not workflow.is_file():
            pytest.skip("security workflow not present")
        text = workflow.read_text(encoding="utf-8")
        assert "uv pip install import-linter" not in text, (
            "CI still installs import-linter ad-hoc; it is now a declared "
            "dependency and should come from the dev group"
        )


class TestRunnableFromBothDirectories:
    """The two-directory behaviour is the whole trap."""

    def test_config_and_shim_exist(self) -> None:
        assert CONFIG.is_file(), f"missing {CONFIG}"
        assert SHIM.is_file(), f"missing root shim {SHIM}"

    def test_from_repo_root_it_reads_the_config(self) -> None:
        """Not 'Could not read any configuration' — that was the trap."""
        result = _run_linter(REPO)
        assert "Could not read any configuration" not in result.stdout + result.stderr, (
            "running from the repo root did not reach the contract"
        )

    def test_from_src_it_reaches_the_contracts(self) -> None:
        result = _run_linter(REPO / "src")
        assert "Could not read any configuration" not in result.stdout + result.stderr

    def test_both_invocations_see_the_same_contracts(self) -> None:
        """Same contract set regardless of where it is invoked."""
        root = _run_linter(REPO)
        src = _run_linter(REPO / "src")
        for name in ("H-5", "P1-10", "API adapters"):
            assert (name in root.stdout) == (name in src.stdout), (
                f"contract {name!r} is visible from one directory only"
            )
