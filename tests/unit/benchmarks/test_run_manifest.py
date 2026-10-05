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
import json
import re
import subprocess
from pathlib import Path

import pytest

from src.app.core.config import settings
from src.benchmarks.run_manifest import (
    APP_ENTRYPOINT,
    HARNESS_FILES,
    LOAD_SCRIPT,
    MANIFEST_SCHEMA,
    OUTPUT_PATH_PREFIXES,
    HarnessFileState,
    HarnessNotPinned,
    assert_harness_pinned,
    build_manifest,
    harness_state,
    harness_states,
    unpinned_harness_files,
    write_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "run_perf_baseline.sh"


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

        RED until the harness promotion is committed: ``src/benchmarks/`` was
        created in this session and is still untracked, so the gate is correct to
        refuse. This assertion is deliberately not conditioned on the git state -
        a test that passes either way would not be testing (a).
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
    def manifest(self, pinned_repo: Path):
        original = settings.security.AUTO_BAN_ENABLED
        settings.security.AUTO_BAN_ENABLED = False
        try:
            yield build_manifest(run_params={"users": "100", "host": "http://127.0.0.1:8000"}, repo_root=pinned_repo)
        finally:
            settings.security.AUTO_BAN_ENABLED = original

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
        assert manifest["run"] == {"host": "http://127.0.0.1:8000", "users": "100"}

    def test_it_records_every_security_budget_not_a_summary(self, manifest):
        """P-9a asks for ALL budgets. A summary is how two runs with different
        rate limiting get compared."""
        recorded = manifest["limits"]["security_budgets"]
        expected = {name: list(pair) for name, pair in sorted(settings.rate_limit.SECURITY_BUDGETS.items())}
        assert recorded == expected
        assert "auth" in recorded, "the budget the load script most relaxes must be present"

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
        assert manifest["host"]["platform"] == platform.system()
        assert manifest["host"]["cpu_count"] == manifest["host"]["cpu_count"]
        assert isinstance(manifest["host"]["cpu_count"], int)

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

    def test_it_labels_a_run_with_auto_ban_disabled(self, manifest):
        """The load script sets AUTO_BAN_ENABLED=false on purpose. Reporting a
        number produced that way without the label is a permanently rejected
        item, so the label has to travel in the artefact."""
        assert "AUTO_BAN_ENABLED=false" in manifest["labels"]

    def test_it_omits_the_auto_ban_label_when_auto_ban_is_on(self, pinned_repo: Path):
        original = settings.security.AUTO_BAN_ENABLED
        settings.security.AUTO_BAN_ENABLED = True
        try:
            labels = build_manifest(repo_root=pinned_repo)["labels"]
        finally:
            settings.security.AUTO_BAN_ENABLED = original
        assert "AUTO_BAN_ENABLED=false" not in labels

    def test_it_labels_a_non_linux_run_as_non_representative(self, pinned_repo: Path, monkeypatch):
        """A-5: a Windows number may never serve as a `before` arm. The label must
        be in the artefact, not in a reviewer's memory."""
        import src.benchmarks.run_manifest as mod

        monkeypatch.setattr(mod.platform, "system", lambda: "Windows")
        labels = build_manifest(repo_root=pinned_repo)["labels"]
        assert any("NON-REPRESENTATIVE" in label for label in labels)

    def test_it_always_labels_the_relaxed_limits_and_the_shared_runner(self, manifest):
        assert "SECURITY_LIMITS_RELAXED_BY_RUN_PERF_BASELINE" in manifest["labels"]
        assert any("not a capacity measurement" in label for label in manifest["labels"])

    def test_it_refuses_to_build_when_the_harness_is_dirty(self, pinned_repo: Path):
        """A faithful record of an unreproducible run is worse than no artefact:
        it looks complete."""
        (pinned_repo / HARNESS_FILES[1]).write_text("# dirty\n", encoding="utf-8")
        with pytest.raises(HarnessNotPinned):
            build_manifest(repo_root=pinned_repo)

    def test_it_refuses_to_build_without_a_readable_head(self, pinned_repo: Path):
        _git(pinned_repo, "checkout", "--orphan", "no-commits-yet")
        _git(pinned_repo, "rm", "-rq", "--cached", ".")
        for rel in HARNESS_FILES:
            (pinned_repo / rel).unlink()
        with pytest.raises((HarnessNotPinned, RuntimeError)):
            build_manifest(repo_root=pinned_repo)

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
        assert len(found) >= 4, f"the scan found almost nothing: {sorted(found)}"


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

    def test_the_gate_runs_before_the_server_starts(self):
        """A gate after the load test has measured nothing is decoration."""
        body = _script_body()
        assert body.index("src.benchmarks.run_manifest") < body.index("uvicorn src.app.main:app")
        assert body.index("src.benchmarks.run_manifest") < body.index("setup_locust_test_data.py")

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
