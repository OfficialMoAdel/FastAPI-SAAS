"""P-9a: the provenance manifest every perf run must write, and the gate that voids a run whose harness is not pinned.

WHY THIS EXISTS
---------------
``locustfile.py`` was last committed in ``4cd00654`` (2026-09-22 15:24:32), while
``baseline_20260922_011954_stats.csv`` is stamped 2026-09-22 01:19:54 - fourteen
hours earlier. The instrument therefore changed in the same window as the
application, so the two baselines are not a same-instrument A/B and the headline
p95 figure cannot be attributed to the application at all.

The generalisable failure is not the timestamps; it is that an artefact is
comparable only if its instrument is recoverable from git. A baseline whose
harness was untracked, dirty, or only ever described in prose can never be
re-run, so it can never be checked. This module makes that condition impossible
to miss:

* :func:`build_manifest` records the harness at BYTE level (the git blob SHA of
  every file the run executes, plus the commit that last touched each one) and
  the application at COMMIT level (HEAD). If those disagree, or if the artefact
  has no manifest, the run is not interpretable.
* :func:`assert_harness_pinned` refuses to let a run start when any harness file
  is untracked, dirty, or absent. The shell script turns that refusal into a
  VOID.

Two standing rules are encoded rather than left to prose:

* A number produced with ``AUTO_BAN_ENABLED=false`` must carry that label, or it
  may not be reported. ``run_perf_baseline.sh`` relaxes limits on purpose, so
  every artefact it produces needs the label.
* A number measured on Windows is non-representative and may never serve as a
  ``before`` arm (addendum A-5). The manifest records the platform and says so,
  so the label travels with the artefact instead of living in a reviewer's head.

NOTHING HERE IS INFERRED OR DEFAULTED. If git is unavailable, or a settings
object cannot be built, this raises: a manifest that silently omits the fact it
could not establish is worse than no manifest, because it reads as complete.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The load script itself. Referenced by its own shebang invocation rather than
#: by a path literal inside its body, so the drift guard in the test knows to
#: exempt it.
LOAD_SCRIPT = "scripts/run_perf_baseline.sh"

#: Every file a run EXECUTES. This is the set the pin gate covers, and the set
#: the manifest records a blob SHA for. Order is stable for reproducible output.
#:
#: The application under test (``src/app``) is deliberately NOT here: it is
#: thousands of files, and one commit SHA describes it exactly. It is recorded
#: in the manifest under ``git.sha``.
#:
#: ``tests/performance/locustfile.py`` is the file whose commit timestamp is the
#: whole reason this module exists.
HARNESS_FILES: tuple[str, ...] = (
    LOAD_SCRIPT,
    # The Locust load definition. Last commit 4cd00654, 2026-09-22 15:24:32.
    "tests/performance/locustfile.py",
    # Invoked by ``run_perf_baseline.sh --seed``.
    "scripts/setup_locust_test_data.py",
    # This module. It writes the manifest, so an unpinned version of it makes
    # every other field in the artefact untrustworthy.
    "src/benchmarks/run_manifest.py",
)

#: Path literals in the load script that are OUTPUTS, not inputs. Kept beside
#: HARNESS_FILES so that adding a file to the script without classifying it is a
#: test failure rather than a silently unpinned run.
OUTPUT_PATH_PREFIXES: tuple[str, ...] = (
    "reports/",
    "locust_baseline_",
)

#: The app entrypoint the load script starts. Not harness code: its provenance
#: is the commit SHA, and its contents are the application under test.
APP_ENTRYPOINT = "src/app/main:app"

MANIFEST_SCHEMA = "perf-run-manifest/1"


class HarnessNotPinned(RuntimeError):
    """A run may not start: at least one harness file is not reproducible from git.

    Carries the offending file states on ``.files`` so a caller can print a
    table instead of a sentence.
    """

    def __init__(self, files: list[HarnessFileState]) -> None:
        self.files = files
        detail = ", ".join(f"{f.path} ({f.reason})" for f in files)
        super().__init__(
            f"VOID: the harness is not pinned to git: {detail}. "
            "A baseline whose instrument cannot be recovered from git can never be "
            "re-run, so it can never be checked. Commit the harness, or drop the "
            "local edit, and re-run."
        )


@dataclass(frozen=True, slots=True)
class HarnessFileState:
    """What git knows about one harness file."""

    path: str
    exists: bool
    tracked: bool
    dirty: bool
    blob_sha: str | None
    last_commit: str | None
    last_commit_at: str | None

    @property
    def pinned(self) -> bool:
        return self.exists and self.tracked and not self.dirty

    @property
    def reason(self) -> str:
        if not self.exists:
            return "missing"
        if not self.tracked:
            return "untracked"
        if self.dirty:
            return "dirty"
        return "clean"


def _git(repo_root: Path, *args: str) -> str | None:
    """Run one git command; return stripped stdout, or None if it found nothing.

    A missing binary or a non-zero exit is NOT swallowed - the callers below
    turn every None into either a ``tracked=False``/``dirty=True`` state or a
    raise, so a broken git shows up as a VOID rather than as a clean manifest.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    return out or None


def harness_state(repo_root: Path, relative: str) -> HarnessFileState:
    """Resolve one harness file's git state.

    ``dirty`` is deliberately conservative: it is True if the file is untracked
    OR modified OR staged. An untracked file already fails on ``tracked``, so
    folding that in only changes the message, never the verdict.
    """
    path = repo_root / relative
    if not path.exists():
        return HarnessFileState(relative, False, False, False, None, None, None)

    blob = _git(repo_root, "rev-parse", f"HEAD:{relative}")
    tracked = blob is not None
    # --porcelain on an untracked path prints "??"; on a clean path nothing.
    porcelain = _git(repo_root, "status", "--porcelain", "--", relative)
    dirty = porcelain is not None

    last_commit = _git(repo_root, "log", "-1", "--format=%H", "--", relative)
    last_commit_at = _git(repo_root, "log", "-1", "--format=%cI", "--", relative)
    return HarnessFileState(relative, True, tracked, dirty, blob, last_commit, last_commit_at)


def harness_states(repo_root: Path = REPO_ROOT) -> tuple[HarnessFileState, ...]:
    return tuple(harness_state(repo_root, rel) for rel in HARNESS_FILES)


def unpinned_harness_files(repo_root: Path = REPO_ROOT) -> tuple[HarnessFileState, ...]:
    return tuple(f for f in harness_states(repo_root) if not f.pinned)


def assert_harness_pinned(repo_root: Path = REPO_ROOT) -> tuple[HarnessFileState, ...]:
    """Raise :class:`HarnessNotPinned` unless every harness file is pinned.

    This is criterion (b): the run must VOID, not warn, and it must void on
    *dirty* as well as untracked - a dirty harness is the same unrecoverable
    instrument as an untracked one, and is the easier mistake to make.
    """
    bad = list(unpinned_harness_files(repo_root))
    if bad:
        raise HarnessNotPinned(bad)
    return harness_states(repo_root)


#: Imports whose first segment is ``app`` land inside ``src/app``, which the
#: manifest already pins exactly, at commit granularity, via ``git.sha``. Listing
#: those thousands of files individually would say nothing the app SHA does not,
#: so they are excluded and covered by ``git.sha`` instead.
_APP_MODULE_ROOT = "app"


def _module_to_repo_file(repo_root: Path, module: str) -> Path | None:
    """Where a dotted module name resolves in this repo, or ``None``.

    ``None`` covers site-packages (``locust``, ``httpx``), stdlib, and names that
    do not exist here. Only a name that resolves to a real file inside this
    checkout can be a clean-checkout dependency.
    """
    parts = module.split(".")
    if not parts or parts[0] != "src" or (len(parts) > 1 and parts[1] == _APP_MODULE_ROOT):
        return None
    candidate = repo_root.joinpath(*parts).with_suffix(".py")
    if not candidate.is_file():
        package_init = candidate.with_name("__init__.py")
        return package_init if package_init.is_file() else None
    return candidate


def repo_module_imports(path: Path) -> tuple[str, ...]:
    """Every repo-rooted module this file imports.

    Walks the whole tree rather than only module level, because a helper that
    imports a private helper is exactly as much a dependency of the run as a
    top-level import is.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return tuple(sorted(set(modules)))


def harness_dependencies(repo_root: Path = REPO_ROOT) -> dict[str, tuple[str, ...]]:
    """Transitive repo files the harness modules import, keyed by harness file.

    Criterion (d), and the level the first gate cannot see. ``HARNESS_FILES`` is
    a hand-maintained list, so a harness module can acquire a new repo import and
    the gate above would stay green while the run quietly depends on a file a
    clean checkout does not have - which is the 2026-09-22 failure again, one
    level down. A dirty or untracked *root* is caught by
    :func:`assert_harness_pinned`; an untracked *dependency* is caught here.
    """
    resolved: dict[str, tuple[str, ...]] = {}
    seen: set[str] = set()

    def walk(relative: str) -> tuple[str, ...]:
        if relative in resolved:
            return resolved[relative]
        if relative in seen:
            # A cycle cannot add a new dependency; stop rather than recurse.
            return ()
        seen.add(relative)
        path = repo_root / relative
        if not path.is_file():
            return ()
        nested: set[str] = set()
        for module in repo_module_imports(path):
            target = _module_to_repo_file(repo_root, module)
            if target is None:
                continue
            child = target.relative_to(repo_root).as_posix()
            nested.add(child)
            nested.update(walk(child))
        resolved[relative] = tuple(sorted(nested))
        return resolved[relative]

    for harness_file in HARNESS_FILES:
        if harness_file.endswith(".py"):
            resolved[harness_file] = walk(harness_file)
    return resolved


def untracked_harness_dependencies(repo_root: Path = REPO_ROOT) -> tuple[str, ...]:
    """Dependencies a clean checkout would not contain.

    Tracked, not pinned: a clean checkout contains exactly the tracked files, so
    this is the precise predicate for "a clean checkout reproduces the declared
    configuration". It is deliberately weaker than the root gate's ``pinned``,
    because a local edit to a dependency does not stop a clean checkout from
    reproducing the run - it only makes the run's own bytes unpinned, which
    :func:`assert_harness_pinned` already refuses.
    """
    untracked: set[str] = set()
    for dependencies in harness_dependencies(repo_root).values():
        for relative in dependencies:
            if _git(repo_root, "ls-files", "--error-unmatch", relative) is None:
                untracked.add(relative)
    return tuple(sorted(untracked))


class HarnessDependencyNotTracked(RuntimeError):
    """A harness module imports a repo file that git does not have."""

    def __init__(self, files: tuple[str, ...]) -> None:
        self.files = files
        super().__init__(
            f"VOID: {len(files)} file(s) the harness imports are not tracked by git: "
            f"{', '.join(files)}. A clean checkout would not contain them, so this run could not "
            "be reproduced from the committed harness. Add them to HARNESS_FILES and commit, or "
            "stop importing them."
        )


def assert_clean_checkout_reproducible(repo_root: Path = REPO_ROOT) -> dict[str, tuple[str, ...]]:
    """Refuse a run whose harness imports something a clean checkout lacks."""
    missing = untracked_harness_dependencies(repo_root)
    if missing:
        raise HarnessDependencyNotTracked(missing)
    return harness_dependencies(repo_root)


def _limits() -> dict[str, Any]:
    """The security limits actually in force, read from the settings objects.

    Read from ``settings`` rather than from the shell script's ``export`` lines
    on purpose: the manifest must record what the SERVER ran with, and the two
    can diverge (a script typo, an env file, a stale export). A field that does
    not exist raises here, at preflight, instead of being quietly dropped from
    the artefact.
    """
    from src.app.core.config import settings

    budgets = settings.rate_limit.SECURITY_BUDGETS
    return {
        "algorithm": settings.rate_limit.RATE_LIMIT_ALGORITHM,
        "security_budgets": {name: list(pair) for name, pair in sorted(budgets.items())},
        "default_anonymous_limit": list(settings.rate_limit.DEFAULT_ANONYMOUS_LIMIT),
        "default_authenticated_limit": list(settings.rate_limit.DEFAULT_AUTHENTICATED_LIMIT),
        "auto_ban_enabled": settings.security.AUTO_BAN_ENABLED,
        "auto_ban_threshold": settings.security.AUTO_BAN_THRESHOLD,
        "auto_ban_violation_window": settings.security.AUTO_BAN_VIOLATION_WINDOW,
    }


def _stores() -> dict[str, str]:
    """Store addresses - host and port only. Never a credential.

    ``POSTGRES_USER`` and both Redis passwords are deliberately absent: the
    point of the field is "which store did this hit", and a manifest that leaks
    a password into a committed artefact would be traded for that.
    """
    from src.app.core.config import settings

    return {
        "postgres": f"{settings.POSTGRES_SERVER}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}",
        "redis_cache": f"{settings.REDIS_CACHE_HOST}:{settings.REDIS_CACHE_PORT}",
        "redis_queue": f"{settings.REDIS_QUEUE_HOST}:{settings.REDIS_QUEUE_PORT}",
    }


def _labels() -> list[str]:
    """Standing-rule labels that MUST travel with every number this harness emits.

    These are not commentary. A manifest without them permits the two failures
    the programme has already permanently rejected: reporting a rate-limit-exempt
    number unlabelled, and using a Windows number as a ``before`` arm.
    """
    from src.app.core.config import settings

    labels = [
        "SECURITY_LIMITS_RELAXED_BY_RUN_PERF_BASELINE",
        "non-representative: not a capacity measurement (shared runner, co-located stores)",
    ]
    if not settings.security.AUTO_BAN_ENABLED:
        labels.append("AUTO_BAN_ENABLED=false")
    if platform.system() != "Linux":
        labels.append("platform-not-Linux: NON-REPRESENTATIVE, may never serve as a `before` arm (A-5)")
    return labels


def build_manifest(*, run_params: dict[str, Any] | None = None, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Assemble the manifest. Raises if git cannot describe the tree."""
    head = _git(repo_root, "rev-parse", "HEAD")
    if head is None:
        raise RuntimeError(
            "VOID: cannot read HEAD. A perf artefact without a commit SHA cannot be "
            "attributed to an application, so this run must not produce one."
        )
    # assert_harness_pinned first: a manifest for a dirty harness would be a
    # faithful record of an unreproducible run, which is worse than no artefact.
    files = assert_harness_pinned(repo_root)
    # ...and then the dependency closure, which is the same argument one level
    # down: a clean checkout containing every ROOT is still not a clean checkout
    # that can run, if a harness module imports something the checkout lacks.
    dependencies = assert_clean_checkout_reproducible(repo_root)

    return {
        "schema": MANIFEST_SCHEMA,
        "git": {
            "sha": head,
            "short": head[:12],
            "branch": _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD"),
        },
        "harness": {
            "files": [asdict(f) for f in files],
            "dependencies": {rel: list(deps) for rel, deps in sorted(dependencies.items())},
        },
        "python": {
            "version": sys.version,
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "host": {
            "platform": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
        },
        "stores": _stores(),
        "limits": _limits(),
        "labels": _labels(),
        "run": dict(sorted((run_params or {}).items())),
    }


def write_manifest(manifest: dict[str, Any], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_manifest", description=__doc__)
    parser.add_argument("--output", type=Path, help="write the manifest JSON here; omit to only gate")
    parser.add_argument("--users", default=None, help="recorded in manifest.run")
    parser.add_argument("--rate", default=None, help="recorded in manifest.run")
    parser.add_argument("--duration", default=None, help="recorded in manifest.run")
    parser.add_argument("--host", default=None, help="recorded in manifest.run")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help=(
            "the checkout to describe. Exists so a VOID can be demonstrated against a scratch "
            "repository: the refusal has to be decidable without the application importing, and "
            "the harness it gates is pinned or dirty in a working checkout by definition."
        ),
    )
    args = parser.parse_args(argv)

    params = {"users": args.users, "rate": args.rate, "duration": args.duration, "host": args.host}
    params = {k: v for k, v in params.items() if v is not None}
    try:
        manifest = build_manifest(run_params=params, repo_root=args.repo_root)
    except HarnessNotPinned as exc:
        for f in exc.files:
            print(f"  {f.reason:9s} {f.path}", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 3
    except HarnessDependencyNotTracked as exc:
        for relative in exc.files:
            print(f"  untracked {relative}", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 5
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 4

    for f in manifest["harness"]["files"]:
        print(f"  pinned    {f['path']}  blob={f['blob_sha'][:12]}  last_commit={str(f['last_commit'])[:12]}")
    print(f"  app       {APP_ENTRYPOINT}  commit={manifest['git']['short']}")

    if args.output:
        path = write_manifest(manifest, args.output)
        print(f"  manifest  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
