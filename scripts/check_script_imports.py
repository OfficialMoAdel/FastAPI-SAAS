"""CI check: the operator-facing scripts under `src/scripts/` must import.

A script in `src/scripts/` is an entry point someone runs by hand, usually
during an incident, when the system is already broken. Its failure mode is
the worst one available: `python -m src.scripts.recover_webhooks` raises
`ModuleNotFoundError` at startup and recovers nothing, at the exact moment
someone needed the recovery.

`src/scripts/recover_webhooks.py` is the concrete case. It was written
against `modules.webhooks.recovery`, which was later merged into
`delivery.py`, and against flat `settings.REDIS_QUEUE_*` properties that
moved under `settings.redis_queue`. Both of those were caught in review and
fixed — but nothing in CI would have caught either. A module-level import of
a deleted module is invisible to ruff unless the import is inside a function,
invisible to mypy if the file is excluded, and invisible to the test suite
because nothing imports the script. The breakage only surfaces when a human
types the command.

So this gate imports each script for real, in a subprocess, and requires the
import to succeed. That is the property that matters and it needs no
database: importing the module runs its module-level code (imports,
decorators, constants) without calling `main()`.

Scripts are imported by module path rather than by file path so that
`from src.app...` inside them resolves exactly as it does when an operator
runs `python -m`. Running each in a subprocess rather than in-process is
deliberate: a script that mutates global state at import time, or that
fails to import, must not be able to take the gate itself down with it.

Usage:
    uv run python scripts/check_script_imports.py

Exit codes:
    0 - every operator script imports cleanly
    1 - at least one script failed to import
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "src" / "scripts"

# Imported rather than spawned, because the app's settings need the same
# environment the test job provides. `conftest.py` supplies these for the
# pytest suite; a bare subprocess gets them here. Anything importing
# `src.app.core.config` raises on a missing SECRET_KEY, which is a
# configuration error in the GATE, not in the script under test — so the
# gate supplies known-good values and says so.
_ENV = {
    "ENVIRONMENT": "local",
    "SECRET_KEY": "0123456789abcdef0123456789abcdef0123456789abcdef12",
    "ADMIN_PASSWORD": "TestAdmin123!@#456",
    # PostgresSettings requires USER and PASSWORD with no defaults, so any
    # import that reaches src.app.core.config fails without them. These are
    # the values tests/conftest.py sets and the ones tests.yml exports to
    # the tests job.
    "POSTGRES_USER": "postgres",
    "POSTGRES_PASSWORD": "postgres",
    "POSTGRES_SERVER": "localhost",
    "POSTGRES_PORT": "5432",
    "REDIS_QUEUE_HOST": "localhost",
    "REDIS_QUEUE_PORT": "6379",
    "REDIS_CACHE_HOST": "localhost",
    "REDIS_CACHE_PORT": "6379",
}


def script_modules(scripts_dir: Path = SCRIPTS_DIR) -> list[str]:
    """Return the importable module names under scripts_dir, sorted.

    `__init__.py` is a package marker, not an entry point.
    """
    return [
        f"src.scripts.{p.stem}"
        for p in sorted(scripts_dir.glob("*.py"))
        if p.name != "__init__.py"
    ]


def check(scripts_dir: Path = SCRIPTS_DIR) -> list[str]:
    """Return one message per script that fails to import (empty = all good)."""
    import os

    if not scripts_dir.is_dir():
        return [f"missing scripts directory: {scripts_dir}"]

    env = {**os.environ, **_ENV}
    failures: list[str] = []
    for module in script_modules(scripts_dir):
        result = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        if result.returncode != 0:
            detail = (result.stderr.strip().splitlines() or ["<no stderr>"])[-1]
            failures.append(f"{module}: {detail}")
    return failures


def main() -> int:
    failures = check()
    if failures:
        print(
            "FAIL: operator-facing scripts in src/scripts/ do not import:",
            file=sys.stderr,
        )
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        print(
            "\nThese are run by hand, usually during an incident. A broken "
            "import means the script recovers nothing at the moment it is "
            "needed. If a module was merged or moved, update the import.",
            file=sys.stderr,
        )
        return 1
    print(f"OK: {len(script_modules(SCRIPTS_DIR))} operator scripts import cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
