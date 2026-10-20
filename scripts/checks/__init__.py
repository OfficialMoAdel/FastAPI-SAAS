"""Repository consistency gates — the checks CI runs on every push.

Each module in this package is a self-contained gate that reads the tree and
exits non-zero when it finds a defect. They are grouped here so the set of
gates is one directory listing rather than a dozen files mixed in with the
one-shot operator scripts under ``scripts/``.

Two properties every gate in here holds:

* it needs no database and no network — the lint job runs them all;
* it has a test beside it under ``tests/unit/scripts/`` that pins the
  behaviour CI depends on (usually that the gate CAN fail).

Run one directly from the repository root:

    uv run python scripts/checks/doc_paths.py

See ``AGENTS.md`` for which gate enforces what.
"""
