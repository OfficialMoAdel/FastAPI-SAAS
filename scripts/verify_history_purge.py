"""Verify that no known-leaked credential survives anywhere in a git repo.

Read-only. Safe to run against the real repository or a rewritten mirror.

Reports COUNTS AND PATHS ONLY — never a secret value. The values are read
from the blocklist at runtime precisely so that nobody has to paste them into
a shell command, a log, or a commit message to use this tool.

The blocklist files are excluded from the result, and that exclusion is
deliberate: `crypt.py` and `test_secret_blocklist.py` contain the values as
raw literals because the validator compares by equality. Including them would
mean this tool could never return clean, and a check that can never pass gets
ignored.

Usage:
    uv run python scripts/verify_history_purge.py            # the real repo
    uv run python scripts/verify_history_purge.py /tmp/mirror.git
    uv run python scripts/verify_history_purge.py --tree-only # skip history scan

Exit codes:
    0 - no leaked value outside the blocklist files, in any ref
    1 - at least one ref still carries one
    2 - the repo could not be scanned
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files that hold the blocklist on purpose. See module docstring.
BLOCKLIST_FILES = frozenset(
    {
        "src/app/core/config/crypt.py",
        "src/app/core/config/database.py",
        "tests/unit/core/test_secret_blocklist.py",
    }
)


def _load_blocklist() -> list[str]:
    """Read the known-leaked values from the validator, without echoing them."""
    env_defaults = {
        "ENVIRONMENT": "local",
        "SECRET_KEY": "0" * 64,
        "ADMIN_PASSWORD": "VerifyOnlyNotReal1!",
        "POSTGRES_USER": "postgres",
        "POSTGRES_PASSWORD": "postgres",
        "POSTGRES_DB": "postgres",
        "POSTGRES_SERVER": "localhost",
        "POSTGRES_PORT": "5432",
        "REDIS_QUEUE_HOST": "localhost",
        "REDIS_QUEUE_PORT": "6379",
        "REDIS_CACHE_HOST": "localhost",
        "REDIS_CACHE_PORT": "6379",
    }
    for key, value in env_defaults.items():
        os.environ.setdefault(key, value)
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    from src.app.core.config.crypt import KNOWN_LEAKED_SECRET_KEYS  # noqa: PLC0415
    from src.app.core.config.database import KNOWN_LEAKED_POSTGRES_PASSWORDS  # noqa: PLC0415

    values = [v for v in KNOWN_LEAKED_SECRET_KEYS if v]
    values += [p for p in KNOWN_LEAKED_POSTGRES_PASSWORDS if p]
    if not values:
        raise RuntimeError("the blocklist is empty; this check cannot pass")
    return values


def _git(repo: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, errors="replace"
    )


def _path_of(line: str) -> str:
    # `git grep -l <ref>` prints "<ref>:<path>"; a bare ref prints just the path.
    head, _, rest = line.partition(":")
    return rest if rest and "/" in head else line


def scan_ref(repo: str, ref: str, secrets: list[str]) -> int:
    hits = 0
    for secret in secrets:
        result = _git(repo, "grep", "-l", "-I", "-F", secret, ref, "--")
        for line in (result.stdout or "").splitlines():
            path = _path_of(line.strip())
            if path and path not in BLOCKLIST_FILES:
                hits += 1
    return hits


def scan_worktree(repo: str, secrets: list[str]) -> int:
    """Untracked and gitignored files are NOT scanned.

    They are not in history and are not exposed by the repository; they are a
    local-hygiene question, covered by the tracked-env-guard CI job.
    """
    result = _git(repo, "grep", "-l", "-I", "-F", secrets[0], "HEAD", "--")
    return len([line for line in (result.stdout or "").splitlines() if line.strip()])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", nargs="?", default=str(REPO_ROOT), help="repository path (default: this repo)")
    parser.add_argument("--tree-only", action="store_true", help="check only the HEAD tree, not full ref history")
    args = parser.parse_args(argv)

    try:
        secrets = _load_blocklist()
    except Exception as exc:
        print(f"could not load the blocklist: {exc}", file=sys.stderr)
        return 2

    print(f"checking {args.repo} against {len(secrets)} blocklisted value(s)")
    print(f"blocklist files excluded from the result: {sorted(BLOCKLIST_FILES)}\n")

    refs = _git(args.repo, "for-each-ref", "--format=%(refname)").stdout.split()

    dirty: list[tuple[str, int]] = []
    if args.tree_only:
        tree_hits = scan_worktree(args.repo, secrets)
        print(f"HEAD tree: {tree_hits} occurrence(s) outside the blocklist files")
        if tree_hits:
            print("\nFAIL: the current tree still carries a leaked value.")
            return 1
        print("\nOK: HEAD tree is clean.")
        return 0

    for ref in refs:
        hits = scan_ref(args.repo, ref, secrets)
        if hits:
            dirty.append((ref, hits))

    print(f"refs scanned: {len(refs)}")
    if not dirty:
        print("OK: no ref carries a leaked value outside the blocklist files.")
        return 0

    print(f"\nFAIL: {len(dirty)} of {len(refs)} refs still carry a leaked value:")
    for ref, hits in dirty:
        print(f"    {ref}: {hits}")
    print(
        "\nA rewrite that does not cover these refs leaves the values reachable.\n"
        "See docs/HISTORY_PURGE_RUNBOOK.md section 4 for which refs a prior\n"
        "attempt typically missed."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
