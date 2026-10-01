"""s92p.1: the layering contract must be runnable before a push, from anywhere.

The config lives at ``src/.importlinter`` (its ``root_packages = src`` is
relative to that directory). Run from the repository root, ``lint-imports``
reports "Could not read any configuration" and exits 1 — indistinguishable
from a real contract violation, which is the trap the bead describes.

This root shim is that fix: it finds the config relative to the repo root and
re-executes the linter from ``src/``, so ``lint-imports`` behaves identically
no matter where it is invoked. A task-runner target would have required new
infrastructure; a shim is three lines and makes the documented command just
work.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve()


def _main() -> int:
    import importlinter.cli

    return int(importlinter.cli.lint_imports(no_logo=True))


if __name__ == "__main__":
    # importlinter resolves its config from the CURRENT working directory, so
    # re-enter src/ (the config's own directory) before linting.
    src = _HERE.parent / "src"
    if not (src / ".importlinter").is_file():
        print(f"import contract config not found at {src / '.importlinter'}", file=sys.stderr)
        raise SystemExit(2)
    import os

    os.chdir(src)
    raise SystemExit(_main())
