"""P-9a verification: the harness must be pinned to git, or the run must VOID.

``run_perf_baseline.sh``'s instrument is only interpretable if it can be recovered
from git. The 2026-09-22 baseline is the proof that this is not automatic:
``locustfile.py`` was last committed fourteen hours AFTER the baseline it is
supposed to have produced, so the two baselines are not a same-instrument A/B.

Three things are pinned here, and they are different things:

1. :mod:`test_harness_state` - the gate's own logic, against REAL git repositories
   built in ``tmp_path``. Untracked, dirty, missing, and staged-only are each
   distinguished, because conflating any two of them turns a precise failure into
   a shrug.
2. :mod:`test_manifest_contents` - the manifest records the harness at BYTE level
   (blob SHA) and the app at COMMIT level. A blob SHA per file is what lets a
   reader tell whether two runs used the same instrument, which the commit SHA
   alone cannot: the harness can be edited without being committed.
3. :mod:`test_load_script_wiring` - the shell script really calls the gate, and
   really aborts on a non-zero exit. The script cannot be executed in this
   environment (no bash), so its wiring is pinned structurally - the same
   approach used for ``setup_logging``, which is also uncallable in tests
   because it reconfigures logging process-wide.

The git fixtures are real repositories rather than mocks on purpose: the whole
claim under test is about what git reports, and a mocked ``git status`` would
test the mock.
"""

from __future__ import annotations

import ast
import copy
import json
import os
import platform
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from src.app.core.config import settings
from src.benchmarks.run_manifest import (
    APP_ENTRYPOINT,
    APP_HOST_REPORT_FIELDS,
    APP_HOST_REPORT_SCHEMA,
    EXIT_DEPLOYMENT_UNDECLARED,
    EXIT_INCOMPLETE_MANIFEST,
    EXIT_NO_APP_HOST_REPORT,
    EXIT_NO_HEAD,
    EXIT_NON_REPRESENTATIVE,
    EXIT_PHASE_UNPROVEN,
    EXIT_RUN_END_MISMATCH,
    EXIT_UNPINNED,
    EXIT_UNTRACKED_DEPENDENCY,
    EXPECTED_EVENT_LOOP,
    EXPECTED_HTTP_IMPL,
    HARNESS_FILES,
    IMAGE_DIGEST_ENV,
    LOAD_SCRIPT,
    LOCUST_STATS_SUFFIX,
    MANIFEST_SCHEMA,
    MIN_HOLD_SECONDS,
    ORCHESTRATOR_ENV,
    OUTPUT_PATH_PREFIXES,
    PHASE_REPORT_SCHEMA,
    PHASES,
    REQUIRED_MANIFEST_FIELDS,
    STORE_LOCALITY_ENV,
    AppHostReportMissing,
    DeploymentUndeclared,
    HarnessFileState,
    HarnessNotPinned,
    ManifestIncomplete,
    NonRepresentativeRuntime,
    PhaseSamplesMissing,
    PhaseTimelineInvalid,
    RunEndMismatch,
    _address_host,
    assert_deployment_recorded,
    assert_harness_pinned,
    assert_manifest_complete,
    assert_phase_report_valid,
    assert_run_end_matches,
    build_app_host_report,
    build_manifest,
    build_phase_report,
    harness_state,
    harness_states,
    load_app_host_report,
    main,
    read_locust_phase,
    resolve_core_count,
    resolve_cpu_model,
    resolve_distro,
    resolve_event_loop,
    resolve_http_impl,
    resolve_image_digest,
    resolve_orchestrator,
    resolve_store_locality,
    unpinned_harness_files,
    write_manifest,
    write_phase_report,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "run_perf_baseline.sh"
DOCKERFILE = REPO_ROOT / "Dockerfile"

#: A syntactically valid OCI digest. Not a real image: nothing in this suite runs
#: a container (the repository forbids it), and a real digest recorded here would
#: be a fabricated identity, which is the exact defect the field exists to prevent.
DECLARED_IMAGE_DIGEST = "sha256:" + "0" * 64

#: The deployment manifest of addendum 6.1, as a fixture. Every value is a
#: plausible Linux deployment and NONE of them is this repository's answer: the
#: point of L-2 is that no in-repo file records where the application runs, so a
#: fixture built from the host's real values would assert nothing portable and
#: the suite's own answer must never be mistaken for the deployment's.
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


# --------------------------------------------------------------------------
# fixture: a real git repository holding a copy of the harness
# --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=Test", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def pinned_repo(tmp_path: Path) -> Path:
    """A git repo in which every HARNESS_FILES entry is committed and clean."""
    repo = tmp_path / "repo"
    for rel in HARNESS_FILES:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# harness: {rel}\n", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "pin the harness")
    return repo


@pytest.fixture
def app_host_report() -> dict[str, Any]:
    """An app-host report describing the runtime that SHIPS.

    The loop and HTTP implementation are injected rather than resolved, for two
    reasons. This host is Windows, where uvloop cannot be installed, so a report
    built from its real values would be refused by design and the test would assert
    nothing. And these are exactly the two fields the refusal turns on: a fixture
    that did not pin them would be asserting against whatever the machine running
    the suite happens to have, which is the non-portable test this programme has
    already been bitten by once. The digest is injected for the same reason, so
    the fixture does not read ``PERF_IMAGE_DIGEST`` out of whatever environment
    the suite happens to run in.
    """
    return build_app_host_report(
        event_loop=EXPECTED_EVENT_LOOP,
        http_impl=EXPECTED_HTTP_IMPL,
        image_digest=DECLARED_IMAGE_DIGEST,
        deployment=dict(DECLARED_DEPLOYMENT),
    )


def write_app_host_report(directory: Path, **overrides: Any) -> Path:
    """An app-host report on disk, which is the only form the load host ever sees."""
    report = build_app_host_report(
        event_loop=EXPECTED_EVENT_LOOP,
        http_impl=EXPECTED_HTTP_IMPL,
        image_digest=DECLARED_IMAGE_DIGEST,
        deployment=dict(DECLARED_DEPLOYMENT),
    )
    report.update(overrides)
    path = directory / "app_host_report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# (b) the gate distinguishes untracked / dirty / missing / staged
# --------------------------------------------------------------------------


class TestHarnessState:
    def test_a_committed_file_is_pinned(self, pinned_repo: Path):
        for state in harness_states(pinned_repo):
            assert state.pinned is True, f"{state.path}: {state.reason}"
            assert state.dirty is False
            assert state.tracked is True
            assert state.blob_sha is not None and len(state.blob_sha) == 40
            assert state.last_commit is not None and len(state.last_commit) == 40

    def test_a_file_git_has_never_seen_is_reported_untracked_not_dirty(self, pinned_repo: Path):
        """The distinction matters: 'you forgot to commit' is a different ask of
        the operator than 'you have local edits'.

        The path must be one git has never seen. Deleting and recreating a
        tracked path leaves it tracked, so a test built that way would be
        asserting nothing about untracked files at all.
        """
        new_rel = "scripts/harness_helper.py"
        (pinned_repo / new_rel).write_text("# local only\n", encoding="utf-8")
        state = harness_state(pinned_repo, new_rel)
        assert state.tracked is False
        assert state.blob_sha is None
        assert state.pinned is False
        assert state.reason == "untracked"

    def test_a_deleted_and_recreated_tracked_file_is_dirty_not_untracked(self, pinned_repo: Path):
        """The complement, and the mistake this test would otherwise have made:
        git tracks a path, not an inode, so recreating it does not untrack it."""
        target = pinned_repo / HARNESS_FILES[1]
        target.unlink()
        target.write_text("# local only\n", encoding="utf-8")
        state = harness_state(pinned_repo, HARNESS_FILES[1])
        assert state.tracked is True
        assert state.reason == "dirty", (
            "the committed blob still exists, so the ask is 'commit the edit', not 'add the file'"
        )

    def test_an_unstaged_edit_is_reported_dirty(self, pinned_repo: Path):
        (pinned_repo / HARNESS_FILES[1]).write_text("# edited in place\n", encoding="utf-8")
        state = harness_state(pinned_repo, HARNESS_FILES[1])
        assert state.tracked is True
        assert state.dirty is True
        assert state.blob_sha is not None, "a dirty file still has a committed blob; the SHA is the LAST COMMITTED one"
        assert state.reason == "dirty"

    def test_a_staged_but_uncommitted_edit_is_also_dirty(self, pinned_repo: Path):
        """Staging is not committing. Treating `git add` as pinning would be the
        same class of error as reading a key builder instead of the stored key."""
        target = pinned_repo / HARNESS_FILES[1]
        target.write_text("# staged\n", encoding="utf-8")
        _git(pinned_repo, "add", HARNESS_FILES[1])
        state = harness_state(pinned_repo, HARNESS_FILES[1])
        assert state.tracked is True
        assert state.dirty is True
        assert state.reason == "dirty"

    def test_a_committed_edit_is_clean_again(self, pinned_repo: Path):
        target = pinned_repo / HARNESS_FILES[1]
        before = harness_state(pinned_repo, HARNESS_FILES[1])
        target.write_text("# edited\n", encoding="utf-8")
        _git(pinned_repo, "add", HARNESS_FILES[1])
        _git(pinned_repo, "commit", "-m", "edit the harness")
        state = harness_state(pinned_repo, HARNESS_FILES[1])
        assert state.pinned is True
        assert state.reason == "clean"
        assert state.dirty is False
        # The recorded identity must follow the new commit, otherwise two runs
        # of different harnesses would carry the same manifest provenance.
        assert state.blob_sha != before.blob_sha
        assert state.last_commit != before.last_commit

    def test_a_missing_file_is_reported_missing(self, pinned_repo: Path):
        (pinned_repo / HARNESS_FILES[2]).unlink()
        state = harness_state(pinned_repo, HARNESS_FILES[2])
        assert state.exists is False
        assert state.pinned is False
        assert state.reason == "missing"

    def test_the_blob_sha_tracks_the_harness_bytes_not_the_commit(self, pinned_repo: Path):
        """Two runs of the SAME commit with a locally edited harness are not the
        same instrument. Only the blob SHA tells them apart."""
        before = harness_state(pinned_repo, HARNESS_FILES[1]).blob_sha
        (pinned_repo / HARNESS_FILES[1]).write_text("# uncommitted tweak\n", encoding="utf-8")
        after = harness_state(pinned_repo, HARNESS_FILES[1]).blob_sha
        assert after == before, "an uncommitted edit does not change HEAD's blob; that is the point"

    def test_a_changed_commit_changes_the_recorded_shas(self, pinned_repo: Path):
        head_before = _git(pinned_repo, "rev-parse", "HEAD").stdout.strip()
        (pinned_repo / HARNESS_FILES[1]).write_text("# a real change\n", encoding="utf-8")
        _git(pinned_repo, "add", "-A")
        _git(pinned_repo, "commit", "-m", "change the harness")
        head_after = _git(pinned_repo, "rev-parse", "HEAD").stdout.strip()
        assert head_before != head_after
        assert harness_state(pinned_repo, HARNESS_FILES[1]).blob_sha != harness_states(pinned_repo)[0].blob_sha


# --------------------------------------------------------------------------
# (b) the gate VOIDS
# --------------------------------------------------------------------------


class TestTheGateVoids:
    def test_it_returns_the_states_when_everything_is_pinned(self, pinned_repo: Path):
        assert len(assert_harness_pinned(pinned_repo)) == len(HARNESS_FILES)
        assert unpinned_harness_files(pinned_repo) == ()

    def test_it_raises_on_an_untracked_harness_file(self, pinned_repo: Path):
        (pinned_repo / HARNESS_FILES[0]).unlink()
        (pinned_repo / HARNESS_FILES[0]).write_text("# untracked rewrite\n", encoding="utf-8")
        with pytest.raises(HarnessNotPinned) as exc:
            assert_harness_pinned(pinned_repo)
        assert [f.path for f in exc.value.files] == [HARNESS_FILES[0]]

    def test_it_raises_on_a_dirty_harness_file(self, pinned_repo: Path):
        (pinned_repo / HARNESS_FILES[3]).write_text("# dirty\n", encoding="utf-8")
        with pytest.raises(HarnessNotPinned) as exc:
            assert_harness_pinned(pinned_repo)
        assert exc.value.files[0].reason == "dirty"

    def test_it_reports_every_offender_not_just_the_first(self, pinned_repo: Path):
        (pinned_repo / HARNESS_FILES[0]).write_text("# dirty\n", encoding="utf-8")
        (pinned_repo / HARNESS_FILES[2]).unlink()
        with pytest.raises(HarnessNotPinned) as exc:
            assert_harness_pinned(pinned_repo)
        assert len(exc.value.files) == 2

    def test_the_message_states_the_consequence_not_just_the_fact(self, pinned_repo: Path):
        """A VOID the operator cannot act on gets worked around, which is worse
        than a missing gate. The message must say what to do and why it matters."""
        (pinned_repo / HARNESS_FILES[1]).unlink()
        with pytest.raises(HarnessNotPinned) as exc:
            assert_harness_pinned(pinned_repo)
        message = str(exc.value)
        assert "missing" in message
        assert "re-run" in message and "checked" in message
        assert "Commit the harness" in message

    def test_it_does_not_raise_on_the_real_repo(self):
        """Criterion (a) against the repository as it stands.

        RED while this work is uncommitted: both harness files are ``dirty`` until
        the change lands, so the gate is correct to refuse. It is deliberately not
        conditioned on the git state - a test that passes either way would not be
        testing (a).
        """
        assert unpinned_harness_files(REPO_ROOT) == (), (
            f"unpinned harness files in the real repo: "
            f"{[(f.path, f.reason) for f in unpinned_harness_files(REPO_ROOT)]}. "
            f"Commit them; the load script will refuse to run until you do."
        )


# --------------------------------------------------------------------------
# (c) the manifest records harness bytes and the app commit
# --------------------------------------------------------------------------


class TestManifestContents:
    @pytest.fixture
    def manifest(self, pinned_repo: Path, app_host_report):
        return build_manifest(
            app_host=app_host_report,
            run_params={"users": "100", "host": "http://app-host.internal:8000"},
            repo_root=pinned_repo,
        )

    def test_it_declares_its_schema(self, manifest):
        assert manifest["schema"] == MANIFEST_SCHEMA

    def test_it_records_the_app_commit_sha(self, manifest, pinned_repo: Path):
        head = _git(pinned_repo, "rev-parse", "HEAD").stdout.strip()
        assert manifest["git"]["sha"] == head
        assert manifest["git"]["short"] == head[:12]

    def test_it_records_a_blob_sha_and_a_last_commit_for_every_harness_file(self, manifest):
        recorded = {f["path"]: f for f in manifest["harness"]["files"]}
        assert set(recorded) == set(HARNESS_FILES), "every executed file must appear, not just the interesting ones"
        for path, entry in recorded.items():
            assert entry["blob_sha"] is not None, f"{path} has no recorded blob SHA"
            assert len(entry["blob_sha"]) == 40
            assert entry["last_commit"] is not None, f"{path} has no recorded last-touched commit"

    def test_the_recorded_blob_sha_matches_git(self, manifest, pinned_repo: Path):
        for entry in manifest["harness"]["files"]:
            assert entry["blob_sha"] == harness_state(pinned_repo, entry["path"]).blob_sha

    def test_it_records_the_run_parameters(self, manifest):
        assert manifest["run"] == {"host": "http://app-host.internal:8000", "users": "100"}

    def test_it_records_every_security_budget_not_a_summary(self, manifest):
        """P-9a asks for ALL budgets. A summary is how two runs with different
        rate limiting get compared."""
        recorded = manifest["limits"]["security_budgets"]
        expected = {name: list(pair) for name, pair in sorted(settings.rate_limit.SECURITY_BUDGETS.items())}
        assert recorded == expected
        assert "auth" in recorded, "the budget a login spends on every unauthenticated attempt must be present"

    def test_it_records_the_algorithm_and_the_both_default_limits(self, manifest):
        assert manifest["limits"]["algorithm"] == settings.rate_limit.RATE_LIMIT_ALGORITHM
        assert manifest["limits"]["default_anonymous_limit"] == list(settings.rate_limit.DEFAULT_ANONYMOUS_LIMIT)
        assert manifest["limits"]["default_authenticated_limit"] == list(
            settings.rate_limit.DEFAULT_AUTHENTICATED_LIMIT
        )

    def test_it_records_the_runtime_fingerprint(self, manifest):
        import platform
        import sys

        assert manifest["python"]["version"] == sys.version
        assert manifest["python"]["implementation"] == platform.python_implementation()
        assert manifest["load_host"]["platform"] == platform.system()
        assert isinstance(manifest["load_host"]["cpu_count"], int)

    def test_it_labels_the_load_host_and_the_app_host_separately(self, manifest, app_host_report):
        """One ``host`` key could not say which machine it described, and the
        whole 2026-09-22 pack was mislabelled by exactly that ambiguity."""
        import platform

        assert "load_host" in manifest
        assert "app_host" in manifest
        assert manifest["load_host"]["platform"] == platform.system()
        assert manifest["app_host"]["event_loop"] == EXPECTED_EVENT_LOOP
        assert manifest["app_host"]["http"] == EXPECTED_HTTP_IMPL
        assert manifest["limits"] == app_host_report["limits"], "the limits recorded must be the SERVER's"
        assert manifest["stores"] == app_host_report["stores"], "the stores recorded must be the SERVER's"

    def test_it_records_store_addresses(self, manifest):
        stores = manifest["stores"]
        assert set(stores) == {"postgres", "redis_cache", "redis_queue"}
        assert str(settings.POSTGRES_SERVER) in stores["postgres"]
        assert str(settings.POSTGRES_DB) in stores["postgres"]

    def test_it_records_no_credential(self, manifest):
        """A manifest is an artefact that gets committed and shared. A password in
        it is a worse defect than the missing SHA it was added to fix."""
        blob = json.dumps(manifest)
        for secret_name in ("POSTGRES_PASSWORD", "REDIS_CACHE_PASSWORD", "REDIS_QUEUE_PASSWORD", "SECRET_KEY"):
            assert secret_name not in blob
        password = settings.POSTGRES_PASSWORD
        if password is not None:
            assert password.get_secret_value() not in blob

    def test_it_labels_a_run_with_auto_ban_disabled(self, pinned_repo: Path, app_host_report):
        """Unified plan 4.2: a number produced with ``AUTO_BAN_ENABLED=false`` may
        not be reported unlabelled, so the label has to travel in the artefact.

        The value comes from the app-host report - the SERVER's flag - not from this
        process's settings. Reading the load host's flag would produce a manifest
        that is confident and about the wrong machine.
        """
        app_host_report["limits"]["auto_ban_enabled"] = False
        labels = build_manifest(app_host=app_host_report, run_params={"users": "100"}, repo_root=pinned_repo)["labels"]
        assert "AUTO_BAN_ENABLED=false" in labels

    def test_it_omits_the_auto_ban_label_when_the_server_has_auto_ban_on(
        self, pinned_repo: Path, app_host_report
    ):
        app_host_report["limits"]["auto_ban_enabled"] = True
        labels = build_manifest(app_host=app_host_report, run_params={"users": "100"}, repo_root=pinned_repo)["labels"]
        assert "AUTO_BAN_ENABLED=false" not in labels

    def test_it_labels_a_non_linux_load_host_as_non_representative(
        self, pinned_repo: Path, app_host_report, monkeypatch
    ):
        """A-5: a Windows number may never serve as a `before` arm. The label must
        be in the artefact, not in a reviewer's memory."""
        import src.benchmarks.run_manifest as mod

        monkeypatch.setattr(mod.platform, "system", lambda: "Windows")
        labels = build_manifest(app_host=app_host_report, run_params={"users": "100"}, repo_root=pinned_repo)["labels"]
        assert any("load-host-not-Linux" in label and "NON-REPRESENTATIVE" in label for label in labels)

    def test_it_labels_a_non_linux_app_host_as_non_representative(self, pinned_repo: Path, app_host_report):
        """The server's platform matters as much as the load generator's: a Linux
        load host driving a Windows server is not the shipped topology either."""
        app_host_report["host"]["platform"] = "Windows"
        labels = build_manifest(app_host=app_host_report, run_params={"users": "100"}, repo_root=pinned_repo)["labels"]
        assert any("app-host-not-Windows" in label and "NON-REPRESENTATIVE" in label for label in labels)

    def test_it_always_labels_the_shared_runner_and_the_two_host_topology(self, manifest):
        assert any("not a capacity measurement" in label for label in manifest["labels"])
        assert "topology: load generator and application on separate hosts" in manifest["labels"]

    def test_it_does_not_claim_the_runner_relaxed_anything(self, manifest):
        """The old script exported ``AUTO_BAN_ENABLED=false`` and relaxed budgets
        while no report disclosed it. The new one exports nothing, so carrying the
        old label forward would put a false statement in every artefact - which is
        the same defect the label was invented to stop, pointed the other way."""
        assert "SECURITY_LIMITS_RELAXED_BY_RUN_PERF_BASELINE" not in manifest["labels"]

    def test_it_refuses_to_build_when_the_harness_is_dirty(self, pinned_repo: Path, app_host_report):
        """A faithful record of an unreproducible run is worse than no artefact:
        it looks complete."""
        (pinned_repo / HARNESS_FILES[1]).write_text("# dirty\n", encoding="utf-8")
        with pytest.raises(HarnessNotPinned):
            build_manifest(app_host=app_host_report, run_params={"users": "100"}, repo_root=pinned_repo)

    def test_it_refuses_to_build_without_a_readable_head(self, pinned_repo: Path, app_host_report):
        _git(pinned_repo, "checkout", "--orphan", "no-commits-yet")
        _git(pinned_repo, "rm", "-rq", "--cached", ".")
        for rel in HARNESS_FILES:
            (pinned_repo / rel).unlink()
        with pytest.raises((HarnessNotPinned, RuntimeError)):
            build_manifest(app_host=app_host_report, run_params={"users": "100"}, repo_root=pinned_repo)

    def test_it_writes_json_that_round_trips(self, manifest, tmp_path: Path):
        out = write_manifest(manifest, tmp_path / "nested" / "m.json")
        assert out.exists()
        assert json.loads(out.read_text(encoding="utf-8")) == manifest


# --------------------------------------------------------------------------
# (d) the script invokes the gate and honours its exit code
# --------------------------------------------------------------------------


def _script_body() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _code_lines() -> list[str]:
    """Non-comment, non-blank lines. Comment-only lines still teach an operator
    that a file is used, which is not the same as the script executing it, so the
    path guard below deliberately looks only at code."""
    return [line.strip() for line in _script_body().splitlines() if line.strip() and not line.strip().startswith("#")]


def _script_code() -> str:
    """The script with its comment header removed.

    The header deliberately names the behaviour the rewrite REMOVED - the
    co-located uvicorn, the relaxed exports - because an operator who remembers
    the old runner needs to be told what changed. Assertions about what the
    script DOES must therefore read the code, or they would match the prose
    describing the defect.
    """
    return "\n".join(_code_lines())


def _module_refs_to_paths() -> set[str]:
    """Every repo file the script can reach, from two shapes.

    A path literal (``scripts/setup_locust_test_data.py``) and a module reference
    (``-m src.benchmarks.run_manifest``, ``src.app.main:app``). Handling both is
    required: ``src/benchmarks/run_manifest.py`` is reachable ONLY as a module
    reference, so a path-literal-only scan would silently drop the very file that
    writes the manifest from the pinned set.

    Three exclusions, each for a reason that would otherwise make this a
    generator of noise the reader must filter mentally:

    - Comments. ``_code_lines()``: prose that mentions ``locustfile.py`` is not
      the script executing it, and a mention is exactly how a stale reference
      lingers in a header forever.
    - Anything outside the checkout. ``-m uvicorn`` and ``-m locust`` are
      third-party console entry points, and ``-m uvicorn`` resolves to
      ``uvicorn.py`` at the repository root, which does not exist. Requiring the
      resolved file to exist under ``REPO_ROOT`` drops them on the evidence
      rather than on a blocklist that would rot.
    - Declared outputs. The run writes ``reports/baseline_<stamp>_manifest.json``;
      an output is not an instrument, and pinning it would mean the gate can
      never pass on a clean tree.
    """
    text = re.sub(r"\$\{[^}]*\}", "", "\n".join(_code_lines()))  # drop variable expansions
    found: set[str] = set()

    for match in re.finditer(r"(?:-m\s+([a-z_][a-z0-9_.]*)|([a-z_][a-z0-9_]*\.app\.main):app)", text):
        dotted = match.group(1) or match.group(2)
        found.add(dotted.replace(".", "/") + ".py")

    # Bare path literals with a source/config extension. .exe is excluded on
    # purpose: .venv interpreter probes are not harness files.
    for match in re.finditer(r"[A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:py|sh|json|toml|lock)\b", text):
        found.add(match.group(0))

    resolved = set()
    for rel in found:
        if any(rel.startswith(prefix) for prefix in OUTPUT_PATH_PREFIXES):
            continue
        if (REPO_ROOT / rel).is_file():
            resolved.add(rel)
    return resolved


class TestTheReachabilityScanItself:
    """The drift guard is only as good as the scan behind it, and a scan that
    silently returns nothing passes every downstream assertion."""

    def test_it_finds_each_pinned_file_by_both_reachable_shapes(self):
        found = _module_refs_to_paths()
        # LOAD_SCRIPT is exempt because a script cannot name itself: it reaches
        # itself by being invoked, not by a literal in its own body. Requiring it
        # of the scan would mean requiring a self-referential path that has no
        # meaning, and then exempting it again at every use.
        for rel in HARNESS_FILES:
            if rel == LOAD_SCRIPT:
                continue
            assert rel in found, f"{rel} is pinned but the scan cannot see it, so the drift guard proves nothing"

    def test_it_ignores_third_party_entry_points(self):
        """`-m uvicorn` must not be reported as the repo file `uvicorn.py`."""
        found = _module_refs_to_paths()
        assert "uvicorn.py" not in found
        assert "locust.py" not in found
        assert not any(rel.startswith("locust") and "/" not in rel for rel in found)

    def test_it_ignores_outputs_and_comments(self):
        found = _module_refs_to_paths()
        assert not any(rel.endswith("_manifest.json") for rel in found)
        # The bound is derived, not a magic number: every pinned file the script
        # can name must be visible, and LOAD_SCRIPT is exempt because a script
        # reaches itself by being invoked.
        reachable_pinned_files = len(HARNESS_FILES) - 1
        assert len(found) >= reachable_pinned_files, (
            f"the scan found {sorted(found)}, fewer than the {reachable_pinned_files} pinned files "
            f"the script can reach, so a drift guard built on it would prove nothing"
        )


class TestLoadScriptWiring:
    def test_it_runs_the_manifest_gate(self):
        assert "-m src.benchmarks.run_manifest" in _script_body()

    def test_it_captures_the_gate_exit_code_and_aborts_on_it(self):
        """`set -e` alone would abort, but silently and without the VOID banner.
        The explicit capture is what makes the failure legible in the log."""
        body = _script_body()
        assert "PROVENANCE_RC=$?" in body
        assert re.search(r'if \[\[ "\$\{PROVENANCE_RC\}" -ne 0 \]\]; then', body)
        assert 'exit "${PROVENANCE_RC}"' in body

    def test_it_writes_the_void_banner_to_the_log(self):
        body = _script_body()
        void_block = re.search(r'if \[\[ "\$\{PROVENANCE_RC\}" -ne 0 \]\]; then(.*?)\n\s*fi', body, re.DOTALL)
        assert void_block is not None, "the abort branch is gone"
        assert "VOID" in void_block.group(1)
        assert 'tee -a "${LOG}"' in void_block.group(1), "the VOID must reach the artefact log, not just the console"

    def test_the_gate_runs_before_any_load_is_generated(self):
        """A gate after the load test has measured nothing is decoration.

        The boundary used to be "before the server starts", which was the right
        order for a script that started one. The script no longer starts anything,
        so the boundary is the first moment the system under test is exercised.
        """
        body = _script_body()
        first_locust = body.index("-m locust")
        assert body.index("src.benchmarks.run_manifest") < body.index("setup_locust_test_data.py")
        assert body.index("src.benchmarks.run_manifest") < first_locust

    def test_it_gates_both_ends_of_the_run(self):
        """A start record alone cannot notice a commit or a limit that moved while
        the numbers were being collected; the end record is what checks it."""
        body = _script_body()
        assert "--phase start" in body
        assert "--phase end" in body
        assert "--assert-matches" in body
        assert body.index("--phase start") < body.index("--phase end")

    def test_it_never_starts_the_server(self):
        """Criterion (a) is structural, not a promise.

        The script refuses to measure a server on this host. That refusal is only
        credible if no configuration of the script can start one here, because a
        refusal an operator can route around by passing a different flag is not a
        gate - it is a suggestion."""
        body = _script_code()
        for forbidden, why in (
            ("uvicorn", "starting the server is what this script no longer does"),
            ("src.app.main:app", "the app entrypoint is the server, and this script does not start it"),
            ("SERVER_PID", "there is no server process to be reaped"),
            ("nohup", "a detached server would still be a same-host measurement"),
        ):
            assert forbidden not in body, f"{forbidden!r} is back in the script: {why}"

    def test_it_refuses_a_target_on_this_host(self):
        """The refusal has to cover what a same-host target actually looks like:
        loopback in several spellings, this host's own names, and any address bound
        on this machine - which is the case an operator who typed their own LAN IP
        would hit and not expect."""
        body = _script_body()
        for spelling in ("localhost", "127.*", "0.0.0.0", "::1", "*.localhost"):
            assert spelling in body, f"{spelling!r} is no longer refused as a local target"
        assert "hostname -s" in body and "hostname -f" in body, "this host's own short and FQDN must be refused"
        assert "ip -o addr show" in body, "an address bound on this host must be refused even under a name"
        assert "--host is required" in body, "there must be no default target at all"

    def test_the_interpreter_probe_prefers_the_posix_layout(self):
        """The old script tested ``.venv/Scripts/python.exe`` FIRST, which is how
        the pack's e2e baselines ended up being produced on Windows (PF-7), where
        uvloop cannot be installed and no number is representative (A-5)."""
        body = _script_body()
        assert body.index(".venv/bin/python") < body.index(".venv/Scripts/python.exe")

    def test_it_overrides_no_security_limit(self):
        """Criterion (c) in its strongest form: production budgets are in force by
        default because the script exports nothing at all, so there is no
        ``AUTO_BAN_ENABLED=false`` for the artefact to have to disclose."""
        body = _script_code()
        for forbidden in (
            "export AUTO_BAN_ENABLED",
            "export SECURITY_BUDGETS",
            "DEFAULT_ANONYMOUS_LIMIT=",
            "DEFAULT_AUTHENTICATED_LIMIT=",
        ):
            assert forbidden not in body, f"{forbidden!r} is back: the runner must not change the server's limits"

    def test_it_runs_three_phases_and_measures_the_third(self):
        """P-5 needs the cold figure and the warm figure SEPARATELY, and the hold
        between them.

        The first locust run measures a server whose caches are cold (perf finding
        F5: a cold first request pays for auth and tenant cache misses that
        production pays once). The second holds at target concurrency - the
        concurrency that populates what 100 users populate, so a lower-concurrency
        hold would warm less - and its samples are discarded. Only the third is the
        baseline, and only the third carries ``--reset-stats``.
        """
        body = _script_code()
        cold_call = 'run_locust "${CSV}_cold" "${COLD_WINDOW}"'
        hold_call = 'run_locust "${CSV}_hold" "${HOLD}"'
        measured_call = 'run_locust "${CSV}" "${DURATION}" "--reset-stats"'
        for call in (cold_call, hold_call, measured_call):
            assert call in body, f"the {call!r} invocation is gone; the three phases must each run"
        assert body.index(cold_call) < body.index(hold_call) < body.index(measured_call), (
            "cold, then the hold, then the measured window: in that order or the cold figure is warmed"
        )
        assert "run_locust \"${CSV}_hold\" \"${HOLD}\" --reset-stats" not in body, (
            "resetting the hold's stats would be pointless - they are discarded - and reading it as a "
            "signal that the hold was measured is exactly the confusion P-5 ends"
        )
        assert '_cold' in body and "never instead of it" in body, (
            "the cold window's own numbers must be kept and named, or the inflation stops being measurable"
        )

    def test_the_hold_floor_is_read_from_the_harness_not_written_here(self):
        """Criterion (b): the floor is a declared constant next to the rule.

        A literal ``HOLD=60s`` at the call site would be a second copy of a number
        already written down once, and the copy is the one that would drift."""
        body = _script_code()
        assert "--print-min-hold-seconds" in body, "the runner must read P-5's floor from run_manifest.py"
        assert re.search(r'HOLD="\$\{HOLD:-\$\{HOLD_FLOOR\}s\}"', body), "the default hold must be that floor"
        floor_call = r'HOLD_FLOOR="\$\("\$\{PY\}" -m src\.benchmarks\.run_manifest --print-min-hold-seconds\)"'
        assert re.search(floor_call, body), "the floor must be read from the module, not written here"
        assert "HOLD=60s" not in body and "HOLD=1m" not in body, (
            "the floor must not be retyped at the call site; that is the literal the rule exists to prevent"
        )

    def test_a_hold_shorter_than_the_floor_is_refused_not_clamped(self):
        """Clamping would silently report a number taken before steady state, which
        is the failure P-5 exists to stop - with no marker that it happened."""
        body = _script_code()
        assert 'require_at_least "hold" "${HOLD}" "${HOLD_FLOOR}"' in body
        assert 'require_at_least "cold-window" "${COLD_WINDOW}" "${MIN_COLD_SECONDS}"' in body
        assert '-lt "${floor}"' in body, "the comparison against the floor must exist, not a default"
        helper = re.search(r"require_at_least\(\).*?\n\}", body, re.DOTALL)
        assert helper is not None, "the floor helper is gone"
        assert "refuse" in helper.group(0), "the helper must be able to refuse"

    def test_it_emits_the_phase_report_after_the_run_end_gate(self):
        """The cold/warm report describes a run the provenance gate has already
        accepted, and it must not be the thing that ran anyway."""
        body = _script_code()
        assert "--emit-phase-report" in body
        for flag in ("--manifest", "--cold-csv", "--hold-csv", "--warm-csv", "--output"):
            assert flag in body, f"{flag} is not forwarded, so the phase report cannot be built"
        assert body.index("--phase end") < body.index("--emit-phase-report")
        assert 'PHASES="reports/baseline_${STAMP}_phases.json"' in body

    def test_it_keeps_no_warmup_flag(self):
        """``--warmup`` described two phases; P-5 needs three, and a surviving
        alias would let an operator set the hold to a value the floor refuses."""
        body = _script_code()
        assert "--warmup" not in body
        assert "WARMUP" not in body

    def test_it_passes_the_run_parameters_into_the_manifest(self):
        body = _script_body()
        for flag in ("--users", "--rate", "--duration", "--host", "--output"):
            assert flag in body, f"{flag} is not forwarded, so manifest.run would be incomplete"

    def test_the_help_text_is_derived_not_a_magic_line_range(self):
        """It used to be `sed -n '2,30p'`, which truncated the header and then
        printed `set -euo pipefail` into the help output."""
        assert "awk" in _script_body()
        assert not re.search(r"sed -n '\d+,\d+p'", _script_body())

    def test_every_path_it_reaches_is_classified(self):
        """The drift guard: a harness file added to the script without being added
        to HARNESS_FILES would execute unpinned, and no test would notice."""
        unclassified = []
        for path in sorted(_module_refs_to_paths()):
            if path in HARNESS_FILES or path == APP_ENTRYPOINT.replace(":app", ".py"):
                continue
            if any(path.startswith(prefix) for prefix in OUTPUT_PATH_PREFIXES):
                continue
            unclassified.append(path)
        assert unclassified == [], (
            f"the load script reaches {unclassified}, which is neither in HARNESS_FILES, "
            f"the app entrypoint, nor a declared output. A file it executes must be pinned."
        )

    def test_every_pinned_file_is_actually_referenced_by_the_script(self):
        """The reverse direction: a pinned entry the script no longer runs is a
        maintenance tax and hides which files matter."""
        referenced = _module_refs_to_paths()
        orphans = [rel for rel in HARNESS_FILES if rel != LOAD_SCRIPT and rel not in referenced]
        assert orphans == [], f"pinned but never referenced by the load script: {orphans}"

    def test_the_shell_script_is_valid_bash(self):
        """Syntax-checked with `bash -n` when a real bash exists, and skipped -
        never silently passed - when it does not.

        `shutil.which("bash")` alone is not enough: on Windows it resolves to
        ``C:\\Windows\\system32\\bash.exe``, the WSL *launcher*, which exists,
        is on PATH, and exits non-zero for every argument because no distro is
        installed. Treating that as a syntax failure would be a false alarm
        about the script, so bash is probed by running it first.
        """
        import shutil

        bash = shutil.which("bash")
        if bash is None:
            pytest.skip("no bash on PATH; the script cannot be syntax-checked in this environment")
        probe = subprocess.run([bash, "-c", ":"], capture_output=True, text=True, check=False)
        if probe.returncode != 0:
            pytest.skip(f"{bash} is on PATH but cannot run a command (WSL launcher, no distro); skipping syntax check")
        proc = subprocess.run([bash, "-n", str(SCRIPT)], capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr


class TestHarnessFileStateIsSelfDescribing:
    def test_the_reason_is_a_stable_vocabulary(self):
        """The shell script prints `reason`, so the set of words is part of the
        contract rather than incidental formatting."""
        vocab = {
            HarnessFileState("p", True, False, True, None, None, None).reason,
            HarnessFileState("p", True, True, True, "b", "c", "d").reason,
            HarnessFileState("p", False, False, False, None, None, None).reason,
            HarnessFileState("p", True, True, False, "b", "c", "d").reason,
        }
        assert vocab == {"untracked", "dirty", "missing", "clean"}

    def test_pinned_is_exactly_exists_and_tracked_and_not_dirty(self):
        for exists, tracked, dirty in [(a, b, c) for a in (True, False) for b in (True, False) for c in (True, False)]:
            state = HarnessFileState("p", exists, tracked, dirty, "b", "c", "d")
            assert state.pinned is (exists and tracked and not dirty)


class TestTheManifestModuleIsUsableAsAProgram:
    def test_it_has_a_main_entry_point(self):
        """run_perf_baseline.sh invokes it with `-m`, so the module must be
        executable as a program, not merely importable."""
        mod = ast.parse(Path(REPO_ROOT / "src" / "benchmarks" / "run_manifest.py").read_text(encoding="utf-8"))

        def is_main_guard(node: ast.stmt) -> bool:
            return (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name)
                and node.test.left.id == "__name__"
            )

        assert any(is_main_guard(n) for n in mod.body), (
            'no `if __name__ == "__main__":` block; `python -m src.benchmarks.run_manifest` would do nothing'
        )

    def test_it_gates_and_writes_in_one_invocation(self):
        """Two invocations would mean the run can pass the gate and then fail to
        record the manifest - or record one the gate never approved."""
        mod = ast.parse(Path(REPO_ROOT / "src" / "benchmarks" / "run_manifest.py").read_text(encoding="utf-8"))
        func = next(n for n in mod.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        calls = [n.func.id for n in ast.walk(func) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        assert calls.count("build_manifest") == 1
        assert calls.count("write_manifest") == 1
        assert calls.index("build_manifest") < calls.index("write_manifest")

    def test_the_gate_runs_before_the_write(self):
        """build_manifest() calls assert_harness_pinned() itself, so a manifest
        for an unpinned harness cannot be written even by a direct caller."""
        mod = ast.parse(Path(REPO_ROOT / "src" / "benchmarks" / "run_manifest.py").read_text(encoding="utf-8"))
        func = next(n for n in mod.body if isinstance(n, ast.FunctionDef) and n.name == "build_manifest")
        calls = [n.func.id for n in ast.walk(func) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        assert "assert_harness_pinned" in calls
        assert calls.index("_git") < calls.index("assert_harness_pinned"), (
            "HEAD must be read before the gate, not after"
        )


# --------------------------------------------------------------------------
# H-9: two hosts, one artefact - the app-host report is the only evidence
# --------------------------------------------------------------------------
#
# Before H-9 the load generator and the server were one process tree on one
# machine, so the harness could read everything it needed about the server off its
# own settings object. That is no longer true, and the tests below pin the
# consequences: a report that cannot be trusted, a runtime that is not the one that
# ships, a field that was quietly omitted, and a run whose configuration moved
# while it was in flight.


class TestThePremiseTheRuntimeGateRestsOn:
    def test_the_image_cmd_leaves_the_loop_and_the_protocol_to_uvicorn(self):
        """The gate refuses anything but ``uvloop`` + ``httptools`` on the strength
        of the image's CMD. If the image ever passed ``--loop`` or ``--http``, these
        expectations would be wrong and the gate would refuse the shipped image -
        so the premise is asserted rather than assumed."""
        cmd_line = next(
            (line for line in DOCKERFILE.read_text(encoding="utf-8").splitlines() if line.strip().startswith("CMD")),
            None,
        )
        assert cmd_line is not None, "the Dockerfile declares no CMD, so there is no shipped runtime to check"
        assert "uvicorn" in cmd_line
        assert "--loop" not in cmd_line, (
            "the image now pins --loop explicitly, so EXPECTED_EVENT_LOOP is a guess. Re-derive it."
        )
        assert "--http" not in cmd_line, (
            "the image now pins --http explicitly, so EXPECTED_HTTP_IMPL is a guess. Re-derive it."
        )

    def test_resolve_event_loop_agrees_with_uvloops_importability(self):
        """uvicorn's auto-selection is a single ``try: import uvloop``, so
        importability IS the decision. Checked against an independent import
        rather than against the resolver's own logic."""
        try:
            import uvloop  # noqa: F401

            importable = True
        except ImportError:
            importable = False
        assert resolve_event_loop() == ("uvloop" if importable else "asyncio")

    def test_resolve_http_impl_agrees_with_httptools_importability(self):
        """Same argument for the protocol, checked against uvicorn's OWN resolved
        symbol rather than a second implementation of the choice."""
        from uvicorn.protocols.http.auto import AutoHTTPProtocol

        try:
            import httptools  # noqa: F401

            importable = True
        except ImportError:
            importable = False
        expected = "httptools" if importable else "h11"
        assert resolve_http_impl() == expected
        assert AutoHTTPProtocol.__module__.endswith(f"{expected}_impl")

    def test_the_report_records_the_premise_it_was_resolved_under(self):
        """A report that says ``uvloop`` without saying it was resolved under
        ``--loop auto`` cannot be checked against the image later."""
        report = build_app_host_report(event_loop=EXPECTED_EVENT_LOOP, http_impl=EXPECTED_HTTP_IMPL)
        assert report["uvicorn"]["loop_arg"] == "auto"
        assert report["uvicorn"]["http_arg"] == "auto"
        assert report["uvicorn"]["version"]
        assert report["schema"] == APP_HOST_REPORT_SCHEMA

    def test_the_report_carries_all_five_identity_fields(self):
        """Addendum 6.1, verbatim: the resolved ``--loop`` value, the event-loop
        implementation, the HTTP implementation, the Python version, and the image
        digest. Four are resolvable from the server's own interpreter; the fifth is
        declared. All five must be present, because a manifest missing one reads as
        complete - which is the failure this whole module exists to make impossible.
        """
        report = build_app_host_report(
            event_loop=EXPECTED_EVENT_LOOP,
            http_impl=EXPECTED_HTTP_IMPL,
            image_digest=DECLARED_IMAGE_DIGEST,
        )
        assert report["uvicorn"]["loop_arg"] == "auto", "the resolved --loop value"
        assert report["event_loop"] == EXPECTED_EVENT_LOOP, "the event-loop implementation"
        assert report["http"] == EXPECTED_HTTP_IMPL, "the HTTP implementation"
        assert report["python"]["version"], "the Python version"
        assert report["image_digest"] == DECLARED_IMAGE_DIGEST, "the image digest"

    def test_the_resolved_loop_value_and_the_implementation_are_not_the_same_fact(self):
        """``--loop auto`` is the argument the image passes; ``uvloop`` is what it
        resolved to. Collapsing them into one field would leave a report that says
        ``auto`` and means nothing, or one that says ``uvloop`` with nothing left to
        check against the CMD."""
        report = build_app_host_report(
            event_loop=EXPECTED_EVENT_LOOP,
            http_impl=EXPECTED_HTTP_IMPL,
            image_digest=DECLARED_IMAGE_DIGEST,
        )
        assert report["uvicorn"]["loop_arg"] != report["event_loop"]


class TestTheImageDigestIsDeclaredNotInvented:
    """The fifth identity field cannot be resolved by the process it describes, so
    every rule about it has to be about honesty: declare it, validate what was
    declared, label its absence, and never substitute anything."""

    def test_it_is_read_from_the_environment_the_orchestrator_declared_it_in(self, monkeypatch):
        monkeypatch.setenv(IMAGE_DIGEST_ENV, DECLARED_IMAGE_DIGEST)
        assert resolve_image_digest() == DECLARED_IMAGE_DIGEST

    def test_an_undeclared_digest_is_none_and_not_a_guess(self, monkeypatch):
        monkeypatch.delenv(IMAGE_DIGEST_ENV, raising=False)
        assert resolve_image_digest() is None

    def test_a_blank_declaration_is_absent_not_an_empty_identity(self, monkeypatch):
        monkeypatch.setenv(IMAGE_DIGEST_ENV, "   ")
        assert resolve_image_digest() is None

    def test_the_report_records_the_declared_digest_verbatim(self, monkeypatch):
        monkeypatch.setenv(IMAGE_DIGEST_ENV, DECLARED_IMAGE_DIGEST)
        report = build_app_host_report(
            event_loop=EXPECTED_EVENT_LOOP,
            http_impl=EXPECTED_HTTP_IMPL
        )
        assert report["image_digest"] == DECLARED_IMAGE_DIGEST

    def test_an_undeclared_digest_is_labelled_rather_than_dropped(self, pinned_repo: Path, monkeypatch):
        """A bare-metal app host has no digest, and that is a real topology - but a
        number that cannot be tied to a build must say so on its face."""
        monkeypatch.delenv(IMAGE_DIGEST_ENV, raising=False)
        report = build_app_host_report(
            event_loop=EXPECTED_EVENT_LOOP,
            http_impl=EXPECTED_HTTP_IMPL,
            deployment=dict(DECLARED_DEPLOYMENT),
        )
        assert report["image_digest"] is None
        manifest = build_manifest(app_host=report, run_params={"users": "100"}, repo_root=pinned_repo)
        assert any("image-digest-unknown" in label for label in manifest["labels"]), manifest["labels"]

    def test_a_declared_digest_carries_no_such_label(self, pinned_repo: Path, app_host_report):
        manifest = build_manifest(app_host=app_host_report, run_params={"users": "100"}, repo_root=pinned_repo)
        assert not any("image-digest-unknown" in label for label in manifest["labels"]), manifest["labels"]

    def test_a_declared_digest_reaches_the_manifest(self, pinned_repo: Path, app_host_report):
        manifest = build_manifest(app_host=app_host_report, run_params={"users": "100"}, repo_root=pinned_repo)
        assert manifest["app_host"]["image_digest"] == DECLARED_IMAGE_DIGEST

    def test_a_truncated_digest_is_refused_on_load(self, tmp_path: Path):
        """Worse than absent: a digest cut in half by the hand copy reads as an
        identity, and nothing downstream would ever notice."""
        report = write_app_host_report(tmp_path)
        document = json.loads(report.read_text(encoding="utf-8"))
        document["image_digest"] = DECLARED_IMAGE_DIGEST[:20]
        report.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(AppHostReportMissing) as caught:
            load_app_host_report(report)
        assert IMAGE_DIGEST_ENV in str(caught.value), "the refusal must name where a digest comes from"

    def test_a_digest_typed_from_memory_is_refused_on_load(self, tmp_path: Path):
        for impostor in ("latest", "sha256:", "local", "sha256:" + "z" * 64, DECLARED_IMAGE_DIGEST.upper()):
            report = write_app_host_report(tmp_path, image_digest=impostor)
            with pytest.raises(AppHostReportMissing):
                load_app_host_report(report)

    def test_a_digest_that_is_not_a_string_is_refused_on_load(self, tmp_path: Path):
        report = write_app_host_report(tmp_path, image_digest={"digest": DECLARED_IMAGE_DIGEST})
        with pytest.raises(AppHostReportMissing):
            load_app_host_report(report)

    def test_an_absent_digest_is_carried_as_none_and_accepted(self, tmp_path: Path):
        """Absence is a fact about the topology, not a defect in the report. Refusing
        it would make the harness unusable on a bare-metal app host - and would be a
        wider decision than addendum 6.1 makes."""
        report = write_app_host_report(tmp_path, image_digest=None)
        assert load_app_host_report(report)["image_digest"] is None

    def test_the_digest_is_not_part_of_the_presence_checked_field_list(self):
        """It is deliberately NOT in ``APP_HOST_REPORT_FIELDS``: those are the fields
        whose absence makes the report unusable, and this one is legitimately absent
        on a bare-metal app host. Mixing the two would make a legitimate topology
        refuse to load."""
        assert "image_digest" not in APP_HOST_REPORT_FIELDS

    def test_the_digest_is_a_load_bearing_part_of_the_runtime_record(
        self, pinned_repo: Path, app_host_report):
        """It travels inside the app-host section, which the run-end record must
        match. So a digest that changed while the run was in flight - the app host
        was replaced mid-measurement - voids the run instead of producing a number
        attributed to two different builds."""
        end = dict(app_host_report)
        end["image_digest"] = "sha256:" + "1" * 64
        started = build_manifest(
            app_host=app_host_report, run_params={"users": "100"}, repo_root=pinned_repo
        )
        finished = build_manifest(
            app_host=end, run_params={"users": "100"}, repo_root=pinned_repo, phase="end"
        )
        with pytest.raises(RunEndMismatch) as caught:
            assert_run_end_matches(started, finished)
        assert "app_host" in caught.value.sections


class TestTheDeploymentManifestIsRecordedNotGuessed:
    """L-2 / addendum A-4: where the server runs, as a fact, or the run does not happen.

    N-3 found no in-repo file recording the deployment target, so every field here
    is either resolved off the machine or declared by the operator. None is ever
    inferred from the CI runner's topology, because that runner is exactly the
    co-located shared box A-5 rules out as a ``before`` arm.
    """

    def test_every_field_of_addendum_six_one_is_present_in_the_report(self, app_host_report):
        report = app_host_report
        for field in ("orchestrator", "distro", "cpu_model", "core_count", "store_locality"):
            assert field in report["deployment"], f"addendum 6.1 names {field} and the report omits it"

    def test_the_manifest_carries_the_deployment_verbatim(self, pinned_repo: Path, app_host_report):
        manifest = build_manifest(
            app_host=app_host_report, run_params={"users": "100"}, repo_root=pinned_repo
        )
        for field in ("orchestrator", "distro", "cpu_model", "core_count", "store_locality"):
            assert manifest["deployment"][field] == DECLARED_DEPLOYMENT[field], (
                f"deployment.{field} was not recorded as declared: an artefact that paraphrases "
                "the deployment cannot be checked against it"
            )

    def test_the_core_count_the_ratios_are_divided_by_is_recorded(self, app_host_report):
        """A-8's thread/process ratios inherit an UNDOCUMENTED production core count,
        which is why they are adjudicated RE-MEASURE. The number the report records is
        the one a reader can divide by, so it has to be an integer rather than a
        string."""
        assert isinstance(app_host_report["deployment"]["core_count"], int)
        assert app_host_report["deployment"]["core_count"] > 0

    def test_a_missing_field_aborts_rather_than_defaulting(self, pinned_repo: Path, app_host_report):
        """The whole point of the manifest. A core count that silently became the
        machine's own count, or a locality that silently became 'remote', would put
        every throughput claim on an assumption that is invisible in the artefact."""
        for field in ("orchestrator", "distro", "cpu_model", "core_count", "store_locality"):
            holed = dict(app_host_report)
            deployment = {k: v for k, v in DECLARED_DEPLOYMENT.items() if k != field}
            holed["deployment"] = deployment
            with pytest.raises(DeploymentUndeclared) as caught:
                build_manifest(app_host=holed, run_params={"users": "100"}, repo_root=pinned_repo)
            assert field in caught.value.fields, f"dropping {field} did not refuse the run"

    def test_the_refusal_names_where_a_declaration_comes_from(self, pinned_repo: Path, app_host_report):
        holed = dict(app_host_report)
        holed["deployment"] = {k: v for k, v in DECLARED_DEPLOYMENT.items() if k != "orchestrator"}
        with pytest.raises(DeploymentUndeclared) as caught:
            build_manifest(app_host=holed, run_params={"users": "100"}, repo_root=pinned_repo)
        message = str(caught.value)
        assert ORCHESTRATOR_ENV in message, "the operator must be told which variable to set"
        assert "defaulting" in message

    def test_a_report_with_no_deployment_section_at_all_is_refused_on_load(self, tmp_path: Path):
        path = write_app_host_report(tmp_path)
        document = json.loads(path.read_text(encoding="utf-8"))
        del document["deployment"]
        path.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(AppHostReportMissing) as caught:
            load_app_host_report(path)
        assert ORCHESTRATOR_ENV in str(caught.value)

    def test_a_store_locality_that_is_not_one_of_the_three_is_refused(self, tmp_path: Path):
        """Co-located puts a round trip at ~0.3 ms against the pack's 0.72-3.10 ms, so
        which of the three it is decides what the RTT findings are worth. A fourth
        spelling would be a reader's guess."""
        for impostor in ("localhost", "COLOCATED", "same-box", "remote-ish", True):
            report = write_app_host_report(tmp_path)
            document = json.loads(report.read_text(encoding="utf-8"))
            document["deployment"]["store_locality"] = impostor
            report.write_text(json.dumps(document), encoding="utf-8")
            with pytest.raises(AppHostReportMissing):
                load_app_host_report(report)

    def test_locality_is_derived_when_the_addresses_settle_it(self):
        """A loopback literal is co-located by definition and a routable literal is
        not, so neither needs a human. Deriving it is strictly better than asking."""
        loopback = {
            "postgres": "127.0.0.1:5432/app",
            "redis_cache": "127.0.0.1:6379",
            "redis_queue": "127.0.0.1:6379",
        }
        assert resolve_store_locality(loopback) == "co-located"
        remote = {
            "postgres": "10.0.0.5:5432/app",
            "redis_cache": "10.0.0.6:6379",
            "redis_queue": "10.0.0.6:6379",
        }
        assert resolve_store_locality(remote) == "remote"
        mixed = {**loopback, "redis_cache": "10.0.0.6:6379"}
        assert resolve_store_locality(mixed) == "mixed"

    def test_the_address_is_split_by_what_it_is_not_by_counting_colons(self, monkeypatch):
        """``[::1]:6379`` and a bare ``::1`` have the same colon count and different
        meanings. Parsing them the same way classifies a co-located store as remote -
        the one direction that changes every RTT conclusion in the programme - so the
        shapes are pinned individually.
        """
        monkeypatch.delenv(STORE_LOCALITY_ENV, raising=False)
        loopback_v6 = {
            "postgres": "[::1]:5432/app",
            "redis_cache": "[::1]:6379",
            "redis_queue": "[::1]:6379",
        }
        assert resolve_store_locality(loopback_v6) == "co-located", (
            "a bracketed IPv6 loopback address with a port is still the app host itself"
        )
        bare_v6 = {
            "postgres": "::1",
            "redis_cache": "::1",
            "redis_queue": "::1",
        }
        assert resolve_store_locality(bare_v6) == "co-located"
        remote_v6 = {
            "postgres": "[fd00::1]:5432/app",
            "redis_cache": "[fd00::2]:6379",
            "redis_queue": "[fd00::2]:6379",
        }
        assert resolve_store_locality(remote_v6) == "remote"
        assert _address_host("10.0.0.5:6379") == "10.0.0.5"
        assert _address_host("db.internal:5432/app") == "db.internal"
        assert _address_host("db.internal") == "db.internal"
        assert _address_host("fd00::1") == "fd00::1"
        assert _address_host("fd00::1") != "fd00:", (
            "a bare IPv6 literal ending in a decimal group must not have that group "
            "stripped as if it were a port"
        )

    def test_locality_is_not_derivable_from_a_bare_hostname(self, monkeypatch):
        """Which is the case in practice: every store address in ``src/.env`` is a
        hostname. It may resolve to loopback ON THE APP HOST - the case that would
        invalidate the RTT findings - so string-matching the address would be a
        guess wearing a fact's clothes."""
        monkeypatch.delenv(STORE_LOCALITY_ENV, raising=False)
        hostnames = {
            "postgres": "db.internal:5432/app",
            "redis_cache": "cache.internal:6379",
            "redis_queue": "queue.internal:6379",
        }
        assert resolve_store_locality(hostnames) is None, (
            "a hostname is not evidence of remoteness; returning a value here would put "
            "the Redis-cost programme on an assumption"
        )

    def test_the_deployment_can_declare_what_the_addresses_cannot(self, monkeypatch):
        monkeypatch.setenv(STORE_LOCALITY_ENV, "co-located")
        hostnames = {
            "postgres": "db.internal:5432/app",
            "redis_cache": "cache.internal:6379",
            "redis_queue": "queue.internal:6379",
        }
        assert resolve_store_locality(hostnames) == "co-located"

    def test_the_orchestrator_is_declared_and_never_inferred(self, monkeypatch):
        """There is no in-repo answer to substitute and the CI runner is not one."""
        monkeypatch.delenv(ORCHESTRATOR_ENV, raising=False)
        assert resolve_orchestrator() is None
        monkeypatch.setenv(ORCHESTRATOR_ENV, "  docker-compose  ")
        assert resolve_orchestrator() == "docker-compose", "the declaration is recorded verbatim"

    def test_the_distro_and_cpu_model_are_read_off_the_machine(self):
        """Both are evidence, not declarations, and both come from the file the image
        was built from rather than from a reconstructed uname string. Neither may
        return anything on a host that publishes neither file - ``/etc/os-release``
        and ``/proc/cpuinfo`` are Linux files, so their absence is the honest answer
        and not a reason to substitute ``platform.platform()``."""
        distro = resolve_distro()
        cpu_model = resolve_cpu_model()
        if platform.system() != "Linux":
            assert distro is None, (
                f"on a non-Linux host {platform.system()} there is no /etc/os-release to read, "
                f"so {distro!r} must have been reconstructed from something else"
            )
            assert cpu_model is None, (
                f"on a non-Linux host there is no /proc/cpuinfo, so {cpu_model!r} must have "
                "come from somewhere that is not the machine's own record"
            )
        else:
            assert isinstance(distro, str) and distro, "a Linux host publishes /etc/os-release"
            assert isinstance(cpu_model, str) and cpu_model, (
                "a Linux host publishes /proc/cpuinfo; the A-8 ratios are core-count "
                "dependent, so '12-core' is not checkable without the model string"
            )

    def test_the_platform_is_imported_for_that_check(self):
        """Guard on the guard: the assertion above is only meaningful because it reads
        the platform, so the name has to be in scope."""
        assert platform.system() in {"Linux", "Windows", "Darwin"}

    def test_the_core_count_prefers_the_process_mask_over_the_machine_count(self):
        """``os.cpu_count()`` counts what the kernel knows, which on a pinned or
        cgroup-limited deployment overstates the parallelism the workload gets - and
        the parallelism is what every ratio is divided by."""
        cores = resolve_core_count()
        machine = os.cpu_count()
        assert cores is not None, "the process always has a core count; None would abort every run"
        assert machine is not None
        assert cores <= machine, (
            "an affinity mask cannot be wider than the machine; a count larger than "
            "os.cpu_count() means the number is not the parallelism the workload gets"
        )

    def test_a_deployment_that_moved_mid_run_voids_the_run(self, pinned_repo: Path, app_host_report):
        """The core count of the machine the numbers came from is part of what the
        run-start record promises. A host replaced mid-measurement means the two ends
        describe different machines."""
        moved = dict(app_host_report)
        moved["deployment"] = {**DECLARED_DEPLOYMENT, "core_count": 64}
        started = build_manifest(
            app_host=app_host_report, run_params={"users": "100"}, repo_root=pinned_repo
        )
        finished = build_manifest(
            app_host=moved, run_params={"users": "100"}, repo_root=pinned_repo, phase="end"
        )
        with pytest.raises(RunEndMismatch) as caught:
            assert_run_end_matches(started, finished)
        assert "deployment" in caught.value.sections

    def test_the_image_digest_is_excluded_from_the_refused_fields(self):
        """The five deployment fields are evidence for the throughput claims and an
        absent one stops the run. The digest is different in kind - a bare-metal app
        host legitimately has none, and what it lacks is a tie to a build, not a fact
        about the machine. Refusing it would make the harness unusable outside a
        container, and it is labelled instead (see the class above)."""
        holed = {**DECLARED_DEPLOYMENT, "image_digest": None}
        assert_deployment_recorded(holed), "an absent digest must not refuse the run"
        with pytest.raises(DeploymentUndeclared) as caught:
            assert_deployment_recorded({**DECLARED_DEPLOYMENT, "core_count": None})
        assert caught.value.fields == ("core_count",), (
            "the refusal must name only the field that is actually absent"
        )

    def test_the_module_as_a_program_refuses_an_undeclared_deployment(self, tmp_path: Path, pinned_repo):
        report = write_app_host_report(tmp_path)
        document = json.loads(report.read_text(encoding="utf-8"))
        del document["deployment"]["core_count"]
        report.write_text(json.dumps(document), encoding="utf-8")
        output = tmp_path / "manifest.json"
        rc = main(
            [
                "--repo-root",
                str(pinned_repo),
                "--app-host-report",
                str(report),
                "--output",
                str(output),
                "--users",
                "100",
            ]
        )
        assert rc == EXIT_DEPLOYMENT_UNDECLARED, (
            "a manifest missing the deployment core count must not be written; the exit code "
            "has to tell the operator this was a deployment finding and not a dirty harness"
        )
        assert not output.exists(), "no artefact may be written for a run that was refused"


class TestTheAppHostReportIsCheckedNotTrusted:
    """The file is hand-copied between machines, so every way it can be wrong has
    to be refused on load rather than read as a report with little to say."""

    def test_a_report_that_is_not_there_is_refused(self, tmp_path: Path):
        with pytest.raises(AppHostReportMissing):
            load_app_host_report(tmp_path / "never-copied.json")

    def test_a_report_from_another_schema_is_refused(self, tmp_path: Path):
        path = write_app_host_report(tmp_path, schema="perf-app-host-report/0")
        with pytest.raises(AppHostReportMissing) as caught:
            load_app_host_report(path)
        assert APP_HOST_REPORT_SCHEMA in str(caught.value)

    def test_a_report_truncated_by_the_copy_is_refused(self, tmp_path: Path):
        path = tmp_path / "truncated.json"
        path.write_text(json.dumps({"schema": APP_HOST_REPORT_SCHEMA}), encoding="utf-8")
        with pytest.raises(AppHostReportMissing) as caught:
            load_app_host_report(path)
        assert ", ".join(APP_HOST_REPORT_FIELDS) in str(caught.value)

    def test_a_report_that_is_not_an_object_is_refused(self, tmp_path: Path):
        path = tmp_path / "list.json"
        path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        with pytest.raises(AppHostReportMissing):
            load_app_host_report(path)

    def test_the_refusal_names_the_command_that_produces_one(self, tmp_path: Path):
        """The fix is a command to run on a machine the operator may not be logged
        into, so a refusal that did not name it would leave them guessing."""
        with pytest.raises(AppHostReportMissing) as caught:
            load_app_host_report(tmp_path / "absent.json")
        assert "--emit-app-host-report" in str(caught.value)


class TestANonRepresentativeServerIsRefused:
    def test_a_server_on_asyncio_is_refused(self, pinned_repo: Path, tmp_path: Path):
        report = load_app_host_report(write_app_host_report(tmp_path, event_loop="asyncio"))
        with pytest.raises(NonRepresentativeRuntime) as caught:
            build_manifest(app_host=report, run_params={"users": "100"}, repo_root=pinned_repo)
        assert "asyncio" in str(caught.value)

    def test_a_server_on_h11_is_refused(self, pinned_repo: Path, tmp_path: Path):
        report = load_app_host_report(write_app_host_report(tmp_path, http="h11"))
        with pytest.raises(NonRepresentativeRuntime) as caught:
            build_manifest(app_host=report, run_params={"users": "100"}, repo_root=pinned_repo)
        assert "h11" in str(caught.value)

    def test_the_refusal_names_the_expected_runtime(self, pinned_repo: Path, tmp_path: Path):
        report = load_app_host_report(write_app_host_report(tmp_path, event_loop="asyncio", http="h11"))
        with pytest.raises(NonRepresentativeRuntime) as caught:
            build_manifest(app_host=report, run_params={"users": "100"}, repo_root=pinned_repo)
        message = str(caught.value)
        assert EXPECTED_EVENT_LOOP in message and EXPECTED_HTTP_IMPL in message

    def test_the_report_itself_records_why_it_is_non_representative(self, tmp_path: Path):
        """The app host is told the truth as well as being refused, so the operator
        sees the verdict in the file they are about to copy."""
        report = build_app_host_report(event_loop="asyncio", http_impl="h11")
        assert report["representative"] is False
        assert any("asyncio" in reason for reason in report["non_representative_reasons"])
        assert any("h11" in reason for reason in report["non_representative_reasons"])

    def test_a_non_linux_app_host_is_recorded_but_not_refused(self):
        """Platform is a LABELLING rule (A-5); the loop and the protocol change what
        the instrument measures. Refusing to measure at all on a non-Linux host
        would be a wider decision than the addendum makes."""
        report = build_app_host_report(event_loop=EXPECTED_EVENT_LOOP, http_impl=EXPECTED_HTTP_IMPL)
        if report["host"]["platform"] == "Linux":
            pytest.skip("this host is Linux, so the non-Linux path cannot be exercised here")
        assert any("not Linux" in reason for reason in report["non_representative_reasons"])
        assert report["representative"] is False


class TestAMissingFieldAbortsRatherThanDefaulting:
    def test_a_complete_manifest_passes(self, pinned_repo: Path, app_host_report):
        manifest = build_manifest(app_host=app_host_report, run_params={"users": "100"}, repo_root=pinned_repo)
        assert_manifest_complete(manifest)

    def test_every_required_field_is_load_bearing(self, pinned_repo: Path, app_host_report):
        """Each one is a field a reader is entitled to assume. Removing any of them
        must ABORT: the alternative is a manifest that omits what it could not
        establish and reads as complete, which is the failure this module exists to
        make impossible."""
        manifest = build_manifest(app_host=app_host_report, run_params={"users": "100"}, repo_root=pinned_repo)
        for field in REQUIRED_MANIFEST_FIELDS:
            damaged = copy.deepcopy(manifest)
            top, _, rest = field.partition(".")
            if rest:
                del damaged[top][rest]  # type: ignore[index]
            else:
                del damaged[top]
            with pytest.raises(ManifestIncomplete) as caught:
                assert_manifest_complete(damaged)
            assert field in caught.value.fields

    def test_an_empty_value_counts_as_absent_not_as_a_value(self, pinned_repo: Path, app_host_report):
        """``run: {}`` describes no run at all: no users, no target. Reading it as
        present would let a perf artefact that measured nothing be filed as a
        baseline."""
        manifest = build_manifest(app_host=app_host_report, run_params={"users": "100"}, repo_root=pinned_repo)
        with pytest.raises(ManifestIncomplete):
            assert_manifest_complete({**manifest, "run": {}})

    def test_the_refusal_says_it_is_a_harness_defect(self, pinned_repo: Path, app_host_report):
        manifest = build_manifest(app_host=app_host_report, run_params={"users": "100"}, repo_root=pinned_repo)
        with pytest.raises(ManifestIncomplete) as caught:
            assert_manifest_complete({**manifest, "schema": ""})
        assert "defect in the harness" in str(caught.value)


class TestTheRunEndMustMatchTheRunStart:
    def _build(self, pinned_repo: Path, app_host_report, phase: str = "start"):
        return build_manifest(
            app_host=app_host_report, run_params={"users": "100"}, repo_root=pinned_repo, phase=phase
        )

    def test_an_unchanged_run_matches_its_start(self, pinned_repo: Path, app_host_report):
        start = self._build(pinned_repo, app_host_report)
        end = self._build(pinned_repo, app_host_report, phase="end")
        assert assert_run_end_matches(start, end) == end

    def test_the_phase_is_recorded_because_it_distinguishes_two_artefacts(self, pinned_repo: Path, app_host_report):
        """Both records name a file, so a reader can tell which is which without
        reading the timestamp."""
        assert self._build(pinned_repo, app_host_report)["phase"] == "start"
        assert self._build(pinned_repo, app_host_report, phase="end")["phase"] == "end"

    def test_a_commit_that_moved_voids_the_run(self, pinned_repo: Path, app_host_report):
        start = self._build(pinned_repo, app_host_report)
        (pinned_repo / HARNESS_FILES[1]).write_text("# edited mid-run\n", encoding="utf-8")
        _git(pinned_repo, "add", "-A")
        _git(pinned_repo, "commit", "-m", "an edit that landed during the run")
        end = self._build(pinned_repo, app_host_report, phase="end")
        with pytest.raises(RunEndMismatch) as caught:
            assert_run_end_matches(start, end)
        assert "git" in caught.value.sections
        assert "harness" in caught.value.sections

    def test_a_security_limit_that_moved_voids_the_run(self, pinned_repo: Path, app_host_report):
        start = self._build(pinned_repo, app_host_report)
        drifted = copy.deepcopy(app_host_report)
        drifted["limits"]["default_anonymous_limit"] = [999, 3600]
        end = self._build(pinned_repo, drifted, phase="end")
        with pytest.raises(RunEndMismatch) as caught:
            assert_run_end_matches(start, end)
        assert "limits" in caught.value.sections

    def test_a_relabelled_app_host_voids_the_run(self, pinned_repo: Path, app_host_report):
        """The app host was restarted on a different platform mid-run. The run-start
        record is then a statement about conditions that stopped holding, and the
        numbers in between cannot be attributed to anything.

        The replacement platform is picked to differ from THIS host's, so the test
        asserts a move rather than passing because the fixture already said what
        the edit says."""
        start = self._build(pinned_repo, app_host_report)
        moved = copy.deepcopy(app_host_report)
        elsewhere = "Linux" if app_host_report["host"]["platform"] != "Linux" else "Windows"
        moved["host"]["platform"] = elsewhere
        moved["representative"] = False
        moved["non_representative_reasons"] = [f"app host platform is {elsewhere}, not Linux (A-5)"]
        end = self._build(pinned_repo, moved, phase="end")
        with pytest.raises(RunEndMismatch) as caught:
            assert_run_end_matches(start, end)
        assert "app_host" in caught.value.sections
        assert "labels" in caught.value.sections, "a change that adds a label changes the labels section too"

    def test_the_refusal_says_the_run_is_void(self, pinned_repo: Path, app_host_report):
        start = self._build(pinned_repo, app_host_report)
        end = {**copy.deepcopy(start), "git": {**start["git"], "sha": "0" * 40}}
        with pytest.raises(RunEndMismatch) as caught:
            assert_run_end_matches(start, end)
        assert "void" in str(caught.value)


class TestTheModuleAsAProgramEndToEnd:
    """`main()` is what the shell script and any CI job actually run, so the exit
    codes are pinned here rather than only in the assert functions."""

    def test_a_missing_report_is_exit_six_and_writes_nothing(self, tmp_path: Path, capsys):
        out = tmp_path / "m.json"
        rc = main(["--output", str(out), "--users", "100"])
        assert rc == EXIT_NO_APP_HOST_REPORT
        assert not out.exists(), (
            "a manifest written without the app host's report describes the load host under server keys"
        )
        assert "--emit-app-host-report" in capsys.readouterr().err

    def test_an_unusable_report_is_exit_six(self, tmp_path: Path):
        bad = tmp_path / "bad.json"
        bad.write_text("{}", encoding="utf-8")
        assert main(["--app-host-report", str(bad), "--users", "100"]) == EXIT_NO_APP_HOST_REPORT

    def test_a_non_uvloop_server_is_exit_seven(self, tmp_path: Path, pinned_repo: Path, capsys):
        report = write_app_host_report(tmp_path, event_loop="asyncio")
        rc = main(["--app-host-report", str(report), "--repo-root", str(pinned_repo), "--users", "100"])
        assert rc == EXIT_NON_REPRESENTATIVE
        assert "asyncio" in capsys.readouterr().err

    def test_a_dirty_harness_still_exits_three(self, tmp_path: Path, pinned_repo: Path):
        """The new gates must not displace the old ones. A dirty harness is a
        different ask of the operator than a non-representative server, and the
        exit code is how a CI job tells them apart."""
        (pinned_repo / HARNESS_FILES[1]).write_text("# dirty\n", encoding="utf-8")
        report = write_app_host_report(tmp_path)
        rc = main(["--app-host-report", str(report), "--repo-root", str(pinned_repo), "--users", "100"])
        assert rc == EXIT_UNPINNED

    def test_an_unchanged_run_passes_both_ends(self, tmp_path: Path, pinned_repo: Path):
        report = write_app_host_report(tmp_path)
        start = tmp_path / "start.json"
        end = tmp_path / "end.json"
        common = ["--app-host-report", str(report), "--repo-root", str(pinned_repo), "--users", "100", "--host", "http://app-host.internal:8000"]
        assert main([*common, "--output", str(start)]) == 0
        assert main([*common, "--output", str(end), "--phase", "end", "--assert-matches", str(start)]) == 0
        assert json.loads(start.read_text(encoding="utf-8"))["phase"] == "start"
        assert json.loads(end.read_text(encoding="utf-8"))["phase"] == "end"

    def test_a_mid_run_change_is_exit_nine_and_no_end_manifest_is_written(self, tmp_path: Path, pinned_repo: Path):
        report = write_app_host_report(tmp_path)
        start = tmp_path / "start.json"
        end = tmp_path / "end.json"
        common = ["--app-host-report", str(report), "--repo-root", str(pinned_repo), "--users", "100"]
        assert main([*common, "--output", str(start)]) == 0
        (pinned_repo / HARNESS_FILES[1]).write_text("# landed mid-run\n", encoding="utf-8")
        _git(pinned_repo, "add", "-A")
        _git(pinned_repo, "commit", "-m", "mid-run")
        end_args = [*common, "--output", str(end), "--phase", "end", "--assert-matches", str(start)]
        assert main(end_args) == EXIT_RUN_END_MISMATCH
        assert start.exists(), "the run-start record is the statement of what was measured; it must survive"
        assert not end.exists(), (
            "a manifest is a self-certifying document: one that failed its own run-end "
            "assertion must not land in reports/ where tooling globs up valid records. "
            "The VOID banner in the log and exit code 9 are the record that it failed."
        )

    def test_an_end_record_with_nothing_to_assert_against_is_refused(self, tmp_path: Path, pinned_repo: Path, capsys):
        report = write_app_host_report(tmp_path)
        rc = main(["--app-host-report", str(report), "--repo-root", str(pinned_repo), "--phase", "end"])
        assert rc == EXIT_NO_APP_HOST_REPORT
        assert "--assert-matches" in capsys.readouterr().err

    def test_an_end_record_whose_start_record_is_missing_is_refused(self, tmp_path: Path, pinned_repo: Path):
        report = write_app_host_report(tmp_path)
        rc = main(
            [
                "--app-host-report", str(report),
                "--repo-root", str(pinned_repo),
                "--phase", "end",
                "--assert-matches", str(tmp_path / "never-written.json"),
            ]
        )
        assert rc == EXIT_NO_APP_HOST_REPORT

    def test_app_host_mode_prints_a_report_and_exits_zero(self, capsys):
        assert main(["--emit-app-host-report"]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["schema"] == APP_HOST_REPORT_SCHEMA
        for field in APP_HOST_REPORT_FIELDS:
            assert field in report and report[field] not in (None, {}, [], ""), (
                f"{field} is absent from the emitted report"
            )

    def test_each_refusal_has_its_own_exit_code(self):
        """A CI job has to tell "the harness was edited" from "the server was not
        the one that ships". Asserted over the source for the same reason the shell
        wiring is: the exit-code branches are only reachable by making
        ``build_manifest`` misbehave, and a test that mocks the gate tests the
        mock."""
        mod = ast.parse((REPO_ROOT / "src" / "benchmarks" / "run_manifest.py").read_text(encoding="utf-8"))
        func = next(n for n in mod.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        handlers = [n for n in ast.walk(func) if isinstance(n, ast.ExceptHandler)]
        returns = {
            handler.type.id: handler.body[-1].value.id  # type: ignore[union-attr]
            for handler in handlers
            if isinstance(handler.type, ast.Name)
            and isinstance(handler.body[-1], ast.Return)
            and isinstance(handler.body[-1].value, ast.Name)
        }
        assert returns["HarnessNotPinned"] == "EXIT_UNPINNED"
        assert returns["HarnessDependencyNotTracked"] == "EXIT_UNTRACKED_DEPENDENCY"
        assert returns["NonRepresentativeRuntime"] == "EXIT_NON_REPRESENTATIVE"
        assert returns["ManifestIncomplete"] == "EXIT_INCOMPLETE_MANIFEST"
        assert returns["RunEndMismatch"] == "EXIT_RUN_END_MISMATCH"

    def test_the_exit_codes_are_distinct(self):
        codes = [
            EXIT_UNPINNED,
            EXIT_NO_HEAD,
            EXIT_UNTRACKED_DEPENDENCY,
            EXIT_NO_APP_HOST_REPORT,
            EXIT_NON_REPRESENTATIVE,
            EXIT_INCOMPLETE_MANIFEST,
            EXIT_RUN_END_MISMATCH,
            EXIT_PHASE_UNPROVEN,
        ]
        assert len(set(codes)) == len(codes), "two refusals sharing a code cannot be told apart by a CI job"


# --------------------------------------------------------------------------
# (c) P-5: the cold window, the >=60 s hold, and the measured window
# --------------------------------------------------------------------------
#
# Everything below drives the report from SYNTHETIC locust CSVs written on a fake
# clock. That is the only way to assert P-5's claims here: the claims are about
# ordering (the hold ends before the reset) and about populations (the hold's
# requests are not among the reported ones), and neither is observable by running
# a load test on the machine the suite happens to run on.


class FakeClock:
    """A deterministic stand-in for the clock locust timestamps its history with.

    It carries the cumulative request count and the ``--reset-stats`` boundary as
    well, so a test can ask where the reset landed and compare it with where the
    hold ended. That comparison is P-5's ordering claim reduced to arithmetic over
    two numbers rather than to a hope about timing that happened to pass.
    """

    def __init__(self, start: float, *, interval: float = 2.0, users: int = 100) -> None:
        self.now = start
        self.interval = interval
        self.users = users
        self.total = 0
        #: (timestamp, cumulative request count, user count), one per interval,
        #: which is what locust writes and all the report's timing comes from.
        self.rows: list[tuple[float, int, int]] = []
        self.reset_at: float | None = None

    def advance(self, seconds: float, *, requests: int, users: int | None = None) -> None:
        for _ in range(int(seconds / self.interval)):
            self.now += self.interval
            self.total += requests
            self.rows.append((self.now, self.total, self.users if users is None else users))

    def reset(self) -> None:
        """``--reset-stats``. The rows already written stay on disk, which is
        exactly what makes the boundary observable: the cumulative count goes
        DOWN at the first row written after it."""
        self.reset_at = self.now
        self.total = 0


#: The columns locust's ``_stats.csv`` carries that this harness reads. A real file
#: has more; the reader looks up the ones it needs by name and refuses one that has
#: renamed them.
_STATS_HEADER = (
    "Type,Name,Request Count,Failure Count,Average Response Time,"
    "Min Response Time,Max Response Time,50%,95%,99%"
)
_HISTORY_HEADER = "Timestamp,User Count,Total Request Count,Requests/s"


def _write_locust_csvs(
    prefix: Path, *, endpoints: list[dict[str, Any]], history: list[tuple[float, int, int]]
) -> None:
    """Write the two CSVs a single locust phase produces.

    ``Type`` is blank on a per-request-type row and ``Aggregated`` on the totals
    row, which is locust's own convention and what the reader keys off.
    """
    lines = [_STATS_HEADER]
    for endpoint in endpoints:
        lines.append(
            f",{endpoint['name']},{endpoint['requests']},{endpoint.get('failures', 0)},"
            f"{endpoint['mean']},{endpoint['min']},{endpoint['max']},"
            f"{endpoint['p50']},{endpoint['p95']},{endpoint['p99']}"
        )
    lines.append(f"Aggregated,,{sum(int(e['requests']) for e in endpoints)},0,0,0,0,0,0,0")
    stats = prefix.with_name(prefix.name + LOCUST_STATS_SUFFIX)
    stats.write_text("\n".join(lines) + "\n", encoding="utf-8")

    history_lines = [_HISTORY_HEADER]
    for timestamp, cumulative, users in history:
        history_lines.append(f"{timestamp},{users},{cumulative},0")
    history_path = prefix.with_name(prefix.name + "_stats_history.csv")
    history_path.write_text("\n".join(history_lines) + "\n", encoding="utf-8")


def _endpoint(name: str, requests: int, *, p50: float) -> dict[str, Any]:
    return {
        "name": name,
        "requests": requests,
        "failures": 0,
        "mean": p50,
        "min": p50 * 0.9,
        "max": p50 * 1.2,
        "p50": p50,
        "p95": p50 * 1.1,
        "p99": p50 * 1.2,
    }


def _phase(prefix: Path, clock: FakeClock, *, p50: float, names: tuple[str, ...] = ("GET /api/v1/health",)) -> Path:
    """Write one phase, splitting the clock's request count across ``names``."""
    per_endpoint = clock.total // len(names)
    remainder = clock.total - per_endpoint * len(names)
    endpoints = []
    for index, name in enumerate(names):
        requests = per_endpoint + (remainder if index == 0 else 0)
        endpoints.append(_endpoint(name, requests, p50=p50))
    _write_locust_csvs(prefix, endpoints=endpoints, history=clock.rows)
    return prefix


@pytest.fixture
def p5_run(tmp_path: Path, pinned_repo: Path, app_host_report: dict[str, Any]) -> dict[str, Any]:
    """One coherent P-5 run, on a fake clock, written as locust would have written it.

    cold 30 s at ~300 ms -> hold 60 s at 9999 ms -> spawn ramp -> ``--reset-stats``
    -> warm 30 s at ~30 ms. The 10x is the shape of the inflation this whole item
    exists to keep visible, and the hold's absurd medians are deliberate: any
    percentile that ever reported one could not be missed.
    """
    cold_clock = FakeClock(1_700_000_000.0)
    cold_clock.advance(30, requests=30)
    hold_clock = FakeClock(cold_clock.now)
    hold_clock.advance(60, requests=10)
    warm_clock = FakeClock(hold_clock.now)
    warm_clock.advance(10, requests=20, users=50)  # spawning, before --reset-stats
    warm_clock.reset()
    warm_clock.advance(30, requests=10)

    prefixes = {"cold": tmp_path / "baseline_cold", "hold": tmp_path / "baseline_hold", "warm": tmp_path / "baseline"}
    _phase(prefixes["cold"], cold_clock, p50=300.0)
    _phase(prefixes["hold"], hold_clock, p50=9999.0)
    _phase(prefixes["warm"], warm_clock, p50=30.0)

    manifest = build_manifest(
        app_host=app_host_report,
        run_params={"users": "100", "rate": "10", "duration": "30s", "host": "http://app-host.internal:8000"},
        repo_root=pinned_repo,
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return {
        "manifest": manifest,
        "manifest_path": manifest_path,
        "prefixes": prefixes,
        "warm_clock": warm_clock,
        "dir": tmp_path,
    }


def _phase_report(p5_run: dict[str, Any]) -> dict[str, Any]:
    prefixes = p5_run["prefixes"]
    return build_phase_report(
        manifest=p5_run["manifest"],
        cold_prefix=prefixes["cold"],
        hold_prefix=prefixes["hold"],
        warm_prefix=prefixes["warm"],
    )


class TestTheColdAndWarmPhasesAreBothReported:
    """Criterion (c): the cold figure and the warm figure are both in the artefact,
    so a 5-10x inflation cannot hide behind whichever one was measured."""

    def test_both_numbers_are_in_the_artefact_with_the_inflation_between_them(self, p5_run: dict[str, Any]):
        report = _phase_report(p5_run)
        assert report["schema"] == PHASE_REPORT_SCHEMA
        entry = report["comparison"]["GET /api/v1/health"]
        assert entry["cold_median_ms"] == 300.0, "the cold figure must survive into the report"
        assert entry["warm_median_ms"] == 30.0
        assert entry["inflation_ratio"] == 10.0
        assert entry["cold_requests"] > entry["warm_requests"], (
            "the cold window carries the spawn ramp and the warm one does not; if these were equal "
            "the two figures would not be describing different populations"
        )
        assert any("cold and warm reported separately" in label for label in report["labels"])

    def test_the_hold_is_read_and_never_reported(self, p5_run: dict[str, Any]):
        report = _phase_report(p5_run)
        phases = report["phases"]
        assert phases["cold"]["reported"] is True
        assert phases["warm"]["reported"] is True
        assert phases["hold"]["reported"] is False
        assert "not a result" in phases["hold"]["discarded_reason"]
        assert "hold" not in report["comparison"], "a phase that is not reported must not appear in the comparison"
        assert set(phases) == set(PHASES)

    def test_no_hold_request_can_reach_a_reported_percentile(self, p5_run: dict[str, Any]):
        """Criterion (d).

        The hold is a SEPARATE locust process writing SEPARATE files, so the
        exclusion is structural rather than a subtraction that could be got wrong.
        The test states it the same way: the hold's median is the one value that
        appears nowhere in the reported figures, and the reported population is the
        measured phase's own.
        """
        report = _phase_report(p5_run)
        hold = report["phases"]["hold"]
        warm = report["phases"]["warm"]
        name = "GET /api/v1/health"
        assert hold["endpoints"][name]["quantiles_ms"]["0.5"] == 9999.0, "the fixture's hold must stay absurd"
        reported = warm["endpoints"][name]["quantiles_ms"]
        assert 9999.0 not in reported.values(), "the hold's latency reached a reported percentile"
        assert reported["0.5"] == 30.0
        assert warm["csv_prefix"] != hold["csv_prefix"]
        assert warm["requests"] == p5_run["warm_clock"].total

    def test_the_reset_boundary_lands_after_the_hold_and_defines_the_population(self, p5_run: dict[str, Any]):
        """Criterion (a): the reset happens AFTER the hold, asserted with a fake clock.

        Both numbers come from the same clock the CSVs were written on, so this is
        arithmetic over recorded timestamps rather than a timing coincidence.
        """
        report = _phase_report(p5_run)
        hold_last = report["phases"]["hold"]["window"]["last_timestamp"]
        reset = report["reset"]
        assert reset["detected"] is True, "the fixture's reset must be observable"
        assert p5_run["warm_clock"].reset_at >= hold_last, "the fake clock reset before the hold ended"
        assert reset["at_timestamp"] > p5_run["warm_clock"].reset_at
        assert report["warm_population"]["verified"] is True
        assert report["warm_population"]["reported_requests"] == reset["requests_after_reset"], (
            "the reported percentiles are the post-reset population only if their own request "
            "count is the one the history accounts for after the reset"
        )
        assert report["warm_population"]["reported_requests"] == 150
        assert report["warm_population"]["post_reset_requests"] == 150

    def test_a_reset_that_precedes_the_end_of_the_hold_voids_the_run(self, tmp_path: Path):
        """The phases overlap: the measured window opened while the hold was still
        running, so the hold cannot have preceded the reset."""
        prefixes = {"cold": tmp_path / "c", "hold": tmp_path / "h", "warm": tmp_path / "w"}
        _write_locust_csvs(prefixes["cold"], endpoints=[_endpoint("GET /api/v1/health", 100, p50=300.0)],
                           history=[(t, int(t) - 900, 100) for t in (1000.0, 1010.0, 1020.0, 1030.0)])
        hold_times = (1040.0, 1050.0, 1060.0, 1070.0, 1080.0, 1090.0, 1100.0)
        _write_locust_csvs(prefixes["hold"], endpoints=[_endpoint("GET /api/v1/health", 600, p50=9999.0)],
                           history=[(t, int(t) - 900, 600) for t in hold_times])
        _write_locust_csvs(prefixes["warm"], endpoints=[_endpoint("GET /api/v1/health", 20, p50=30.0)],
                           history=[(1095.0, 100, 50), (1097.0, 5, 100), (1099.0, 15, 100)])
        with pytest.raises(PhaseTimelineInvalid) as caught:
            build_phase_report(manifest={"git": {}, "run": {}},
                               cold_prefix=prefixes["cold"], hold_prefix=prefixes["hold"], warm_prefix=prefixes["warm"])
        findings = str(caught.value)
        assert "--reset-stats boundary" in findings, findings
        assert "hold did not precede the reset" in findings, findings
        assert any("before hold finished" in f for f in caught.value.findings), (
            "overlapping windows are incoherent on their own terms and must be reported as such, "
            "not only as a reset-ordering problem"
        )

    def test_phases_that_did_not_run_in_order_are_refused(self, tmp_path: Path):
        prefixes = {"cold": tmp_path / "c", "hold": tmp_path / "h", "warm": tmp_path / "w"}
        _write_locust_csvs(prefixes["cold"], endpoints=[_endpoint("GET /api/v1/health", 100, p50=300.0)],
                           history=[(t, int(t) - 1000, 100) for t in (2000.0, 2010.0, 2020.0)])
        # The hold was written where the cold window's rows should be, and the cold
        # window where the hold's should be: the file names are right and the clock
        # says otherwise.
        _write_locust_csvs(prefixes["hold"], endpoints=[_endpoint("GET /api/v1/health", 600, p50=9999.0)],
                           history=[(t, int(t) - 1000, 600) for t in (1800.0, 1810.0, 1820.0)])
        _write_locust_csvs(prefixes["warm"], endpoints=[_endpoint("GET /api/v1/health", 20, p50=30.0)],
                           history=[(t, int(t) - 1000, 20) for t in (1900.0, 1910.0, 1920.0)])
        with pytest.raises(PhaseTimelineInvalid) as caught:
            build_phase_report(manifest={"git": {}, "run": {}},
                               cold_prefix=prefixes["cold"], hold_prefix=prefixes["hold"], warm_prefix=prefixes["warm"])
        findings = " ".join(caught.value.findings)
        assert "out of order" in findings, findings
        assert "hold began at" in findings, findings

    def test_a_phase_that_recorded_nothing_is_refused(self, p5_run: dict[str, Any]):
        report = _phase_report(p5_run)
        report["phases"]["cold"]["window"]["intervals"] = 0
        with pytest.raises(PhaseTimelineInvalid) as caught:
            assert_phase_report_valid(report)
        assert "no history intervals" in str(caught.value)

    def test_a_missing_phase_csv_voids_the_run(self, p5_run: dict[str, Any]):
        hold = p5_run["prefixes"]["hold"]
        hold.with_name(hold.name + LOCUST_STATS_SUFFIX).unlink()
        with pytest.raises(PhaseSamplesMissing) as caught:
            _phase_report(p5_run)
        assert "hold" in str(caught.value)
        assert "phase CSV" in str(caught.value)

    def test_a_phase_csv_with_no_rows_is_refused(self, p5_run: dict[str, Any]):
        """A phase locust never wrote is not a phase with zero requests, and the
        difference is the whole point: one is a measurement, the other is a gap."""
        cold = p5_run["prefixes"]["cold"]
        cold.with_name(cold.name + LOCUST_STATS_SUFFIX).write_text("", encoding="utf-8")
        with pytest.raises(PhaseSamplesMissing) as caught:
            _phase_report(p5_run)
        assert "no rows" in str(caught.value)

    def test_a_renamed_locust_column_is_refused_by_name(self, p5_run: dict[str, Any]):
        cold = p5_run["prefixes"]["cold"]
        cold.with_name(cold.name + LOCUST_STATS_SUFFIX).write_text(
            "Type,Name,Fails,Average Response Time,Min Response Time,Max Response Time,50%,95%,99%\n"
            ",GET /api/v1/health,0,300.0,270.0,360.0,300.0,330.0,360.0\n",
            encoding="utf-8",
        )
        with pytest.raises(PhaseSamplesMissing) as caught:
            _phase_report(p5_run)
        assert "Request Count" in str(caught.value), (
            "the refusal must name the column it could not find, not read a neighbouring one"
        )

    def test_an_endpoint_absent_from_the_cold_window_is_not_given_a_ratio(self, p5_run: dict[str, Any]):
        """A ratio against nothing is the arithmetic error this programme exists to
        stop, so the cell stays null rather than being filled with a guess."""
        warm = FakeClock(1_700_100_000.0)
        warm.advance(30, requests=20)
        names = ("GET /api/v1/health", "POST /api/v1/auth/token")
        _phase(p5_run["prefixes"]["warm"], warm, p50=30.0, names=names)
        entry = _phase_report(p5_run)["comparison"]["POST /api/v1/auth/token"]
        assert entry["cold_median_ms"] is None
        assert entry["cold_requests"] is None
        assert entry["inflation_ratio"] is None
        assert entry["warm_median_ms"] == 30.0

    def test_a_quantile_the_sample_cannot_resolve_is_carried_next_to_the_number(self, tmp_path: Path):
        """At n=12 a p99 IS the maximum, under any estimator. Reporting the number
        without saying so is how "208.47 ms p95" happened."""
        clock = FakeClock(1_700_200_000.0)
        clock.advance(24, requests=1)  # 12 intervals of 2 s, one request each
        prefix = _phase(tmp_path / "tiny", clock, p50=12.0)
        entry = read_locust_phase(prefix, phase="warm")["endpoints"]["GET /api/v1/health"]
        assert entry["requests"] == 12
        assert entry["quantiles_ms"]["0.99"] == 12.0 * 1.2
        assert entry["unresolvable_quantiles"] == [0.95, 0.99]
        assert "0.5" not in entry["unresolvable_quantiles"], "a median at n=12 is a real rank"

    def test_a_reset_that_never_happens_is_labelled_rather_than_assumed(self, p5_run: dict[str, Any]):
        """Locust's reset is an internal of another tool, so its absence is reported
        as one. Refusing would void every run on the only host that can produce the
        numbers at all."""
        warm = FakeClock(1_700_300_000.0)
        warm.advance(40, requests=20)  # no reset(): the cumulative count only climbs
        _phase(p5_run["prefixes"]["warm"], warm, p50=30.0)
        report = _phase_report(p5_run)
        assert report["reset"]["detected"] is False
        assert report["warm_population"]["verified"] is False
        labels = " ".join(report["labels"])
        assert "warm-phase-reset-unproven" in labels
        assert "warm-population-unverified" in labels
        assert "[Unverified - Needs-Linux-measure]" in labels, (
            "an unobserved internal cannot be asserted; it has to be labelled as still to be measured"
        )

    def test_the_report_names_the_run_it_belongs_to(self, p5_run: dict[str, Any]):
        report = _phase_report(p5_run)
        manifest = p5_run["manifest"]
        assert report["git"]["sha"] == manifest["git"]["sha"]
        assert report["run"] == manifest["run"]
        assert report["min_hold_seconds"] == MIN_HOLD_SECONDS

    def test_the_hold_floor_is_the_rule_and_the_rule_is_sixty_seconds(self):
        assert MIN_HOLD_SECONDS >= 60, "P-5 is '>=60 s at target concurrency before --reset-stats'"

    def test_the_report_is_written_under_a_directory_it_creates(self, p5_run: dict[str, Any]):
        report = _phase_report(p5_run)
        out = p5_run["dir"] / "reports" / "baseline_phases.json"
        assert not out.parent.exists()
        assert write_phase_report(report, out) == out
        assert json.loads(out.read_text(encoding="utf-8"))["schema"] == PHASE_REPORT_SCHEMA


class TestThePhaseReportAsAProgram:
    def test_it_refuses_without_all_four_evidence_flags(self, capsys):
        rc = main(["--emit-phase-report"])
        err = capsys.readouterr().err
        assert rc == EXIT_PHASE_UNPROVEN
        for flag in ("--manifest", "--cold-csv", "--hold-csv", "--warm-csv"):
            assert flag in err, f"{flag} is not named in the refusal, so an operator cannot tell what is missing"

    def test_a_missing_manifest_voids_the_report(self, tmp_path: Path, p5_run: dict[str, Any], capsys):
        prefixes = p5_run["prefixes"]
        rc = main([
            "--emit-phase-report",
            "--manifest", str(tmp_path / "never-written.json"),
            "--cold-csv", str(prefixes["cold"]),
            "--hold-csv", str(prefixes["hold"]),
            "--warm-csv", str(prefixes["warm"]),
        ])
        assert rc == EXIT_NO_APP_HOST_REPORT
        assert "commit" in capsys.readouterr().err

    def test_it_writes_the_report_and_reports_both_phases(self, p5_run: dict[str, Any], tmp_path: Path, capsys):
        out = tmp_path / "reports" / "phases.json"
        prefixes = p5_run["prefixes"]
        rc = main([
            "--emit-phase-report",
            "--manifest", str(p5_run["manifest_path"]),
            "--cold-csv", str(prefixes["cold"]),
            "--hold-csv", str(prefixes["hold"]),
            "--warm-csv", str(prefixes["warm"]),
            "--output", str(out),
        ])
        out_text = capsys.readouterr().out
        assert rc == 0, out_text
        assert "reported=False" in out_text, "the discarded phase must be visible in the operator's output"
        assert "inflation=10.0x" in out_text
        written = json.loads(out.read_text(encoding="utf-8"))
        assert written["comparison"]["GET /api/v1/health"]["inflation_ratio"] == 10.0
        assert written["warm_population"]["verified"] is True

    def test_it_refuses_and_writes_nothing_when_a_phase_is_missing(
        self, p5_run: dict[str, Any], tmp_path: Path, capsys
    ):
        out = tmp_path / "phases.json"
        prefixes = p5_run["prefixes"]
        prefixes["hold"].with_name(prefixes["hold"].name + LOCUST_STATS_SUFFIX).unlink()
        rc = main([
            "--emit-phase-report",
            "--manifest", str(p5_run["manifest_path"]),
            "--cold-csv", str(prefixes["cold"]),
            "--hold-csv", str(prefixes["hold"]),
            "--warm-csv", str(prefixes["warm"]),
            "--output", str(out),
        ])
        assert rc == EXIT_PHASE_UNPROVEN
        assert not out.exists(), (
            "a phase report with a missing phase written into reports/ would be globbed up as a valid record"
        )
        assert "phase" in capsys.readouterr().err

    def test_the_hold_floor_is_printed_for_the_runner_to_read(self, capsys):
        assert main(["--print-min-hold-seconds"]) == 0
        assert capsys.readouterr().out.strip() == str(MIN_HOLD_SECONDS)
