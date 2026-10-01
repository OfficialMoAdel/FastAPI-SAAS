"""The operator-facing scripts must actually import — the check that would have caught the bug.

`src/scripts/recover_webhooks.py` is an incident tool. Someone runs it by
hand, by typing `python -m src.scripts.recover_webhooks`, at the moment
webhooks are backed up. It was written against
`src.app.modules.webhooks.recovery`, which was later merged into
`delivery.py`, and against flat `settings.REDIS_QUEUE_*` properties that
moved under `settings.redis_queue`. Both defects were found in review and
fixed before this test existed — the script is correct today.

That is exactly why the test needs to exist. Nothing in this repository
would have caught either defect automatically:

* `ruff` does not flag an import of a module that no longer exists;
* `mypy src` never covered `src/scripts/` (it is not under the `src` path
  the type-check job checks for application modules, and a missing module
  import in a script is not a type error in any case);
* the test suite never imported the script, because no test exercised it.

So the breakage was invisible to every automated check and would have
surfaced only at the worst possible moment. These tests pin the property
that does catch it: the module imports, by module path, exactly as it does
when an operator runs it.

Subprocess rather than in-process, for two reasons. An import failure must
not be able to take the test session down with it, and importing these
scripts for real constructs `Settings` from the environment — the same
reason `tests/conftest.py` sets the variables it sets. Each script is
therefore imported with an environment mirroring the one
`.github/workflows/tests.yml` gives its `tests` job, and the values are
repeated here so the tests do not silently depend on a developer's
`src/.env`.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO / "src/scripts"
CHECKER = REPO / "scripts/check_script_imports.py"

# Mirrors the `tests` job env in .github/workflows/tests.yml and the
# os.environ.setdefault block in tests/conftest.py. The settings object
# refuses to construct without SECRET_KEY, ADMIN_PASSWORD, and the
# Postgres credentials, so a bare subprocess would fail on configuration
# rather than on the property under test.
_ENV = {
    "ENVIRONMENT": "local",
    "SECRET_KEY": "0123456789abcdef0123456789abcdef0123456789abcdef12",
    "ADMIN_PASSWORD": "TestAdmin123!@#456",
    "POSTGRES_USER": "postgres",
    "POSTGRES_PASSWORD": "postgres",
    "POSTGRES_SERVER": "localhost",
    "POSTGRES_PORT": "5432",
    "REDIS_QUEUE_HOST": "localhost",
    "REDIS_QUEUE_PORT": "6379",
    "REDIS_CACHE_HOST": "localhost",
    "REDIS_CACHE_PORT": "6379",
}


def _import_module(module: str, cwd: Path = REPO) -> subprocess.CompletedProcess[str]:
    """Import one module by dotted path in a fresh interpreter."""
    return subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **_ENV},
    )


class TestOperatorScriptsImport:
    """Every script in src/scripts/ imports cleanly.

    The property is behavioural, not textual: it is exactly what happens
    when an operator types the command, which is why it needs the real
    import machinery rather than an AST scan. An AST scan would have
    passed the original broken script, since both broken imports are
    syntactically perfect.
    """

    @pytest.mark.parametrize("name", ["recover_webhooks", "verify_indexes", "create_first_tier"])
    def test_named_script_imports(self, name: str) -> None:
        result = _import_module(f"src.scripts.{name}")
        assert result.returncode == 0, (
            f"src/scripts/{name}.py does not import:\n{result.stderr}"
        )

    def test_every_script_in_the_directory_imports(self) -> None:
        """Covers scripts added later, so the gate is not a fixed list.

        A hand-maintained list of scripts would silently stop covering
        anything new — the same rot the graph checker is meant to prevent.
        """
        scripts = sorted(p.stem for p in SCRIPTS_DIR.glob("*.py") if p.name != "__init__.py")
        assert scripts, "no scripts found — the glob or the directory is wrong"

        failures = []
        for name in scripts:
            result = _import_module(f"src.scripts.{name}")
            if result.returncode != 0:
                detail = (result.stderr.strip().splitlines() or ["<no stderr>"])[-1]
                failures.append(f"{name}: {detail}")
        assert failures == [], f"scripts that do not import: {failures}"


class TestRecoveryScriptTargetsTheLiveModule:
    """The recovery script must point at the module that actually exists.

    Importing the script proves `recover_webhooks` resolves, but not that
    it resolves to the RIGHT thing. A script that imported a still-present
    but superseded module would pass the import check while recovering
    nothing. These assertions pin the specific target, so a future merge
    that moves the function again fails here rather than at an incident.
    """

    def test_imports_recover_from_delivery_not_a_removed_module(self) -> None:
        import src.scripts.recover_webhooks as script
        from src.app.modules.webhooks.delivery import recover_stuck_deliveries

        assert script.recover_stuck_deliveries is recover_stuck_deliveries, (
            "the script must call the live recover_stuck_deliveries; it was "
            "originally written against modules.webhooks.recovery, which was "
            "merged into delivery.py"
        )

    def test_removed_recovery_module_stays_removed(self) -> None:
        """The old module path must NOT come back.

        A re-created `modules/webhooks/recovery.py` shim would let a stale
        import resolve again, hiding the break rather than fixing it.
        """
        assert not (REPO / "src/app/modules/webhooks/recovery.py").exists(), (
            "src/app/modules/webhooks/recovery.py reappeared; recovery lives "
            "in delivery.py. A shim here would mask a stale import."
        )

    def test_uses_nested_settings_not_the_flat_deprecated_accessor(self) -> None:
        """The script must read `settings.redis_queue`, not `settings.REDIS_QUEUE_*`.

        The flat form still resolves through `Settings.__getattr__`, so it
        imports and runs — it is a deprecated delegation that warns on
        first use and is scheduled to go away. A check that only imported
        the module would not distinguish the two forms, so the property is
        pinned structurally.
        """
        source = (SCRIPTS_DIR / "recover_webhooks.py").read_text(encoding="utf-8")
        assert "settings.redis_queue" in source, (
            "the script must read the nested settings.redis_queue component"
        )
        assert "settings.REDIS_QUEUE" not in source, (
            "the script still reads flat settings.REDIS_QUEUE_* properties, "
            "which resolve only via a deprecated delegation"
        )


class TestTheImportGateIsWiredAndBlocks:
    """CI wiring plus falsification, following the doc-gate convention.

    `tests/unit/scripts/test_doc_gate_wiring.py` established the shape for
    this repository: a checker that exists but is never run is a line in a
    file, and a step that is `continue-on-error` is advisory. Same properties,
    same parsing approach (re over the workflow text, not PyYAML, which is
    not a declared dev dependency).
    """

    WORKFLOW = REPO / ".github/workflows/tests.yml"

    def _step_block_containing(self, needle: str) -> str:
        text = self.WORKFLOW.read_text(encoding="utf-8")
        blocks: list[str] = []
        current: list[str] | None = None
        for line in text.splitlines():
            if line.startswith("      - ") and re.match(r"^\s{6}- \w+:", line):
                current = [line]
                blocks.append("\n".join(current))
                continue
            if current is not None:
                current.append(line)
                blocks[-1] = "\n".join(current)
        return next((b for b in blocks if needle in b), "")

    def test_checker_step_is_present(self) -> None:
        step = self._step_block_containing("check_script_imports.py")
        assert step, (
            f"no step in {self.WORKFLOW.name} runs check_script_imports.py; "
            "the operator-script import gate has been removed from CI"
        )
        assert "run:" in step, f"the reference is not a runnable step:\n{step}"

    def test_gate_step_is_not_allowed_to_fail(self) -> None:
        step = self._step_block_containing("check_script_imports.py")
        assert step, f"no step in {self.WORKFLOW.name} runs check_script_imports.py"
        assert "continue-on-error" not in step, f"the gate step is allowed to fail:\n{step}"

    def test_checker_reports_a_broken_import(self, tmp_path: Path) -> None:
        """Falsification: the checker must FAIL on a script that cannot import.

        Builds a throwaway repo containing a copy of the checker and a
        script that imports a module which does not exist — the exact
        original defect — and requires a non-zero exit naming the script.
        Without this, a checker that returned 0 unconditionally would
        satisfy the wiring tests above.
        """
        tree = tmp_path / "repo"
        (tree / "scripts").mkdir(parents=True)
        (tree / "src" / "scripts").mkdir(parents=True)
        (tree / "scripts" / "check_script_imports.py").write_text(
            CHECKER.read_text(encoding="utf-8"), encoding="utf-8"
        )
        # The original defect, reproduced verbatim in shape: a script
        # importing a module that no longer exists.
        (tree / "src" / "scripts" / "broken.py").write_text(
            "from src.app.modules.webhooks.recovery import recover_stuck_deliveries\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, str(tree / "scripts" / "check_script_imports.py")],
            cwd=tree,
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, **_ENV},
        )
        assert result.returncode != 0, (
            f"the checker passed a script with an unresolvable import:\n"
            f"{result.stdout}\n{result.stderr}"
        )
        assert "broken" in (result.stdout + result.stderr), (
            f"the failure must name the offending script:\n{result.stdout}\n{result.stderr}"
        )
