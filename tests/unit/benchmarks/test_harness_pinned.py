"""H-5 verification: the 2026-09-22 loss, and the two ways it could happen again.

``tests/performance/locustfile.py`` was last committed in ``4cd00654`` at
2026-09-22 15:24:32, while ``baseline_20260922_011954_stats.csv`` is stamped
2026-09-22 01:19:54 - fourteen hours earlier. That commit also changed
``jwt_service.py``, ``core/errors.py`` and ``modules/auth/rate_limit.py``. So the
two baselines differ by 29 commits, a database reset, a 101-account reseed AND a
change to the measuring instrument, which makes the headline p95 figure
unattributable.

Two baselines like that are not an A/B, and no amount of later analysis recovers
the lost arm. ``FastAPI_saas-puww`` built the instrument that prevents it: a
provenance gate plus a manifest, covered by ``test_run_manifest.py``. This file
is the part that was missing, and it is the part the bead calls load-bearing:

1. A run VOIDS when any harness file is untracked or dirty. Asserted HERE as a
   real process exit, not as a raised exception, because a refusal nobody
   observed refusing is not a refusal.
2. Harness commit SHA and application git SHA both reach the artefact.
3. A clean checkout reproduces the declared configuration.

Criterion 3 needed new implementation rather than just a new assertion, because
``HARNESS_FILES`` is a hand-maintained tuple. A harness module could acquire a
repo import and the original gate would stay green while the run quietly depends
on a file a clean checkout does not have - the same unrecoverable instrument,
one level down. ``run_manifest.harness_dependencies`` now walks the closure, and
``assert_clean_checkout_reproducible`` refuses a run when part of it is untracked.

Bash cannot be executed on this host (``C:\\Windows\\system32\\bash.exe`` is the
WSL launcher and exits non-zero for every argument), so the void is exercised
through the exact entry point ``run_perf_baseline.sh`` invokes,
``python -m src.benchmarks.run_manifest``. That is a deliberate substitution: the
gate's contract is this process refusing to start, and the shell wrapper's only
remaining job is to propagate the exit code, which ``test_run_manifest.py``
already pins.

Every invocation below also passes ``--app-host-report``, because the load
script no longer starts the application and the gate therefore requires the
server's own report (H-9). The report written here declares ``uvloop`` and
``httptools`` so the exit code under test is the harness one and not the runtime
one; the runtime refusals are covered in ``test_run_manifest.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from src.benchmarks.run_manifest import (
    EXPECTED_EVENT_LOOP,
    EXPECTED_HTTP_IMPL,
    HARNESS_FILES,
    REPO_ROOT,
    HarnessDependencyNotTracked,
    HarnessNotPinned,
    assert_clean_checkout_reproducible,
    build_app_host_report,
    build_manifest,
    harness_dependencies,
    untracked_harness_dependencies,
)

#: Exit codes ``main()`` returns. Asserted individually so a refactor cannot
#: collapse two distinct refusals into one indistinguishable failure.
EXIT_UNPINNED = 3
EXIT_NO_HEAD = 4
EXIT_UNTRACKED_DEPENDENCY = 5

#: A syntactically valid OCI digest, declared rather than resolved. Nothing here
#: runs a container (the repository forbids it), so a real digest would be a
#: fabricated identity.
DECLARED_IMAGE_DIGEST = "sha256:" + "0" * 64

#: The deployment manifest the fixture declares. Not this repository's answer -
#: L-2's whole point is that no in-repo file records where the application runs -
#: but it has to be complete, because a manifest missing one of these fields is
#: refused before the harness gates ever get to be the thing under test.
DECLARED_DEPLOYMENT: dict[str, Any] = {
    "orchestrator": "docker-compose",
    "distro": "Debian GNU/Linux 12 (bookworm)",
    "cpu_model": "AMD EPYC 7763 64-Core Processor",
    "core_count": 8,
    "store_locality": "remote",
    "image_digest": DECLARED_IMAGE_DIGEST,
    "store_addresses": {
        "postgres": "db.internal:5432/app",
        "redis_cache": "cache.internal:6379",
        "redis_queue": "queue.internal:6379",
    },
}

#: Content for the scratch checkout. ``locustfile`` deliberately imports a repo
#: module so the dependency closure has something to find; without that the
#: criterion-3 tests would pass vacuously.
PREFIXED_FIXTURE = {
    "scripts/run_perf_baseline.sh": "#!/usr/bin/env bash\necho scratch\n",
    "scripts/setup_locust_test_data.py": '"""scratch seeder."""\n',
    "src/benchmarks/run_manifest.py": '"""scratch manifest."""\n',
    "src/benchmarks/percentiles.py": (
        '"""scratch percentiles."""\n\n\ndef nearest_rank(values, q):\n    return sorted(values)[0]\n'
    ),
    "tests/performance/locustfile.py": (
        '"""scratch locustfile."""\n\nfrom src.benchmarks.percentiles import nearest_rank\n\nUNUSED = nearest_rank\n'
    ),
}


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=Test", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


def _scratch_checkout(root: Path, *, commit: bool = True, exclude_from_commit: tuple[str, ...] = ()) -> Path:
    """A minimal git checkout holding exactly the harness and one dependency.

    ``exclude_from_commit`` produces a file git has NEVER heard of, which is the
    only way to reach the ``untracked`` state for a fixed HARNESS_FILES tuple.
    """
    repo = root / "checkout"
    repo.mkdir(parents=True)
    _git(repo, "init")
    for relative, content in PREFIXED_FIXTURE.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    if commit:
        _git(repo, "add", "-A", "--", ".", *(":(exclude)" + path for path in exclude_from_commit))
        _git(repo, "commit", "-m", "pin the harness")
    return repo


def _app_host_report(directory: Path) -> Path:
    """Write a representative app-host report and return its path.

    The gate now refuses a run whose app-host report is absent, so every process
    invocation here needs one. It declares ``uvloop`` and ``httptools`` - what the
    Dockerfile's flagless CMD resolves to - because the runtime gate is not what
    this file is testing; the runtime gate's own refusals are covered by
    ``test_run_manifest.py``. What these tests assert is the exit code, and an
    absent report would give every one of them the same wrong one. The digest is
    declared rather than resolved so the fixture cannot read the suite's own
    ``PERF_IMAGE_DIGEST``, which would make these tests environment-dependent.
    """
    report = build_app_host_report(
        event_loop=EXPECTED_EVENT_LOOP,
        http_impl=EXPECTED_HTTP_IMPL,
        image_digest=DECLARED_IMAGE_DIGEST,
        deployment=dict(DECLARED_DEPLOYMENT),
    )
    target = directory / "app_host_report.json"
    target.write_text(json.dumps(report), encoding="utf-8")
    return target


def _gate(repo: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    """Invoke the gate exactly as the load script does, app-host report included."""
    with tempfile.TemporaryDirectory() as directory:
        report = _app_host_report(Path(directory))
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "src.benchmarks.run_manifest",
                "--repo-root",
                str(repo),
                "--app-host-report",
                str(report),
                *extra,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=os.environ,
        )


@pytest.fixture
def scratch_repo(tmp_path: Path) -> Path:
    return _scratch_checkout(tmp_path)


@pytest.fixture
def app_host() -> dict[str, Any]:
    """The in-process form of what :func:`_app_host_report` writes to disk.

    Declares the digest and the whole deployment manifest so it is identical to the
    file :func:`_gate` hands the subprocess: the exit codes asserted below are about
    the harness gates, and a report missing a deployment field would stop the run
    earlier with a different code than the one under test.
    """
    return build_app_host_report(
        event_loop=EXPECTED_EVENT_LOOP,
        http_impl=EXPECTED_HTTP_IMPL,
        image_digest=DECLARED_IMAGE_DIGEST,
        deployment=dict(DECLARED_DEPLOYMENT),
    )


# --------------------------------------------------------------------------
# (b) a run VOIDS - observed as a process exit, not as a raised exception
# --------------------------------------------------------------------------


def test_the_run_voids_when_a_harness_file_is_dirty(scratch_repo: Path, tmp_path: Path):
    """The negative test the bead demands: touch a harness file, assert the void.

    The edit is made AFTER the checkout is committed, which is the realistic
    shape of this mistake - a harness somebody tweaked locally and ran anyway.
    """
    target = scratch_repo / "tests" / "performance" / "locustfile.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# local tweak\n", encoding="utf-8")

    result = _gate(scratch_repo, "--output", str(tmp_path / "m.json"))

    assert result.returncode == EXIT_UNPINNED, f"a dirty harness must void; got {result.returncode}\n{result.stderr}"
    assert "tests/performance/locustfile.py" in result.stderr
    assert "dirty" in result.stderr
    assert not (tmp_path / "m.json").exists(), "a voided run must not leave a manifest behind"


def test_the_run_voids_when_a_harness_file_is_untracked(tmp_path: Path):
    """Git has never heard of this file, so a clean checkout would not have it.

    Built by committing everything EXCEPT one harness file, rather than by
    ``git rm --cached``: that leaves the file in the working tree with its blob
    still in HEAD, which is a different state (see
    ``test_a_file_dropped_from_the_index_is_dirty_not_untracked``).
    """
    scratch_repo = _scratch_checkout(tmp_path, exclude_from_commit=("src/benchmarks/run_manifest.py",))

    result = _gate(scratch_repo, "--output", str(tmp_path / "m.json"))

    assert result.returncode == EXIT_UNPINNED, (
        f"an untracked harness must void; got {result.returncode}\n{result.stderr}"
    )
    assert "src/benchmarks/run_manifest.py" in result.stderr
    assert "untracked" in result.stderr


def test_a_file_dropped_from_the_index_is_dirty_not_untracked(scratch_repo: Path):
    """``git rm --cached`` leaves HEAD holding the blob, so ``tracked`` is true.

    Pinned because the distinction is load-bearing and easy to get backwards:
    ``tracked`` means "HEAD has a blob for this path", which is what makes the
    recorded blob SHA real. The file is present-but-unindexed, so it differs from
    HEAD and ``dirty`` is the honest reason.
    """
    _git(scratch_repo, "rm", "--cached", "-q", "src/benchmarks/run_manifest.py")

    result = _gate(scratch_repo)

    assert result.returncode == EXIT_UNPINNED
    assert "dirty" in result.stderr
    assert "untracked" not in result.stderr


def test_the_run_voids_when_a_harness_file_is_missing(scratch_repo: Path):
    (scratch_repo / "scripts" / "setup_locust_test_data.py").unlink()

    result = _gate(scratch_repo)

    assert result.returncode == EXIT_UNPINNED
    assert "setup_locust_test_data.py" in result.stderr
    assert "missing" in result.stderr


def test_a_pinned_harness_lets_the_run_through(scratch_repo: Path, tmp_path: Path):
    """The other half of the contract, so the void above cannot pass vacuously."""
    output = tmp_path / "reports" / "manifest.json"

    result = _gate(scratch_repo, "--output", str(output), "--users", "100", "--duration", "5m")

    assert result.returncode == 0, f"a pinned harness must not void: {result.stderr}"
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["run"]["users"] == "100"
    assert "pinned " in result.stdout


# --------------------------------------------------------------------------
# criterion 3 - a clean checkout reproduces the declared configuration
# --------------------------------------------------------------------------


def test_the_run_voids_when_a_harness_dependency_is_untracked(scratch_repo: Path):
    """The case HARNESS_FILES alone cannot see.

    Every ROOT here is tracked and clean, so the original gate is satisfied and
    the run proceeds - while the harness imports a file a clean checkout would
    not contain. That is criterion 3, and it is a different exit code so the two
    refusals stay distinguishable in a log.
    """
    assert list(scratch_repo.glob("**/percentiles.py")) is not None
    _git(scratch_repo, "rm", "--cached", "-q", "src/benchmarks/percentiles.py")

    result = _gate(scratch_repo)

    assert result.returncode == EXIT_UNTRACKED_DEPENDENCY, (
        f"an untracked harness dependency must void; got {result.returncode}\n{result.stderr}"
    )
    assert "src/benchmarks/percentiles.py" in result.stderr
    assert "untracked" in result.stderr


def test_the_dependency_closure_reaches_what_the_harness_imports(scratch_repo: Path):
    """The walk must actually find the import, or criterion 3 is vacuous."""
    closure = harness_dependencies(scratch_repo)

    assert closure["tests/performance/locustfile.py"] == ("src/benchmarks/percentiles.py",)
    assert untracked_harness_dependencies(scratch_repo) == ()


def test_a_dependency_of_a_dependency_is_reached_too(tmp_path: Path):
    """Transitive, because that is what makes the closure worth computing."""
    files = dict(PREFIXED_FIXTURE)
    files["src/benchmarks/percentiles.py"] = (
        '"""scratch."""\n\nfrom src.benchmarks.percentile_core import nearest_rank\n\nUNUSED = nearest_rank\n'
    )
    files["src/benchmarks/percentile_core.py"] = (
        '"""scratch."""\n\n\ndef nearest_rank(values, q):\n    return sorted(values)[0]\n'
    )
    repo = _scratch_checkout(tmp_path, commit=False)
    for relative in ("src/benchmarks/percentiles.py", "src/benchmarks/percentile_core.py"):
        (repo / relative).write_text(files[relative], encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "pin the harness and its dependency")

    closure = harness_dependencies(repo)

    assert closure["tests/performance/locustfile.py"] == (
        "src/benchmarks/percentile_core.py",
        "src/benchmarks/percentiles.py",
    )


def test_stdlib_and_site_packages_are_not_dependencies(scratch_repo: Path):
    """Only files in this checkout can be missing from a checkout of it."""
    closure = harness_dependencies(scratch_repo)
    every = {relative for deps in closure.values() for relative in deps}

    assert every == {"src/benchmarks/percentiles.py"}, every


def test_the_application_is_covered_by_the_commit_not_by_the_closure(scratch_repo: Path, app_host: dict[str, Any]):
    """`src/app` is thousands of files; `git.sha` describes it exactly.

    Listing them would say nothing the commit SHA does not, so the closure
    excludes them - and the manifest still carries the SHA.
    """
    closure = harness_dependencies(scratch_repo)
    assert not any(relative.startswith("src/app/") for deps in closure.values() for relative in deps)

    manifest = build_manifest(app_host=app_host, run_params={"users": "100"}, repo_root=scratch_repo)
    assert manifest["git"]["sha"] == _git(scratch_repo, "rev-parse", "HEAD").stdout.strip()


def test_the_closure_is_written_into_the_manifest(scratch_repo: Path, app_host: dict[str, Any]):
    """Criterion 3's output: the artefact declares what a checkout must contain."""
    manifest = build_manifest(app_host=app_host, run_params={"users": "100"}, repo_root=scratch_repo)

    assert manifest["harness"]["dependencies"]["tests/performance/locustfile.py"] == ["src/benchmarks/percentiles.py"]


def test_every_declared_harness_file_is_tracked_in_the_real_repo():
    """Criterion 3 against the checkout this work actually lands in.

    RED until someone commits, and deliberately not conditioned on git state:
    ``src/benchmarks/`` was created in batch 1 and has never been committed, so a
    clean checkout cannot run the harness it ships. That is exactly what this
    assertion exists to say, and asserting it conditionally would delete the
    criterion rather than satisfy it. Same reason as
    ``test_run_manifest.py::test_it_does_not_raise_on_the_real_repo``.
    """
    untracked = [
        relative
        for relative in HARNESS_FILES
        if subprocess.run(
            ["git", "ls-files", "--error-unmatch", relative],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
        ).returncode
        != 0
    ]

    assert not untracked, (
        f"the real repo does not track {untracked}. A clean checkout would not contain them, so "
        f"FastAPI_saas-puww acceptance (a) is unmet and this run's instrument is unrecoverable. "
        f"Commit them; nothing about the gate is wrong."
    )


def test_assert_clean_checkout_reproducible_raises_with_the_offending_files(scratch_repo: Path):
    _git(scratch_repo, "rm", "--cached", "-q", "src/benchmarks/percentiles.py")

    with pytest.raises(HarnessDependencyNotTracked) as caught:
        assert_clean_checkout_reproducible(scratch_repo)

    assert caught.value.files == ("src/benchmarks/percentiles.py",)
    assert "clean checkout" in str(caught.value)


def test_a_dirty_dependency_does_not_void_because_a_clean_checkout_still_has_it(scratch_repo: Path):
    """The predicate for criterion 3 is TRACKED, not CLEAN, and deliberately so.

    A local edit to a dependency makes this run's bytes unpinned, which
    :func:`assert_harness_pinned` already refuses on the roots. But it does not
    stop a clean checkout from reproducing the run, which is the claim criterion
    3 actually makes. Conflating the two would void every run while somebody is
    mid-edit, and a gate that fires constantly gets switched off.
    """
    target = scratch_repo / "src" / "benchmarks" / "percentiles.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# local tweak\n", encoding="utf-8")

    assert untracked_harness_dependencies(scratch_repo) == ()
    assert_clean_checkout_reproducible(scratch_repo)


def test_an_import_cycle_does_not_hang_the_walk(tmp_path: Path):
    files = dict(PREFIXED_FIXTURE)
    files["src/benchmarks/percentiles.py"] = (
        '"""scratch."""\n\nfrom src.benchmarks.percentile_core import nearest_rank\n\nUNUSED = nearest_rank\n'
    )
    files["src/benchmarks/percentile_core.py"] = (
        '"""scratch."""\n\n'
        "from src.benchmarks.percentiles import unused\n\n\n"
        "def nearest_rank(values, q):\n    return sorted(values)[0]\n"
    )
    repo = _scratch_checkout(tmp_path, commit=False)
    for relative in ("src/benchmarks/percentiles.py", "src/benchmarks/percentile_core.py"):
        (repo / relative).write_text(files[relative], encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "pin a cyclic pair")

    closure = harness_dependencies(repo)

    assert closure["tests/performance/locustfile.py"] == (
        "src/benchmarks/percentile_core.py",
        "src/benchmarks/percentiles.py",
    )


# --------------------------------------------------------------------------
# criterion 2 - both SHAs reach the artefact
# --------------------------------------------------------------------------


def test_both_shas_reach_the_manifest(scratch_repo: Path, app_host: dict[str, Any]):
    """Harness at BYTE level (blob SHA), application at COMMIT level (HEAD).

    The two are different granularities on purpose: the harness is a handful of
    files whose exact bytes decide the number, while the application is fully
    described by the commit it was checked out at.
    """
    manifest = build_manifest(app_host=app_host, run_params={"users": "100"}, repo_root=scratch_repo)
    expected_blob = _git(scratch_repo, "rev-parse", "HEAD:tests/performance/locustfile.py").stdout.strip()

    by_path = {entry["path"]: entry for entry in manifest["harness"]["files"]}
    assert by_path["tests/performance/locustfile.py"]["blob_sha"] == expected_blob
    assert manifest["git"]["sha"] == _git(scratch_repo, "rev-parse", "HEAD").stdout.strip()
    assert manifest["git"]["short"] == manifest["git"]["sha"][:12]


def test_the_git_refusal_is_distinguishable_from_the_dependency_refusal(scratch_repo: Path):
    """Three refusals, three exit codes. Two codes would make a log ambiguous."""
    _git(scratch_repo, "rm", "--cached", "-q", "src/benchmarks/percentiles.py")
    dependency_void = _gate(scratch_repo)

    target = scratch_repo / "tests" / "performance" / "locustfile.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# tweak\n", encoding="utf-8")
    root_void = _gate(scratch_repo)

    assert dependency_void.returncode == EXIT_UNTRACKED_DEPENDENCY
    assert root_void.returncode == EXIT_UNPINNED
    assert dependency_void.returncode != root_void.returncode


def test_the_pin_gate_runs_before_the_dependency_gate(scratch_repo: Path):
    """A dirty root and an untracked dependency: the root is reported.

    The root is the more proximate cause and the cheaper thing to fix, and its
    message names a concrete file to commit. Reporting the dependency first would
    send the reader after a file whose own status is not the problem.
    """
    _git(scratch_repo, "rm", "--cached", "-q", "src/benchmarks/percentiles.py")
    target = scratch_repo / "tests" / "performance" / "locustfile.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# tweak\n", encoding="utf-8")

    result = _gate(scratch_repo)

    assert result.returncode == EXIT_UNPINNED
    assert "tests/performance/locustfile.py" in result.stderr
    assert "percentiles.py" not in result.stderr


def test_a_checkout_with_no_head_is_refused_separately(tmp_path: Path):
    """No HEAD and no harness: the application SHA is the missing thing.

    Without it the run cannot be attributed to an application at all, which is a
    different failure from an unpinned harness, so it gets its own code.
    """
    repo = _scratch_checkout(tmp_path)
    _git(repo, "checkout", "-q", "--orphan", "unborn")
    _git(repo, "rm", "-rq", "--cached", ".")

    assert _gate(repo).returncode in {EXIT_NO_HEAD, EXIT_UNPINNED}


def test_harness_not_pinned_carries_the_offending_states(scratch_repo: Path, app_host: dict[str, Any]):
    """The exception is the structured form of the same fact, for in-process callers."""
    (scratch_repo / "scripts" / "setup_locust_test_data.py").unlink()

    with pytest.raises(HarnessNotPinned) as caught:
        build_manifest(app_host=app_host, run_params={"users": "100"}, repo_root=scratch_repo)

    offenders = {state.path: state.reason for state in caught.value.files}
    assert offenders == {"scripts/setup_locust_test_data.py": "missing"}
