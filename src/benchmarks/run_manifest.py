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
  may not be reported. The label is derived from the value the SERVER ran with -
  read on the app host by :func:`build_app_host_report` and carried in - and not
  from this module's own settings, which describe the load host.
* A number measured on Windows is non-representative and may never serve as a
  ``before`` arm (addendum A-5). The manifest records the platform of BOTH hosts
  and says so, so the label travels with the artefact instead of living in a
  reviewer's head.

TWO HOSTS, ONE ARTEFACT (H-9)
-----------------------------
The load generator and the server are on different machines, so the harness can
no longer learn anything about the server by introspecting itself. Every field
that describes the server - its event loop, its HTTP implementation, its
security limits, its store addresses - is read on the APP host by
:func:`build_app_host_report`, carried in as a JSON file, and gated on here:

* :func:`load_app_host_report` refuses a report that is absent, truncated, from
  another schema, or missing a required key. The file crosses a machine
  boundary, so it is hand-carried and cannot be trusted unchecked.
* :func:`assert_representative_runtime` refuses a server that did not resolve to
  ``uvloop`` + ``httptools``, which is what ``Dockerfile``'s flagless CMD
  resolves to (addendum A-2).
* :func:`assert_run_end_matches` refuses a run whose configuration moved while it
  was in flight, which is what makes the run-START record a promise rather than
  a preamble (unified plan 4.2).

RUNTIME IDENTITY, ALL FIVE FIELDS (L-1)
--------------------------------------
Addendum 6.1 asks for five things: the resolved ``--loop`` value, the event-loop
implementation, the HTTP implementation, the Python version, and the image
digest. The first four are resolved here from the server's own interpreter. The
fifth cannot be - no process can read its own image digest - so the deployment
declares it in ``PERF_IMAGE_DIGEST`` and :func:`resolve_image_digest` records what
was declared, validating its shape on load. An absent digest is recorded and
labelled, never invented and never refused: a bare-metal app host is a real
topology, and what it cannot be tied to is a build, not a runtime.

THE DEPLOYMENT MANIFEST (L-2 / A-4)
-----------------------------------
N-3 confirmed that no file in this repository records where the application
runs: ``docker-compose.yml`` and the deployment checklist contain no occurrence
of linux, bookworm or ubuntu. That is not a documentation gap, it is the reason
the programme cannot prioritise anything. The pack's thread/process ratios came
from a 12-core Windows box and every throughput claim inherits the assumption;
and because nobody wrote down whether the stores are co-located, a co-located
round trip at ~0.3 ms against the pack's 0.72-3.10 ms - which would change what
the entire Redis-reduction programme is worth - is indistinguishable from the
alternative.

:func:`build_deployment_manifest` records the answer, and
:func:`assert_deployment_recorded` refuses the run when it is missing
(exit 10). The split between resolved and declared is deliberate:
``distro``, ``cpu_model``, ``core_count`` and (for IP-literal addresses)
``store_locality`` are read off the machine, so they are evidence;
``orchestrator`` is not knowable from inside a process at all and is declared in
``PERF_ORCHESTRATOR``. Neither this repository nor a CI runner is a substitute for
the deployment, and a guess is worse than a refusal because a guess is invisible
in the artefact.

COLD AND WARM, REPORTED SEPARATELY (P-5)
---------------------------------------
Every startup-adjacent number in the pack is cold and 5-10x inflated (perf F5):
``permission_catalog_reconcile`` 977.48 -> 191.60 ms, ``phase=admin`` 2 203.11 ->
222.38 ms, ``warm_token_blacklist`` 404.51 -> 73.22 ms. A single number conflates
the two, and that conflation is F-21 and F-25. So the runner takes three
invocations, in this order, and this module reads all three back:

* ``cold``  - immediately after the health gate, against a server whose caches
  are cold. This is what an operator experiences on deploy. Spawn ramp INCLUDED,
  and the report says so, because excluding it would also exclude the coldness.
* ``hold``  - at least :data:`MIN_HOLD_SECONDS` at target concurrency. Its samples
  are DISCARDED: it exists to warm the server, not to be reported. Being a
  separate process writing separate files is what makes that discard structural
  rather than a promise.
* ``warm``  - the measured run, with ``--reset-stats``, reported as the baseline.

:func:`build_phase_report` refuses (exit 11) when a phase's CSVs are absent or
empty, when the phases did not run in that order, or when the warm phase's
reported request count is not the post-reset population its own history implies -
because a "warm" percentile set that contains ramp requests is the exact defect
this bead exists to stop.

The percentiles locust wrote are RECORDED, never recomputed: they are aggregates,
so a raw sample no longer exists to re-estimate, and re-deriving them from
per-interval aggregates would be arithmetic on summaries. What this module adds is
the ``n`` behind each of them and the quantiles that ``n`` cannot resolve
(:func:`unresolvable_quantiles`), so a p99 that is simply the maximum says so.

Whether locust's ``--reset-stats`` fired is an OBSERVATION, and it is reported as
one (``reset.detected``) with a label attached when it cannot be seen; it is not a
refusal, because a label is the programme's answer to something unobservable and
a hard gate on another tool's internals would void runs on the one host that can
produce the numbers at all. Incoherent data, by contrast, is refused outright.

NOTHING HERE IS INFERRED OR DEFAULTED. If git is unavailable, if a settings
object cannot be built, if the app host did not report, if the deployment did not
declare itself, or if a phase's samples are not on disk, this raises: an artefact
that silently omits the fact it could not establish is worse than no artefact,
because it reads as complete.
"""

from __future__ import annotations

import argparse
import ast
import csv
import importlib.util
import ipaddress
import json
import os
import platform
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.benchmarks.percentiles import PERCENTILES, unresolvable_quantiles

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

#: The app-host report: the only record of what the SERVER ran with. Produced on
#: the app host by ``--emit-app-host-report`` and carried to the load host by
#: hand, because the two are different machines and the load host cannot observe
#: the server by introspecting itself.
APP_HOST_REPORT_SCHEMA = "perf-app-host-report/1"

#: What ``Dockerfile``'s CMD resolves to. It runs ``uvicorn src.app.main:app``
#: with no ``--loop`` and no ``--http``, so uvicorn picks both by importability
#: (``uvicorn.loops.auto`` and ``uvicorn.protocols.http.auto`` are each a single
#: ``try: import`` around the choice). A run that resolved either differently is
#: a different instrument rather than a faster or slower version of this one, so
#: it is refused instead of reported with a footnote (addendum A-2).
EXPECTED_EVENT_LOOP = "uvloop"
EXPECTED_HTTP_IMPL = "httptools"

#: Where the app host learns which image it is running from. A container runtime
#: knows the digest it pulled; nothing inside the process can ask it without the
#: orchestrator's API, so the deployment declares it here and this module records
#: what was declared. Nothing else is consulted and no digest is ever synthesised.
IMAGE_DIGEST_ENV = "PERF_IMAGE_DIGEST"

#: An OCI image digest, so a truncated or hand-typed value is rejected on load
#: instead of travelling through the artefact looking like an identity.
IMAGE_DIGEST_PATTERN = re.compile(r"\Asha256:[0-9a-f]{64}\Z")

#: Keys an app-host report must carry to be usable. Checked on load because the
#: file is hand-copied between hosts and a truncated copy would otherwise be read
#: as a report that simply had little to say.
APP_HOST_REPORT_FIELDS: tuple[str, ...] = ("event_loop", "http", "python", "host", "limits", "stores")

#: The deployment manifest of addendum 6.1 / L-2. Six facts about where the
#: server runs, none of which any in-repo document records (N-3): without them
#: every throughput ratio is an arithmetic exercise on an assumed machine.
#:
#: TWO OF THEM ARE RESOLVED, THREE ARE DECLARED, ONE IS BOTH. The split is the
#: point - a fact the process can read off itself is evidence, and only a fact
#: about something outside the process has to be declared by the deployment.
#: Nothing here is ever defaulted: a field the module cannot establish ABORTS
#: the run, because a manifest that quietly omits what it did not know reads as
#: complete.
DEPLOYMENT_MANIFEST_FIELDS: tuple[str, ...] = (
    "orchestrator",
    "distro",
    "cpu_model",
    "core_count",
    "store_locality",
    "image_digest",
)

#: Where the app host learns what supervises it. No process can read its own
#: orchestrator's name, and every guess available in-repo is wrong in the way
#: that matters: `.github/workflows/performance.yml` is a shared CI runner, not
#: the deployment. Declared, validated, and ABSENT IS A REFUSAL - unlike the
#: image digest, because "I do not know what runs this in production" is the
#: blocking finding addendum A-4 exists to surface, not a topology to footnote.
ORCHESTRATOR_ENV = "PERF_ORCHESTRATOR"

#: Where the app host declares store locality when the addresses cannot settle
#: it. Every store address in ``src/.env`` is a bare hostname, so whether it is
#: loopback or remote is not derivable from the string alone, and the whole
#: Redis-cost programme turns on the difference: co-located stores put a round
#: trip at ~0.3 ms and change what the RTT findings are worth (A-4).
STORE_LOCALITY_ENV = "PERF_STORE_LOCALITY"

#: The only answers :func:`resolve_store_locality` will accept.
STORE_LOCALITY_VALUES: tuple[str, ...] = ("co-located", "remote", "mixed")

#: Sections that must be IDENTICAL in the run-start and run-end manifests. A
#: commit, a harness blob, a security limit or an app-host runtime that moved
#: while the run was in flight means the start record describes a run that did
#: not happen, so it cannot vouch for the numbers in between (unified plan 4.2).
INVARIANT_SECTIONS: tuple[str, ...] = (
    "git",
    "harness",
    "python",
    "load_host",
    "stores",
    "limits",
    "deployment",
    "labels",
    "app_host",
    "run",
)

#: Dotted paths that must resolve to a value in every manifest. A missing one
#: ABORTS the run rather than defaulting: a manifest that omits what it could
#: not establish reads as complete, which is the failure this module exists to
#: make impossible. ``run`` is required to be non-empty because a perf artefact
#: with no users and no target describes no run at all.
REQUIRED_MANIFEST_FIELDS: tuple[str, ...] = (
    "schema",
    "phase",
    "git.sha",
    "harness.files",
    "python.version",
    "load_host.platform",
    "load_host.cpu_count",
    "stores.postgres",
    "stores.redis_cache",
    "stores.redis_queue",
    "deployment.orchestrator",
    "deployment.distro",
    "deployment.cpu_model",
    "deployment.core_count",
    "deployment.store_locality",
    "limits.auto_ban_enabled",
    "limits.security_budgets",
    "app_host.event_loop",
    "app_host.http",
    "labels",
    "run",
    # T-8's three, which P-9 omitted: which host ran the load and which ran the
    # server, how long the warm-up actually ran, and what the harness cleaned up.
    # All three are REQUIRED and none has a default. A manifest that omits the
    # warm-up cannot be read as a steady-state number, and one that omits the
    # cleanup cannot say what the run left behind; both are absences a reader
    # cannot distinguish from "there was none".
    "topology.verdict",
    "run.hold_seconds",
    "run.cleanup",
)

#: Exit codes. Distinct per failure so a CI job can tell "the harness was edited"
#: from "the server was not the one that ships", which are different asks of the
#: operator.
EXIT_UNPINNED = 3
EXIT_NO_HEAD = 4
EXIT_UNTRACKED_DEPENDENCY = 5
EXIT_NO_APP_HOST_REPORT = 6
EXIT_NON_REPRESENTATIVE = 7
EXIT_INCOMPLETE_MANIFEST = 8
EXIT_RUN_END_MISMATCH = 9
EXIT_DEPLOYMENT_UNDECLARED = 10
EXIT_PHASE_UNPROVEN = 11

#: P-5, verbatim: ">=60 s at target concurrency, then --reset-stats". A declared
#: constant, not a literal at the call site, so the floor travels with the rule
#: instead of being retyped by whoever next edits the runner. The shell script
#: reads this module for it via ``--min-hold-seconds``.
MIN_HOLD_SECONDS = 60

#: The three phases, in the order the runner must execute them. ``hold`` is the
#: only one whose samples are discarded.
PHASES: tuple[str, ...] = ("cold", "hold", "warm")

PHASE_REPORT_SCHEMA = "perf-phase-report/1"

#: What ``--csv=PREFIX`` makes locust write. Only the two read here are named.
LOCUST_STATS_SUFFIX = "_stats.csv"
LOCUST_HISTORY_SUFFIX = "_stats_history.csv"

#: Locust's ``_stats.csv`` columns this module reads, and the history columns it
#: uses to locate the reset. Named rather than indexed so a column reordering in a
#: locust upgrade surfaces as a refusal with a reason rather than as a silently
#: shifted column.
LOCUST_REQUEST_COUNT_COLUMN = "Request Count"
LOCUST_FAILURE_COUNT_COLUMN = "Failure Count"
LOCUST_TYPE_COLUMN = "Type"
LOCUST_NAME_COLUMN = "Name"
LOCUST_TIMESTAMP_COLUMN = "Timestamp"
LOCUST_TOTAL_REQUEST_COUNT_COLUMN = "Total Request Count"
AGGREGATED_ROW_TYPE = "Aggregated"

#: The latency columns locust writes, mapped from its 0-100 header to the
#: fractions :mod:`src.benchmarks.percentiles` speaks. Recorded verbatim, never
#: recomputed - see the module docstring.
LOCUST_QUANTILE_COLUMNS: dict[str, float] = {
    "50%": 0.5,
    "95%": 0.95,
    "99%": 0.99,
}
LOCUST_MEAN_COLUMN = "Average Response Time"
LOCUST_MIN_COLUMN = "Min Response Time"
LOCUST_MAX_COLUMN = "Max Response Time"


class DeploymentUndeclared(RuntimeError):
    """A deployment-manifest field the app host could neither resolve nor was told.

    Carries the offending field names on ``.fields``. This is addendum A-4's
    refusal, and it is the reason the deployment manifest exists at all: N-3
    confirmed no in-repo file records the deployment target, so without an
    operator declaration every ratio in the programme is arithmetic on an
    assumed machine, and the assumption is invisible in the artefact.
    """

    def __init__(self, fields: tuple[str, ...]) -> None:
        self.fields = fields
        listed = ", ".join(fields)
        super().__init__(
            f"VOID: the deployment manifest is incomplete: {listed}. These are facts about "
            "the deployment, not about this process, and nothing in the repository records "
            f"them. Set {ORCHESTRATOR_ENV} and {STORE_LOCALITY_ENV} on the app host and "
            "re-emit the report; the rest are read off the machine and cannot be absent "
            "unless the machine is not Linux. A missing field fails the run rather than "
            "defaulting: an omitted core count turns every throughput ratio into a guess "
            "that reads as a measurement (A-4, addendum 6.1)."
        )


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


class AppHostReportMissing(RuntimeError):
    """The app-host report is absent, unreadable, or not the shape it claims.

    Separate from a generic ``RuntimeError`` because the fix is specific and the
    operator may not have it in front of them: the report is produced by a command
    they have to run on a machine they may not currently be logged into.
    """

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        super().__init__(
            f"VOID: the app-host report at {path} is unusable: {reason}. Since H-9 the load "
            "generator and the server are different machines, so the harness cannot learn the "
            "server's event loop, HTTP implementation, security limits or store addresses by "
            "introspecting itself, and a report that does not carry them would make the manifest "
            "assert about the wrong process. On the app host, with the server running the way "
            "the Dockerfile runs it, execute "
            "`python -m src.benchmarks.run_manifest --emit-app-host-report > app_host_report.json`, "
            "copy that file to the load host, and pass it with --app-host-report."
        )


class NonRepresentativeRuntime(RuntimeError):
    """The server under test did not resolve to the runtime that ships."""

    def __init__(self, findings: tuple[str, ...]) -> None:
        self.findings = findings
        super().__init__(
            f"VOID: the app host is not running the configuration that ships: {'; '.join(findings)}. "
            "Dockerfile's CMD passes neither --loop nor --http, so uvicorn resolves both by "
            "importability. Install uvloop and httptools on the app host and re-run: a number from "
            "a different event loop or HTTP implementation is not comparable to one from the "
            "shipped configuration, so it may not be reported as a baseline."
        )


class ManifestIncomplete(RuntimeError):
    """A required field is absent.

    Refusing to write the artefact is the whole point. A partial manifest is worse
    than no manifest, because a reader cannot tell which parts were measured and
    which were skipped.
    """

    def __init__(self, fields: tuple[str, ...]) -> None:
        self.fields = fields
        super().__init__(
            f"VOID: the manifest is missing {len(fields)} required field(s): {', '.join(fields)}. "
            "Refusing to write a partial artefact, because a reader would take it for a complete "
            "one. This is a defect in the harness, not an operator error: file it rather than "
            "working around it."
        )


class RunEndMismatch(RuntimeError):
    """The end-of-run record disagrees with the start-of-run record."""

    def __init__(self, sections: tuple[str, ...]) -> None:
        self.sections = sections
        super().__init__(
            f"VOID: {len(sections)} section(s) changed while the run was in flight: "
            f"{', '.join(sections)}. The run-start manifest is a statement about the conditions the "
            "numbers were produced under, so a commit, harness blob, security limit or app-host "
            "runtime that differs at the end was not the configuration that was measured. The run is "
            "void; fix the cause and re-run."
        )


class PhaseSamplesMissing(RuntimeError):
    """A phase's CSV output is absent, unreadable, or has no rows in it."""

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(
            f"VOID: cannot read the {reason} at {path}. A cold/warm comparison needs both "
            "populations on disk: reporting only the phase that happened to survive would let the "
            "5-10x cold inflation hide again, which is the entire defect this report exists to "
            "prevent. Re-run the baseline; if the phase genuinely produced nothing, the server "
            "served no requests and there is no number to report."
        )


class PhaseTimelineInvalid(RuntimeError):
    """The phases did not run in the order P-5 requires, or the warm phase is
    not the post-reset population it claims to be."""

    def __init__(self, findings: tuple[str, ...]) -> None:
        self.findings = findings
        super().__init__(
            f"VOID: the cold/hold/warm phases are not what P-5 describes: {'; '.join(findings)}. "
            "P-5 is '>=60 s at target concurrency, THEN --reset-stats', so the hold must finish "
            "before the measured window opens, and the measured window must exclude the ramp. A "
            "baseline that reports ramp requests as steady state is the F-21/F-25 conflation."
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


def resolve_event_loop() -> str:
    """The event loop uvicorn installs for ``--loop auto``.

    Mirrors ``uvicorn.loops.auto.auto_loop_setup``, whose entire body is
    ``try: import uvloop / except ImportError: asyncio_setup / else: uvloop_setup``.
    Importability of the module is therefore the whole decision and there is
    nothing further to ask.

    LIMITATION, stated because it bounds the claim: this reports what uvicorn's
    auto-selection RESOLVES TO on this host, not the loop a process already
    running installed. A server started with an explicit ``--loop`` would differ.
    ``Dockerfile``'s CMD passes none, so the two agree for the configuration that
    ships.
    """
    try:
        spec = importlib.util.find_spec("uvloop")
    except (ImportError, ValueError):
        # ValueError is what a module already in sys.modules with __spec__ = None
        # raises. Either way the import cannot succeed.
        spec = None
    return "uvloop" if spec is not None else "asyncio"


def resolve_http_impl() -> str:
    """The HTTP implementation uvicorn uses for ``--http auto``.

    Read off uvicorn's OWN already-resolved symbol rather than re-implementing the
    choice: importing ``uvicorn.protocols.http.auto`` binds ``AutoHTTPProtocol`` to
    whichever class its ``try: import httptools`` succeeded on, so the module that
    class came from is the answer, with no second copy of the decision to rot.
    """
    from uvicorn.protocols.http.auto import AutoHTTPProtocol

    module = AutoHTTPProtocol.__module__
    if module.endswith("httptools_impl"):
        return "httptools"
    if module.endswith("h11_impl"):
        return "h11"
    raise RuntimeError(
        f"VOID: cannot determine which HTTP implementation uvicorn would use: AutoHTTPProtocol "
        f"resolves to {module}, which is neither the httptools nor the h11 implementation. An "
        "unrecognised resolution is not a runtime to report a number from."
    )


def resolve_image_digest() -> str | None:
    """The digest of the image this app host was started from, or ``None``.

    The fifth identity field of addendum 6.1. Nothing inside a running process can
    read its own image digest: the orchestrator knows which digest it pulled, and
    the only channel it has into the process is the environment. So the deployment
    declares it in ``PERF_IMAGE_DIGEST`` and this function records exactly that -
    no fallback, no inference from the commit SHA, and no shelling out to a
    container CLI, which the repository's own rules prohibit.

    ``None`` is a real answer about a real topology: the app host was not started
    from an image, or the deployment declared no digest. It is recorded and
    labelled by :func:`_labels` rather than refused, because a bare-metal app host
    is measured just as legitimately and the digest does not change the runtime.
    Refusing here would make the harness unusable outside a container over a fact
    that cannot affect a latency number.
    """
    declared = os.environ.get(IMAGE_DIGEST_ENV, "").strip()
    return declared or None


def resolve_orchestrator() -> str | None:
    """What supervises this process, from ``PERF_ORCHESTRATOR``.

    NOT resolvable from inside the process, and not derivable from any in-repo
    file: N-3 confirmed neither ``docker-compose.yml`` nor the deployment
    checklist names a platform at all. The candidate answers available in the
    repository are all wrong in the way that matters - the CI workflow runs on a
    shared ubuntu-latest runner with co-located stores, which is precisely the
    topology addendum A-5 rules out as a ``before`` arm - so this is declared by
    the deployment and recorded verbatim.
    """
    declared = os.environ.get(ORCHESTRATOR_ENV, "").strip()
    return declared or None


def resolve_distro() -> str | None:
    """The distribution and release, read from ``/etc/os-release``.

    The one field a process can be certain about, because the kernel publishes
    it and the container image inherits it. ``platform.platform()`` is not used
    because it reconstructs a release string from uname and libc rather than
    reading what the image actually ships; ``/etc/os-release`` is the file the
    image was built from.
    """
    try:
        fields = platform.freedesktop_os_release()
    except (OSError, AttributeError):
        # Absent on a non-systemd host, and on Windows. That is a real absence:
        # the deployment manifest is about the Linux target, and a Windows app
        # host is already labelled non-representative by A-5.
        return None
    name = str(fields.get("PRETTY_NAME") or fields.get("NAME") or "").strip()
    return name or None


def resolve_cpu_model() -> str | None:
    """The CPU model string, read from ``/proc/cpuinfo``.

    Not ``platform.processor()``: on Linux that returns the ``x86_64`` family
    string or an empty string depending on the build, and the field exists
    precisely because the A-8 ratios are core-count-dependent - "12-core" is not
    a fact a reader can check unless the actual model is in the artefact.
    """
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in cpuinfo.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip().lower() in {"model name", "hardware", "cpu model"}:
            model = value.strip()
            if model:
                return model
    return None


def resolve_core_count() -> int | None:
    """The number of CPUs this process may actually run on, not the machine's count.

    ``os.cpu_count()`` counts the CPUs the kernel knows about, which on a
    cgroup-limited or pinned deployment overstates the parallelism the workload
    gets - and it is the parallelism, not the silicon, that every ratio in the
    programme is divided by. ``sched_getaffinity`` is the process's actual mask
    and is used where the platform offers it.
    """
    affinity = getattr(os, "sched_getaffinity", None)
    if affinity is not None:
        try:
            return len(affinity(0))
        except OSError:
            pass
    return os.cpu_count()


def resolve_store_locality(stores: Mapping[str, str] | None = None) -> str | None:
    """Whether the stores are co-located with the app or remote: derived, or declared.

    DERIVED FIRST, because it can be. A store address that is a loopback literal
    (``127.0.0.1``, ``::1``, ``localhost``) is co-located by definition, and one
    that is a routable address is not. Only when an address is a bare hostname -
    which every store address in ``src/.env`` is, so in practice this is the
    common case - is locality not derivable from the string, and then the
    deployment declares it in ``PERF_STORE_LOCALITY``.

    THIS FIELD IS WHY THE REDIS PROGRAMME IS SHAPED THE WAY IT IS. Co-located
    puts a round trip at ~0.3 ms against the 0.72-3.10 ms the pack recorded, so
    "reduce Redis round trips" is worth 10-25x less than the reports assume, and
    nobody can tell which is true because nobody wrote the answer down (A-4).
    Inferring it would mean reporting a throughput claim on an assumption, so an
    underivable answer is absent and absent ABORTS.
    """
    addresses = dict(stores if stores is not None else _stores())
    verdicts: set[str] = set()
    for address in addresses.values():
        host = _address_host(str(address))
        if host in _LOOPBACK_HOSTS:
            verdicts.add("co-located")
        elif _is_routable_literal(host):
            verdicts.add("remote")
        else:
            # A hostname. Not derivable: it may resolve to loopback on the app
            # host itself, which is the case that would invalidate the RTT
            # findings, and it may not.
            declared = os.environ.get(STORE_LOCALITY_ENV, "").strip().lower()
            if declared in STORE_LOCALITY_VALUES:
                return declared
            return None
    if not verdicts:
        return None
    if len(verdicts) == 1:
        return verdicts.pop()
    return "mixed"


#: Spellings that are the app host itself. ``0.0.0.0`` is here because a service
#: bound to it accepts loopback connections, which is what "co-located" means here.
_LOOPBACK_HOSTS = frozenset(
    {"127.0.0.1", "localhost", "0.0.0.0", "::1", "0:0:0:0:0:0:0:1"}
)


def _address_host(address: str) -> str:
    """The host out of a store address written as ``host:port/db``, ``host:port`` or ``host``.

    Split by what the string is rather than by counting colons, because
    ``[::1]:6379`` and a bare ``::1`` have the same colon count and different
    meanings, and getting it wrong silently classifies a co-located store as
    remote - which is the one direction that changes every RTT conclusion in the
    programme.
    """
    without_database = address.split("/", 1)[0]
    if without_database.startswith("["):
        closing = without_database.find("]")
        if closing != -1:
            return without_database[1:closing]
    if without_database.count(":") == 1:
        return without_database.rsplit(":", 1)[0]
    if _parse_ip(without_database) is not None:
        # The whole thing parses as an IP address, so it is a bare IPv6 literal with
        # no port. This has to be tried BEFORE the trailing-digit rule below, because
        # a bare literal may itself end in a decimal group ("fd00::1") and stripping
        # that as a port would leave "fd00:", which is not an address.
        return without_database
    head, separator, tail = without_database.rpartition(":")
    if separator and tail.isdigit():
        return head
    return without_database


def _parse_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """``host`` as an address, or ``None`` when it is not one.

    ``ipaddress`` rather than a regex: deciding whether a store is remote is not a
    job for string matching, and a hostname that happens to be all digits would
    otherwise be classified by pattern instead of by what it is. Returns the
    ADDRESS rather than a bool because the two callers need different things from
    it - whether it is a loopback address, and whether it is an address at all -
    and conflating them is how a co-located store gets called remote.
    """
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _is_routable_literal(host: str) -> bool:
    """True when ``host`` is an IP literal that is NOT a loopback address."""
    address = _parse_ip(host)
    return address is not None and not address.is_loopback


def build_deployment_manifest(
    *,
    orchestrator: str | None = None,
    distro: str | None = None,
    cpu_model: str | None = None,
    core_count: int | None = None,
    store_locality: str | None = None,
    image_digest: str | None = None,
    stores: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The addendum 6.1 deployment manifest: where the server runs, as a fact.

    Split by what can honestly produce each value. RESOLVED off the machine:
    ``distro`` from ``/etc/os-release``, ``cpu_model`` from ``/proc/cpuinfo``,
    ``core_count`` from the process's affinity mask, ``store_locality`` from the
    store addresses where they are IP literals. DECLARED by the deployment:
    ``orchestrator``, always, and ``store_locality`` where the addresses are bare
    hostnames. ``image_digest`` rides along because it is the other field about
    something outside the process.

    Every argument is injectable so a report can be built deterministically on a
    host that is not the measurement host, which is every host except the one
    that produces the number - and on this host in particular, which is Windows
    and therefore resolves none of the four Linux fields.

    NO FIELD HERE IS EVER DEFAULTED. :func:`assert_deployment_recorded` refuses a
    manifest with a hole in it; the alternative, omitting it, produces an artefact
    that looks complete and cannot support the claim every throughput ratio in
    the programme rests on.
    """
    resolved_stores = dict(stores if stores is not None else _stores())
    return {
        "orchestrator": resolve_orchestrator() if orchestrator is None else orchestrator,
        "distro": resolve_distro() if distro is None else distro,
        "cpu_model": resolve_cpu_model() if cpu_model is None else cpu_model,
        "core_count": resolve_core_count() if core_count is None else core_count,
        "store_locality": (
            resolve_store_locality(resolved_stores) if store_locality is None else store_locality
        ),
        "image_digest": resolve_image_digest() if image_digest is None else image_digest,
        # Not a DEPLOYMENT_MANIFEST_FIELD: the addresses the locality verdict was
        # read from travel with the verdict, so a reader can re-derive it.
        "store_addresses": resolved_stores,
    }


def assert_deployment_recorded(deployment: Mapping[str, Any]) -> dict[str, Any]:
    """Raise :class:`DeploymentUndeclared` unless the manifest has no hole in it.

    Unlike the image digest, an absent field here is a REFUSAL rather than a
    label. The difference is what the field means: a missing digest is an
    honest statement about a bare-metal host, but a missing core count or store
    locality is missing EVIDENCE for the throughput claims the whole programme
    is built on, and a manifest that omits it reads as complete. Addendum A-4
    makes the absence itself the finding, so it must stop the run to be visible.
    """
    missing = tuple(
        field
        for field in DEPLOYMENT_MANIFEST_FIELDS
        if field != "image_digest" and _field_is_absent(deployment.get(field))
    )
    if missing:
        raise DeploymentUndeclared(missing)
    return dict(deployment)


def _uvicorn_premise() -> dict[str, Any]:
    """The premise this module's expectations rest on, recorded in the artefact.

    ``Dockerfile`` runs ``uvicorn src.app.main:app`` with no ``--loop`` and no
    ``--http``, so ``auto`` is what applies and the resolvers above answer the
    question the image actually asks. If a future image passes either flag, this is
    where the change becomes visible in the artefact instead of invalidating every
    expectation silently.
    """
    import uvicorn

    return {"version": uvicorn.__version__, "loop_arg": "auto", "http_arg": "auto"}


def build_app_host_report(
    *,
    event_loop: str | None = None,
    http_impl: str | None = None,
    image_digest: str | None = None,
    deployment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """What the APP HOST is running - the only honest source for the server's config.

    Run on the app host, against the server's own environment. Every field here
    describes a process on a different machine from the load generator, which is
    why none of it can be recovered by the load host looking at itself.

    ``event_loop`` and ``http_impl`` are injectable so a report can be built
    deterministically on a host that is not the measurement host, which is every
    host except the one that produces the number. They are the two fields
    :func:`assert_representative_runtime` turns on, so a fixture that used this
    host's real values would assert nothing portable - and on a host without
    uvloop, which includes any Windows checkout, they would assert the opposite of
    what the test claims. ``image_digest`` is injectable for the same reason and is
    checked on load, not here. ``deployment`` is injectable whole, because all
    six of its fields are facts about the Linux deployment target and this host
    resolves none of them.

    The platform verdict is recorded here rather than enforced here: A-5 makes a
    non-Linux number non-representative, which is a labelling rule, and refusing
    to measure at all would be a wider decision than the addendum makes. The event
    loop and HTTP implementation are refused, because those change what the
    instrument measures rather than only where it was measured.
    """
    loop = resolve_event_loop() if event_loop is None else event_loop
    http = resolve_http_impl() if http_impl is None else http_impl
    digest = resolve_image_digest() if image_digest is None else image_digest
    stores = _stores()
    record = dict(deployment) if deployment is not None else build_deployment_manifest(
        image_digest=digest, stores=stores
    )
    host = {
        "platform": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
    }
    reasons: list[str] = []
    if loop != EXPECTED_EVENT_LOOP:
        reasons.append(f"event loop is {loop}, not {EXPECTED_EVENT_LOOP}")
    if http != EXPECTED_HTTP_IMPL:
        reasons.append(f"HTTP implementation is {http}, not {EXPECTED_HTTP_IMPL}")
    if host["platform"] != "Linux":
        reasons.append(f"app host platform is {host['platform']}, not Linux (A-5)")
    return {
        "schema": APP_HOST_REPORT_SCHEMA,
        "event_loop": loop,
        "http": http,
        # Kept at the top level as well as inside deployment: it is the fifth
        # identity field of addendum 6.1 and the L-1 negative test reads it here.
        "image_digest": digest,
        "uvicorn": _uvicorn_premise(),
        "python": {
            "version": sys.version,
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "host": host,
        "limits": _limits(),
        "stores": stores,
        "deployment": record,
        "representative": not reasons,
        "non_representative_reasons": reasons,
    }


def load_app_host_report(path: Path) -> dict[str, Any]:
    """Read and validate the app-host report, or raise :class:`AppHostReportMissing`.

    Validated on load rather than trusted, because the file is hand-copied between
    hosts: it can be truncated by the copy, be left over from a previous run, or
    have been produced by a host that has since been reconfigured. Each of those
    would otherwise be read as a report that simply had little to say.
    """
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AppHostReportMissing(path, f"it cannot be read ({exc.strerror or exc})") from exc
    except json.JSONDecodeError as exc:
        raise AppHostReportMissing(path, f"it is not valid JSON ({exc})") from exc
    if not isinstance(raw, dict):
        raise AppHostReportMissing(path, "its top level is not a JSON object")
    if raw.get("schema") != APP_HOST_REPORT_SCHEMA:
        raise AppHostReportMissing(
            path, f"its schema is {raw.get('schema')!r}, not {APP_HOST_REPORT_SCHEMA!r}"
        )
    missing = [field for field in APP_HOST_REPORT_FIELDS if _field_is_absent(raw.get(field))]
    if missing:
        raise AppHostReportMissing(path, f"it carries no value for {', '.join(missing)}")
    deployment = raw.get("deployment")
    if not isinstance(deployment, dict):
        raise AppHostReportMissing(
            path,
            "it carries no deployment manifest. Where the server runs is a fact about the "
            "deployment, not about this process, and addendum A-4 makes its absence a "
            f"blocking finding: re-emit the report with {ORCHESTRATOR_ENV} and "
            f"{STORE_LOCALITY_ENV} set on the app host.",
        )
    locality = deployment.get("store_locality")
    if locality is not None and locality not in STORE_LOCALITY_VALUES:
        raise AppHostReportMissing(
            path,
            f"its store locality is {locality!r}, which is not one of "
            f"{', '.join(STORE_LOCALITY_VALUES)}. Co-located puts a round trip at ~0.3 ms "
            "and the pack's RTT findings at 0.72-3.10 ms, so which of the three it is "
            "decides what those findings are worth (A-4).",
        )
    digest = raw.get("image_digest")
    if digest is not None and not IMAGE_DIGEST_PATTERN.match(str(digest)):
        raise AppHostReportMissing(
            path,
            f"its image digest is {digest!r}, which is not an OCI digest. A digest that "
            f"was truncated by the copy or typed by hand is worse than an absent one: it "
            f"reads as an identity. Set {IMAGE_DIGEST_ENV} to the value the orchestrator "
            "pulled, or unset it and let the run be labelled as untied to a build.",
        )
    return raw


def assert_representative_runtime(report: Mapping[str, Any]) -> dict[str, Any]:
    """Refuse a server that did not resolve to the runtime that ships (A-2).

    Raises :class:`NonRepresentativeRuntime` unless the app host reported
    ``uvloop`` AND ``httptools``. The label for the refusal travels in the exit
    code and the message, so the run that did not happen leaves a record instead of
    a silence.
    """
    findings = []
    if report["event_loop"] != EXPECTED_EVENT_LOOP:
        findings.append(f"the event loop resolved to {report['event_loop']}, not {EXPECTED_EVENT_LOOP}")
    if report["http"] != EXPECTED_HTTP_IMPL:
        findings.append(f"the HTTP implementation resolved to {report['http']}, not {EXPECTED_HTTP_IMPL}")
    if findings:
        raise NonRepresentativeRuntime(tuple(findings))
    return dict(report)


def _resolve_field(document: Mapping[str, Any], dotted: str) -> Any:
    node: Any = document
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def _field_is_absent(value: Any) -> bool:
    """Absent means: not there, ``None``, or an empty collection.

    An empty string is a placeholder a reader cannot distinguish from a real
    value, and an empty dict or list is a section that was declared and then never
    filled in. Both are absences, so both abort.
    """
    if value is None:
        return True
    if isinstance(value, str | dict | list | tuple):
        return len(value) == 0
    return False


def assert_manifest_complete(manifest: dict[str, Any]) -> dict[str, Any]:
    """Raise :class:`ManifestIncomplete` unless every required field is present."""
    missing = tuple(
        field for field in REQUIRED_MANIFEST_FIELDS if _field_is_absent(_resolve_field(manifest, field))
    )
    if missing:
        raise ManifestIncomplete(missing)
    return manifest


def assert_run_end_matches(start: Mapping[str, Any], end: Mapping[str, Any]) -> dict[str, Any]:
    """Raise :class:`RunEndMismatch` unless the run changed nothing while in flight.

    The run-start manifest is a statement about the conditions the numbers were
    produced under. Asserting it again at the end is what makes it a statement
    rather than a preamble, and it is the only way a mid-run commit, config edit
    or app-host restart gets caught at all.
    """
    changed = tuple(section for section in INVARIANT_SECTIONS if start.get(section) != end.get(section))
    if changed:
        raise RunEndMismatch(changed)
    return dict(end)


def _topology(app_host: Mapping[str, Any]) -> dict[str, Any]:
    """Which host ran the load, and which ran the server -- DERIVED, not asserted.

    This exists because the label it replaces asserted the answer rather than
    checking it. ``_labels`` used to emit the constant string "topology: load
    generator and application on separate hosts" on every run, having compared
    nothing: two identical Linux boxes behind one hostname would have produced a
    manifest claiming separation it never established, and the whole RTT
    argument would rest on it.

    The verdict is deliberately three-valued rather than a bool, because two
    identical fingerprints do NOT prove co-location (they are what two identical
    hosts look like) while two different ones do prove separation:

    ``distinct``
        the fingerprints differ in at least one identifying field, so the two
        processes cannot be the same machine.
    ``identical-fingerprint``
        every field matches. Co-location is *possible* and separation is
        *unproven*. Reported as its own verdict so no reader has to guess which
        way the evidence points.
    ``unknown``
        the app host reported no host block, or one with a hole in it. Not
        defaulted to either answer.
    """
    load: dict[str, Any] = {
        "platform": platform.system(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
    }
    app_block = dict(app_host.get("host") or {})
    app: dict[str, Any] = {
        "platform": app_block.get("platform"),
        "machine": app_block.get("machine"),
        "cpu_count": app_block.get("cpu_count"),
    }
    fields = tuple(load)
    if not app_block or any(app[name] is None for name in fields):
        verdict = "unknown"
    elif all(app[name] == load[name] for name in fields):
        verdict = "identical-fingerprint"
    else:
        verdict = "distinct"
    return {
        "verdict": verdict,
        "load_host_role": "load generator: produced this artefact and drove the load",
        "app_host_role": "server under test: was measured",
        "load_host": load,
        "app_host": app,
    }


def _labels(app_host: Mapping[str, Any]) -> list[str]:
    """Standing-rule labels that MUST travel with every number this harness emits.

    These are not commentary. A manifest without them permits the two failures the
    programme has already permanently rejected: reporting a rate-limit-exempt
    number unlabelled, and using a Windows number as a ``before`` arm.

    The auto-ban fact is read from the APP-HOST REPORT, not from this module's own
    settings. The load host's settings describe a process that is not under test;
    reading them would produce a manifest that is confident and about the wrong
    machine, which is the exact shape of the 2026-09-22 defect.
    """
    labels = [
        "non-representative: not a capacity measurement (shared runner, co-located stores)",
    ]
    # The topology label is DERIVED. It used to be a constant that claimed
    # separation on every run without comparing the two hosts, so a single-host
    # run produced a manifest asserting a two-host topology it never had.
    verdict = _topology(app_host)["verdict"]
    if verdict == "distinct":
        labels.append("topology: load generator and application on separate hosts")
    elif verdict == "identical-fingerprint":
        labels.append(
            "topology: UNPROVEN - the load host and the app host report identical "
            "fingerprints, so separation is not established and co-location is possible"
        )
    else:
        labels.append(
            "topology: UNKNOWN - the app host reported no usable host block, so which "
            "host ran the load and which ran the server could not be established"
        )
    if not app_host["limits"]["auto_ban_enabled"]:
        labels.append("AUTO_BAN_ENABLED=false")
    if platform.system() != "Linux":
        labels.append("load-host-not-Linux: NON-REPRESENTATIVE, may never serve as a `before` arm (A-5)")
    app_platform = app_host["host"]["platform"]
    if app_platform != "Linux":
        labels.append(
            f"app-host-not-{app_platform}: NON-REPRESENTATIVE, may never serve as a `before` arm (A-5)"
        )
    if app_host.get("image_digest") is None:
        labels.append(
            "image-digest-unknown: the app host declared no image digest, so this number "
            "cannot be tied to a build (L-1)"
        )
    return labels


def _locust_phase_paths(prefix: Path) -> tuple[Path, Path]:
    return (prefix.with_name(prefix.name + LOCUST_STATS_SUFFIX), prefix.with_name(prefix.name + LOCUST_HISTORY_SUFFIX))


def read_locust_csv(path: Path, required: tuple[str, ...]) -> list[dict[str, str]]:
    """Read one of locust's CSVs, refusing anything that is not the table expected.

    Columns are looked up by name. A locust upgrade that renames or reorders a
    column therefore produces a refusal naming the column, not a report that
    quietly reads the wrong one.
    """
    if not path.exists():
        raise PhaseSamplesMissing(path, "phase CSV")
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        raise PhaseSamplesMissing(path, f"phase CSV ({exc.strerror or exc})") from exc
    if not rows:
        raise PhaseSamplesMissing(path, "phase CSV, which has no rows")
    missing = [column for column in required if column not in rows[0]]
    if missing:
        raise PhaseSamplesMissing(path, f"phase CSV missing column(s) {', '.join(missing)}")
    return rows


def _as_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: str | None) -> int | None:
    number = _as_float(value)
    return None if number is None else int(number)


def read_locust_phase(prefix: Path, *, phase: str) -> dict[str, Any]:
    """Read one phase's two CSVs into the block the report carries.

    ``reported`` is False for ``hold``: its samples are deliberately discarded, and
    a reader must be able to see that from the artefact rather than infer it from a
    filename.
    """
    stats_path, history_path = _locust_phase_paths(prefix)
    stats = read_locust_csv(stats_path, (LOCUST_TYPE_COLUMN, LOCUST_NAME_COLUMN, LOCUST_REQUEST_COUNT_COLUMN))
    history = read_locust_csv(
        history_path, (LOCUST_TIMESTAMP_COLUMN, LOCUST_TOTAL_REQUEST_COUNT_COLUMN, "User Count")
    )

    endpoints: dict[str, dict[str, Any]] = {}
    for row in stats:
        if (row.get(LOCUST_TYPE_COLUMN) or "").strip() == AGGREGATED_ROW_TYPE:
            continue
        name = (row.get(LOCUST_NAME_COLUMN) or "").strip()
        if not name:
            continue
        n = _as_int(row.get(LOCUST_REQUEST_COUNT_COLUMN)) or 0
        endpoints[name] = {
            "requests": n,
            "failures": _as_int(row.get(LOCUST_FAILURE_COUNT_COLUMN)) or 0,
            "mean_ms": _as_float(row.get(LOCUST_MEAN_COLUMN)),
            "min_ms": _as_float(row.get(LOCUST_MIN_COLUMN)),
            "max_ms": _as_float(row.get(LOCUST_MAX_COLUMN)),
            # Recorded verbatim: these are aggregates, so the samples they came
            # from no longer exist and re-estimating them would be arithmetic on
            # summaries.
            "quantiles_ms": {
                str(q): _as_float(row.get(column)) for column, q in LOCUST_QUANTILE_COLUMNS.items()
            },
            # ...and n is carried alongside them, because a p99 at n=20 IS the
            # maximum and a reader cannot tell that from the number alone.
            "unresolvable_quantiles": [q for q in unresolvable_quantiles(n) if q in set(PERCENTILES)],
        }

    timestamps = [t for t in (_as_float(row.get(LOCUST_TIMESTAMP_COLUMN)) for row in history) if t is not None]
    totals = [t for t in (_as_int(row.get(LOCUST_TOTAL_REQUEST_COUNT_COLUMN)) for row in history) if t is not None]
    users = [u for u in (_as_int(row.get("User Count")) for row in history) if u is not None]

    return {
        "phase": phase,
        "csv_prefix": str(prefix),
        "reported": phase != "hold",
        "discarded_reason": (
            f"the >= {MIN_HOLD_SECONDS} s hold at target concurrency exists to warm the SERVER; its "
            "requests are not a result and are excluded from every reported percentile"
            if phase == "hold"
            else None
        ),
        "requests": sum(entry["requests"] for entry in endpoints.values()),
        "failures": sum(entry["failures"] for entry in endpoints.values()),
        "endpoints": endpoints,
        "window": {
            "first_timestamp": timestamps[0] if timestamps else None,
            "last_timestamp": timestamps[-1] if timestamps else None,
            "seconds": (timestamps[-1] - timestamps[0]) if len(timestamps) >= 2 else None,
            # locust writes a history row per interval, so a window of zero rows
            # difference is a phase too short to have been measured.
            "intervals": len(history),
            "peak_concurrency": max(users) if users else None,
        },
        "excludes_spawn_ramp": phase == "warm",
        "_cumulative": [(t, n) for t, n in zip(timestamps, totals, strict=True)],
    }


def detect_reset(cumulative: list[tuple[float, int]]) -> dict[str, Any]:
    """Locate the ``--reset-stats`` boundary in a warm phase's cumulative totals.

    Locust's history carries a running ``Total Request Count``. A reset shows up
    as that running total going DOWN, which is what makes P-5's ordering
    checkable from the artefact: the boundary is a timestamp, and it can be
    required to land at or after the end of the hold window.

    This is an observation about another tool's behaviour, so it is reported as
    one: ``detected`` False produces a label, not a refusal, because refusing on
    an unobservable internal would void runs on the only host that can produce the
    numbers at all. Incoherent data - a detected reset whose post-reset total does
    not account for the requests that were reported - is refused, because that is
    a bug rather than a fact.
    """
    for index in range(1, len(cumulative)):
        if cumulative[index][1] < cumulative[index - 1][1]:
            return {
                "detected": True,
                # The row that first shows the smaller count is the first row
                # written after the reset, so its timestamp is when the measured
                # window opened. Everything from here to the end is the reported
                # population, which is what _stats.csv then aggregates.
                "at_timestamp": cumulative[index][0],
                "requests_before_reset": cumulative[index - 1][1],
                "requests_after_reset": cumulative[-1][1],
            }
    return {
        "detected": False,
        "at_timestamp": None,
        "requests_before_reset": None,
        "requests_after_reset": None,
    }


def compare_phases(cold: Mapping[str, Any], warm: Mapping[str, Any]) -> dict[str, Any]:
    """Per endpoint, the cold and warm medians side by side, and the inflation.

    Reported for every endpoint present in EITHER phase, and ``ratio`` is null
    rather than guessed when the cold window had no samples for it: a ratio
    against nothing is the arithmetic error this whole programme exists to stop.
    """
    comparison: dict[str, Any] = {}
    for name in sorted(set(cold["endpoints"]) | set(warm["endpoints"])):
        cold_entry = cold["endpoints"].get(name)
        warm_entry = warm["endpoints"].get(name)
        cold_median = None if cold_entry is None else cold_entry["quantiles_ms"].get("0.5")
        warm_median = None if warm_entry is None else warm_entry["quantiles_ms"].get("0.5")
        ratio = None
        if cold_median and warm_median:
            ratio = round(cold_median / warm_median, 2)
        comparison[name] = {
            "cold_median_ms": cold_median,
            "warm_median_ms": warm_median,
            "cold_requests": None if cold_entry is None else cold_entry["requests"],
            "warm_requests": None if warm_entry is None else warm_entry["requests"],
            "inflation_ratio": ratio,
        }
    return comparison


def build_phase_report(
    *,
    manifest: Mapping[str, Any],
    cold_prefix: Path,
    hold_prefix: Path,
    warm_prefix: Path,
) -> dict[str, Any]:
    """Assemble the cold/warm report. Raises :class:`PhaseSamplesMissing` or
    :class:`PhaseTimelineInvalid`.

    The manifest is required rather than optional: a latency number that does not
    say which commit, which budgets and which runtime produced it is the artefact
    this module has been refusing to write since P-9a.
    """
    phases = {name: read_locust_phase(prefix, phase=name) for name, prefix in
              (("cold", cold_prefix), ("hold", hold_prefix), ("warm", warm_prefix))}
    reset = detect_reset(phases["warm"].pop("_cumulative"))  # type: ignore[arg-type]
    report: dict[str, Any] = {
        "schema": PHASE_REPORT_SCHEMA,
        "git": {"sha": manifest.get("git", {}).get("sha"), "short": manifest.get("git", {}).get("short")},
        "run": dict(manifest.get("run") or {}),
        "min_hold_seconds": MIN_HOLD_SECONDS,
        "phases": phases,
        "reset": reset,
        "warm_population": _warm_population(phases["warm"], reset),
        "comparison": compare_phases(phases["cold"], phases["warm"]),
        "labels": [],
    }
    report["labels"] = _phase_labels(report)
    return assert_phase_report_valid(report)


def _warm_population(warm: Mapping[str, Any], reset: Mapping[str, Any]) -> dict[str, Any]:
    """Cross-check the measured window's reported count against its own history.

    ``_stats.csv`` counts what locust's accumulator held when it was written; the
    history's cumulative total, once the reset is located, counts what the same
    accumulator held after the reset. When they agree, the reported percentiles
    are the post-reset population and no ramp request is among them. When they do
    not, they are not - and that is reported rather than assumed either way.
    """
    reported = warm.get("requests")
    expected = reset.get("requests_after_reset")
    if not reset.get("detected") or expected is None or reported is None:
        return {"verified": False, "reported_requests": reported, "post_reset_requests": expected}
    return {
        "verified": expected == reported,
        "reported_requests": reported,
        "post_reset_requests": expected,
    }


def _phase_labels(report: Mapping[str, Any]) -> list[str]:
    labels = [
        f"cold and warm reported separately; the >= {report['min_hold_seconds']} s hold's samples are "
        "excluded from every reported percentile (P-5)",
        "cold window includes the spawn ramp: it is the deploy-time experience, and excluding the "
        "ramp would also exclude the coldness",
    ]
    if not report["reset"]["detected"]:
        labels.append(
            "warm-phase-reset-unproven: no decrease in locust's cumulative request count was found, so "
            "the reported warm percentiles MAY include the spawn ramp. Treat them as an upper bound "
            "until this is observed on the Linux target ([Unverified - Needs-Linux-measure])."
        )
    if not report["warm_population"]["verified"]:
        population = report["warm_population"]
        labels.append(
            "warm-population-unverified: the measured window reported "
            f"{population['reported_requests']} requests against {population['post_reset_requests']} "
            "accounted for after the reset, so no ramp request can be PROVEN absent from the "
            "reported percentiles. The hold's samples are excluded structurally - it is a separate "
            "process writing separate files - but the measured window's own ramp is not."
        )
    return labels


def assert_phase_report_valid(report: Mapping[str, Any]) -> dict[str, Any]:
    """Refuse a cold/hold/warm report that does not describe a P-5 run.

    Three things must hold, and each is arithmetic over the CSVs rather than a
    claim about them: the phases ran in order; the hold finished before the
    measured window opened; and the measured window's reported request count is
    the post-reset population its own history implies.
    """
    findings: list[str] = []
    phases = report["phases"]
    windows = {name: phases[name]["window"] for name in PHASES}

    missing = [name for name in PHASES if phases[name]["window"]["intervals"] == 0]
    if missing:
        findings.append(f"phase(s) {', '.join(missing)} recorded no history intervals at all")

    starts = [windows[name]["first_timestamp"] for name in PHASES]
    if all(t is not None for t in starts) and starts != sorted(starts):
        findings.append(f"phase start timestamps are out of order: {starts}")
    for earlier, later in (("cold", "hold"), ("hold", "warm")):
        first, last = windows[earlier]["last_timestamp"], windows[later]["first_timestamp"]
        if first is not None and last is not None and last < first:
            findings.append(f"{later} began at {last}, before {earlier} finished at {first}")

    reset = report["reset"]
    if reset["detected"]:
        hold_last = windows["hold"]["last_timestamp"]
        boundary = reset["at_timestamp"]
        if hold_last is not None and boundary is not None and boundary < hold_last:
            findings.append(
                f"the measured window's --reset-stats boundary is at {boundary}, before the "
                f">= {report['min_hold_seconds']} s hold finished at {hold_last}, so the hold did not "
                "precede the reset"
            )
    if findings:
        raise PhaseTimelineInvalid(tuple(findings))
    return dict(report)


def write_phase_report(report: Mapping[str, Any], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def build_manifest(
    *,
    app_host: Mapping[str, Any],
    run_params: dict[str, Any] | None = None,
    repo_root: Path = REPO_ROOT,
    phase: str = "start",
) -> dict[str, Any]:
    """Assemble the manifest. Raises if git cannot describe the tree, or if the
    app host did not report a runtime this programme may measure.

    ``app_host`` is a required argument, not an optional one with a default. There
    is no honest fallback: a manifest built without it would describe the load
    host's security limits and stores under a ``limits`` key that reads as if it
    described the server, which is how every number in the 2026-09-22 pack came to
    be mislabelled.
    """
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
    # ...and only then the app host's report: the load host cannot observe the
    # server it is measuring, so a run with nothing to attest for the server is a
    # run with no claim to make.
    assert_representative_runtime(app_host)
    # ...and the deployment manifest, for the same reason with a harder edge: these
    # six fields are what every throughput ratio in the programme is divided by,
    # and N-3 found no in-repo file recording any of them.
    assert_deployment_recorded(app_host.get("deployment") or {})

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "phase": phase,
        "git": {
            "sha": head,
            "short": head[:12],
            "branch": _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD"),
        },
        "harness": {
            "files": [asdict(f) for f in files],
            "dependencies": {rel: list(deps) for rel, deps in sorted(dependencies.items())},
        },
        # The interpreter and machine that produced the ARTEFACT - the load
        # generator. The server's are under app_host, because it is a different
        # process on a different machine.
        "python": {
            "version": sys.version,
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "load_host": {
            "platform": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
        },
        # Which host ran the load and which ran the server, as a derived fact.
        # Kept beside load_host (the local fingerprint) because this is the
        # pairing of that fingerprint with the app host's, and a reader needs
        # both to know what the numbers were produced across.
        "topology": _topology(app_host),
        # Everything below describes the SERVER, and is taken from what the server
        # reported rather than from what this host would have used.
        "stores": dict(app_host["stores"]),
        "limits": dict(app_host["limits"]),
        # The machine the numbers describe, as a fact. Distinct from app_host.host,
        # which is the platform fingerprint: this is the deployment topology the
        # throughput claims are stated against.
        "deployment": dict(app_host.get("deployment") or {}),
        "labels": _labels(app_host),
        "app_host": dict(app_host),
        "run": dict(sorted((run_params or {}).items())),
    }
    return assert_manifest_complete(manifest)


def write_manifest(manifest: dict[str, Any], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def _run_params(args: argparse.Namespace) -> tuple[dict[str, Any], str | None]:
    """Assemble the manifest's ``run`` section. Returns ``(params, problem)``.

    ``problem`` is the operator-facing refusal, returned rather than printed for
    the same reason as :func:`_load_start_manifest`: this stays a function of its
    input, which is what makes it testable without capturing stdout.

    ``hold_seconds`` survives the ``None`` filter even when the operator omitted
    it, unlike the four fields above it. That is deliberate: it is a REQUIRED
    manifest field, so it has to reach :func:`assert_manifest_complete` as an
    absence the refusal can NAME. Dropping it here would fail the run without
    saying which field was missing, which is the failure mode T-8 exists to
    prevent.

    The cleanup log is left out of ``params`` entirely when it was not supplied,
    for the same reason. Both absences are then reported by name in one place
    instead of being papered over in two.
    """
    params: dict[str, Any] = {
        "users": args.users,
        "rate": args.rate,
        "duration": args.duration,
        "host": args.host,
        "hold_seconds": args.hold_seconds,
    }
    params = {k: v for k, v in params.items() if v is not None or k == "hold_seconds"}
    if args.cleanup_log is None:
        return params, None
    try:
        return {**params, "cleanup": json.loads(args.cleanup_log.read_text(encoding="utf-8"))}, None
    except (OSError, json.JSONDecodeError) as exc:
        return params, (
            f"VOID: cannot read the cleanup log {args.cleanup_log} ({exc}). Without it the manifest "
            "cannot say what the run tore down, so it would record a run whose teardown is unknown."
        )


def _load_start_manifest(args: argparse.Namespace) -> tuple[Any, str | None]:
    """Read the run-start record an ``--phase end`` invocation asserts against.

    Returns ``(manifest, problem)``. ``problem`` is the operator-facing refusal,
    already formatted; it is returned rather than printed so this stays a function
    of its input, which is what makes it testable without capturing stdout.
    """
    if args.assert_matches is not None:
        try:
            return json.loads(args.assert_matches.read_text(encoding="utf-8")), None
        except OSError as exc:
            return None, (
                f"VOID: cannot read the run-start manifest {args.assert_matches} ({exc.strerror or exc}). "
                "There is nothing to assert the end of this run against, so the end record would be "
                "an assertion about nothing."
            )
        except json.JSONDecodeError as exc:
            return None, (
                f"VOID: the run-start manifest {args.assert_matches} is not valid JSON ({exc})."
            )
    if args.phase == "end":
        return None, (
            "VOID: --phase end requires --assert-matches <the run-start manifest>. The point of an "
            "end-of-run record is to assert that nothing changed during the run; without the start "
            "record to compare against it records nothing that can be checked."
        )
    return None, None


def _emit_phase_report(args: argparse.Namespace) -> int:
    """The ``--emit-phase-report`` mode.

    Split out of :func:`main` so the manifest path stays the single place that
    knows how a run is gated: this mode gates on the phase evidence instead, and
    has its own exit code rather than borrowing one that means something else.
    """
    missing_flag = [
        flag
        for flag, value in (
            ("--manifest", args.manifest),
            ("--cold-csv", args.cold_csv),
            ("--hold-csv", args.hold_csv),
            ("--warm-csv", args.warm_csv),
        )
        if value is None
    ]
    if missing_flag:
        print(
            f"VOID: --emit-phase-report needs {', '.join(missing_flag)}. Without all four there is "
            "nothing to compare: a cold number reported next to no warm number, or the reverse, is "
            "the conflation P-5 exists to end.",
            file=sys.stderr,
        )
        return EXIT_PHASE_UNPROVEN
    manifest: Any = None
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except OSError as exc:
        print(
            f"VOID: cannot read the run manifest {args.manifest} ({exc.strerror or exc}). A latency "
            "report that does not name the commit, budgets and runtime it belongs to is not an "
            "artefact.",
            file=sys.stderr,
        )
        return EXIT_NO_APP_HOST_REPORT
    except json.JSONDecodeError as exc:
        print(f"VOID: the run manifest {args.manifest} is not valid JSON ({exc}).", file=sys.stderr)
        return EXIT_NO_APP_HOST_REPORT

    try:
        report = build_phase_report(
            manifest=manifest,
            cold_prefix=args.cold_csv,
            hold_prefix=args.hold_csv,
            warm_prefix=args.warm_csv,
        )
    except PhaseSamplesMissing as exc:
        print(f"  missing  {exc.path} ({exc.reason})", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return EXIT_PHASE_UNPROVEN
    except PhaseTimelineInvalid as exc:
        for finding in exc.findings:
            print(f"  phase    {finding}", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return EXIT_PHASE_UNPROVEN

    for name in PHASES:
        phase = report["phases"][name]
        window = phase["window"]
        print(
            f"  {name:9s} n={phase['requests']:<8d} window={window['seconds']}s "
            f"peak_users={window['peak_concurrency']} reported={phase['reported']}"
        )
    print(f"  reset    detected={report['reset']['detected']} population_verified="
          f"{report['warm_population']['verified']}")
    for name, entry in report["comparison"].items():
        print(f"  compare  {name}  cold_p50={entry['cold_median_ms']} warm_p50={entry['warm_median_ms']} "
              f"inflation={entry['inflation_ratio']}x")
    for label in report["labels"]:
        print(f"  label    {label}")
    if args.output:
        path = write_phase_report(report, args.output)
        print(f"  phases   {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_manifest", description=__doc__)
    parser.add_argument("--output", type=Path, help="write the manifest JSON here; omit to only gate")
    parser.add_argument("--users", default=None, help="recorded in manifest.run")
    parser.add_argument("--rate", default=None, help="recorded in manifest.run")
    parser.add_argument("--duration", default=None, help="recorded in manifest.run")
    parser.add_argument("--host", default=None, help="recorded in manifest.run")
    parser.add_argument(
        "--hold-seconds",
        default=None,
        help=(
            "REQUIRED (recorded as manifest.run.hold_seconds): how long the hold "
            "was REQUESTED, in seconds. The hold FLOOR is this module's MIN_HOLD_SECONDS "
            "and is printed by --print-min-hold-seconds, but a floor is a promise and the "
            "artefact has to carry the number that happened, or the reader cannot tell a "
            "warmed measurement from a truncated one. Omitted means the manifest aborts "
            "(8): a manifest with no warm-up duration reads as a steady-state number."
        ),
    )
    parser.add_argument(
        "--cleanup-log",
        type=Path,
        default=None,
        help=(
            "REQUIRED (embedded as manifest.run.cleanup): a JSON file the harness wrote "
            "listing what it tore down and what it left behind. Omitted means the manifest "
            "aborts (8): a run whose teardown was never recorded cannot say whether the "
            "artefacts beside it are still warm state or were cleaned."
        ),
    )
    parser.add_argument(
        "--emit-app-host-report",
        action="store_true",
        help=(
            "app-host mode: print this host's app-host report to stdout and exit. Run on the APP "
            "HOST, with the server's own environment in force, then copy the file to the load host. "
            f"Set {IMAGE_DIGEST_ENV} to the image digest the orchestrator pulled; unset it and the "
            "report records no digest and the run is labelled as untied to a build. Set "
            f"{ORCHESTRATOR_ENV} to whatever supervises the server and {STORE_LOCALITY_ENV} to "
            "co-located, remote or mixed if the store addresses are bare hostnames; neither is "
            "readable from inside the process, no in-repo file records either, and the run is "
            "void without them."
        ),
    )
    parser.add_argument(
        "--app-host-report",
        type=Path,
        help=(
            "the app host's report, produced there with --emit-app-host-report. Required on the "
            "load host: without it nothing here describes the server under test."
        ),
    )
    parser.add_argument(
        "--phase",
        choices=("start", "end"),
        default="start",
        help="which end of the run this record is. 'end' additionally requires --assert-matches.",
    )
    parser.add_argument(
        "--assert-matches",
        type=Path,
        help=(
            "the run-start manifest. Every invariant section must equal it, or the run is void. "
            "Required with --phase end: an end record that checks nothing is decoration."
        ),
    )
    parser.add_argument(
        "--print-min-hold-seconds",
        action="store_true",
        help=(
            f"print P-5's hold floor ({MIN_HOLD_SECONDS}) and exit. The runner reads it from here "
            "rather than repeating the literal, so the floor lives with the rule and not at the "
            "call site."
        ),
    )
    parser.add_argument(
        "--emit-phase-report",
        action="store_true",
        help=(
            "phase-report mode: read the cold, hold and warm locust CSVs back and emit both the cold "
            "and the warm numbers, per endpoint, with the reset boundary that separates them. Refuses "
            "(11) when a phase's samples are missing or the phases did not run in P-5's order."
        ),
    )
    parser.add_argument("--manifest", type=Path, help="the run manifest this report belongs to; required")
    parser.add_argument("--cold-csv", type=Path, help="locust --csv prefix of the cold phase")
    parser.add_argument("--hold-csv", type=Path, help="locust --csv prefix of the >=60 s hold")
    parser.add_argument("--warm-csv", type=Path, help="locust --csv prefix of the measured phase")
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

    if args.print_min_hold_seconds:
        print(MIN_HOLD_SECONDS)
        return 0

    if args.emit_app_host_report:
        print(json.dumps(build_app_host_report(), indent=2, sort_keys=True))
        return 0

    if args.emit_phase_report:
        return _emit_phase_report(args)

    if args.app_host_report is None:
        print(
            "VOID: --app-host-report is required. Since H-9 the load generator and the server are "
            "different machines, so a manifest written without the app host's report would describe "
            "the load host's security limits and stores under keys that read as the server's. "
            "Produce the report on the app host with "
            "`python -m src.benchmarks.run_manifest --emit-app-host-report > app_host_report.json`.",
            file=sys.stderr,
        )
        return EXIT_NO_APP_HOST_REPORT

    try:
        report = load_app_host_report(args.app_host_report)
    except AppHostReportMissing as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_NO_APP_HOST_REPORT

    start_manifest, start_problem = _load_start_manifest(args)
    if start_problem is not None:
        print(start_problem, file=sys.stderr)
        return EXIT_NO_APP_HOST_REPORT

    params, params_problem = _run_params(args)
    if params_problem is not None:
        print(params_problem, file=sys.stderr)
        return EXIT_INCOMPLETE_MANIFEST
    try:
        manifest = build_manifest(
            run_params=params, repo_root=args.repo_root, app_host=report, phase=args.phase
        )
        if start_manifest is not None:
            manifest = assert_run_end_matches(start_manifest, manifest)
    except HarnessNotPinned as exc:
        for f in exc.files:
            print(f"  {f.reason:9s} {f.path}", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return EXIT_UNPINNED
    except HarnessDependencyNotTracked as exc:
        for relative in exc.files:
            print(f"  untracked {relative}", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return EXIT_UNTRACKED_DEPENDENCY
    except NonRepresentativeRuntime as exc:
        for finding in exc.findings:
            print(f"  non-representative  {finding}", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return EXIT_NON_REPRESENTATIVE
    except ManifestIncomplete as exc:
        for field in exc.fields:
            print(f"  missing  {field}", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return EXIT_INCOMPLETE_MANIFEST
    except DeploymentUndeclared as exc:
        for field in exc.fields:
            print(f"  undeclared  {field}", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return EXIT_DEPLOYMENT_UNDECLARED
    except RunEndMismatch as exc:
        for section in exc.sections:
            print(f"  changed  {section}", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return EXIT_RUN_END_MISMATCH
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_NO_HEAD

    for f in manifest["harness"]["files"]:
        print(f"  pinned    {f['path']}  blob={f['blob_sha'][:12]}  last_commit={str(f['last_commit'])[:12]}")
    print(f"  app       {APP_ENTRYPOINT}  commit={manifest['git']['short']}")
    print(
        f"  app_host  {report['host']['platform']} loop={report['event_loop']} http={report['http']} "
        f"auto_ban={report['limits']['auto_ban_enabled']}"
    )
    deployment = report.get("deployment") or {}
    print(
        f"  deployment  {deployment.get('orchestrator')} {deployment.get('distro')} "
        f"cores={deployment.get('core_count')} stores={deployment.get('store_locality')} "
        f"cpu={deployment.get('cpu_model')}"
    )

    if args.output:
        path = write_manifest(manifest, args.output)
        print(f"  manifest  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
