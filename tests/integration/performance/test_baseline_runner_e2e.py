"""End-to-end verification of the baseline runner: the REFUSALS, not the happy path.

`tests/unit/benchmarks/test_run_manifest.py` pins the runner's wiring
structurally, because bash cannot be relied on there. This file runs the real
`scripts/run_perf_baseline.sh` under a real bash and watches what it actually
does: which hosts it refuses, which venvs it refuses, which exit codes it hands
back, and what lands in `reports/` when it does measure.

WHAT IS REAL AND WHAT IS A STAND-IN, stated once and plainly:

* The runner is real. The bash is real. The refusals are real.
* The provenance gate is real. ``.venv/bin/python`` is a shim that forwards to a
  real Python with this scratch checkout importable, so ``run_manifest.py`` and
  ``percentiles.py`` are the shipped modules executing against the checkout the
  runner just moved into. Its exit codes are its own.
* The app host is a stand-in: a stub ``curl`` that answers 200, and a stub
  ``locust`` that writes CSVs of the right shape. Neither computes a latency.
* Real locust against a real application on a second machine is
  ``test_the_whole_stack_on_the_linux_target`` at the end of this file, which
  only runs on the Linux target and is therefore
  ``[Unverified - Needs-Linux-measure]`` until it is run there.

PER-STEP LOGGING (acceptance criterion (g)). The runner is driven as a
subprocess, so "request id / status code / resolved latency / assertion under
check" is recorded for every single invocation by ``Runner.run``, which takes the
assertion being checked as a REQUIRED argument: a step cannot be taken in this
suite without naming what it is checking, and its id, the runner's exit status
and its wall-clock duration are recorded with it. ``test_every_step_records_its
_own_id_status_latency_and_assertion`` holds the log to that shape.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from src.benchmarks.run_manifest import (
    EXIT_NO_APP_HOST_REPORT,
    EXIT_RUN_END_MISMATCH,
    EXPECTED_EVENT_LOOP,
    EXPECTED_HTTP_IMPL,
    MIN_HOLD_SECONDS,
    REQUIRED_MANIFEST_FIELDS,
    build_app_host_report,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER = REPO_ROOT / "scripts" / "run_perf_baseline.sh"

#: What the runner exits 64 with: EX_USAGE, its own "I will not measure this".
EXIT_REFUSED = 64

#: Every file the scratch checkout needs for the provenance gate to pass, plus
#: the package markers that make ``src.benchmarks`` importable inside it. Spelled
#: out rather than imported from the unit suite: an integration test that imports
#: the unit test it exists to re-verify stops being a second opinion.
CHECKOUT_FILES = (
    "scripts/run_perf_baseline.sh",
    "scripts/setup_locust_test_data.py",
    "src/__init__.py",
    "src/benchmarks/__init__.py",
    "src/benchmarks/percentiles.py",
    "src/benchmarks/run_manifest.py",
    "tests/performance/locustfile.py",
)

#: What this repository actually runs is NOT the answer to L-1/L-2; these are the
#: values a Linux app host would declare. Nothing here is measured, and Docker is
#: prohibited, so no digest is fabricated from the local commit.
DECLARED_IMAGE_DIGEST = "sha256:" + "0" * 64
DECLARED_DEPLOYMENT: dict[str, Any] = {
    "orchestrator": "docker-compose",
    "distro": "Debian GNU/Linux 12 (bookworm)",
    "cpu_model": "AMD EPYC 7763 64-Core Processor",
    "core_count": 8,
    "store_locality": "remote",
    "image_digest": DECLARED_IMAGE_DIGEST,
    "store_addresses": {
        "postgres": "postgres.internal:5432/app",
        "redis_cache": "redis-cache.internal:6379",
        "redis_queue": "redis-queue.internal:6379",
    },
}

GITIGNORE = ".venv/\nreports/\nstub/\nlocust_baseline_*.log\n"

#: The load host's virtualenv interpreter. Forwarded verbatim to a real Python;
#: only locust is intercepted, and only because the app host is a stand-in.
SHIM_PYTHON = """#!/bin/sh
# The load host's `.venv/bin/python`, as a stand-in. Everything except locust is
# forwarded verbatim to a real Python, so the provenance gate and the phase report
# below are the shipped modules running against THIS checkout.
if [ "$1" = "-m" ] && [ "$2" = "locust" ]; then
    shift 2
    exec "@STUB_PY@" "@STUB_LOCUST@" "$@"
fi
exec "@REAL_PY@" "$@"
"""

#: The app host, as far as the runner can observe it.
SHIM_CURL = """#!/bin/sh
# The app host. Records the probe it was asked to make, then answers 200.
printf 'probe %s\\n' "$*" >> "$PERF_STUB_LOG"
printf '200'
exit 0
"""

#: locust, as far as the runner's contract with it goes: the flags it was given
#: and the CSVs it wrote. Never a latency - see the module docstring.
STUB_LOCUST = '''\
"""A stand-in for locust, and the whole of what is faked here.

The runner's contract with locust is its flags and its CSV prefix. A stand-in
that honours both can drive the runner's own gates - the provenance gate at each
end, and the cold/hold/warm phase report - to completion without a second
machine. Timestamps come from a clock file the three invocations share, so the
windows land in the order the runner ran them, and a ``--reset-stats`` run shows
the same decrease in locust's cumulative request count a real reset produces.
"""

import json
import os
import sys

STATS = [
    "Type", "Name", "Request Count", "Failure Count", "Average Response Time",
    "Min Response Time", "Max Response Time", "50%", "95%", "99%",
]
HISTORY = ["Timestamp", "User Count", "Total Request Count", "Requests/s"]
ENDPOINT = "GET /api/v1/health"
INTERVAL = 2.0
PER_INTERVAL = 40
BASE_CLOCK = 1700000000.0
# Median ms per invocation: cold, hold, warm. The hold's figure is absurd on
# purpose - it is the sample that must never reach a reported percentile.
MEDIANS = [300.0, 9999.0, 30.0]


def flag(argv, name, default=None):
    prefix = "--" + name + "="
    for arg in argv:
        if arg.startswith(prefix):
            return arg[len(prefix):]
    return default


def value_after(argv, name, default):
    for index, arg in enumerate(argv):
        if arg == name and index + 1 < len(argv):
            return argv[index + 1]
    return default


def seconds(value):
    if value.endswith("m"):
        return float(value[:-1]) * 60.0
    if value.endswith("h"):
        return float(value[:-1]) * 3600.0
    if value.endswith("s"):
        return float(value[:-1])
    return float(value)


def main(argv):
    prefix = flag(argv, "csv")
    if prefix is None:
        return 0
    path = os.environ["PERF_STUB_CLOCK"]
    saved = {"clock": BASE_CLOCK, "runs": 0}
    if os.path.exists(path):
        with open(path) as handle:
            saved = json.load(handle)
    index = saved["runs"]
    run_time = flag(argv, "run-time", "30s")
    users = value_after(argv, "-u", "100")
    reset = "--reset-stats" in argv
    log = os.environ["PERF_STUB_LOG"]
    with open(log, "a") as handle:
        handle.write("load csv=" + prefix + " run-time=" + run_time + " reset=" + str(reset) + "\\n")
    if index >= len(MEDIANS):
        return 0

    start = saved["clock"]
    rows = max(2, int(seconds(run_time) // INTERVAL))
    totals = []
    total = 0
    boundary = rows // 5
    for row in range(rows):
        if reset and row == boundary:
            total = 0
        total += PER_INTERVAL
        totals.append(total)

    median = MEDIANS[index]
    reported = totals[-1]
    with open(prefix + "_stats.csv", "w") as handle:
        handle.write(",".join(STATS) + "\\n")
        handle.write(
            ",".join([
                "", ENDPOINT, str(reported), "0",
                str(median), str(median * 0.9), str(median * 1.2),
                str(median), str(median * 1.1), str(median * 1.2),
            ]) + "\\n"
        )
        handle.write(",".join(["Aggregated", "", str(reported), "0"] + ["0"] * 7) + "\\n")

    with open(prefix + "_stats_history.csv", "w") as handle:
        handle.write(",".join(HISTORY) + "\\n")
        for row, count in enumerate(totals):
            stamp = start + row * INTERVAL
            handle.write("%.1f,%s,%d,%.1f\\n" % (stamp, users, count, PER_INTERVAL / INTERVAL))

    saved = {"clock": start + rows * INTERVAL + 1.0, "runs": index + 1}
    with open(path, "w") as handle:
        json.dump(saved, handle)

    tamper = os.environ.get("PERF_STUB_TAMPER")
    if tamper == "budgets" and index == 0:
        import glob
        starts = [p for p in glob.glob("reports/*_manifest.json") if not p.endswith("_end.json")]
        with open(starts[0]) as handle:
            manifest = json.load(handle)
        manifest["limits"]["security_budgets"]["auth"] = [999, 60]
        with open(starts[0], "w") as handle:
            json.dump(manifest, handle)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''


@dataclass(frozen=True)
class Step:
    """One invocation of the runner, and the assertion it was taken for."""

    request_id: str
    status_code: int
    latency_ms: float
    assertion: str


@dataclass
class Runner:
    """A throwaway checkout the real runner can be pointed at."""

    root: Path
    bash: str
    stub_dir: Path
    log_path: Path
    steps: list[Step] = field(default_factory=list)
    _counter: int = 0

    # -- construction -------------------------------------------------------

    @classmethod
    def build(cls, root: Path, bash: str, *, real_python: str | None = None) -> Runner:
        root.mkdir(parents=True, exist_ok=True)
        for relative in CHECKOUT_FILES:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO_ROOT / relative, target)
        (root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
        (root / "reports").mkdir(exist_ok=True)

        stub_dir = root / "stub"
        stub_dir.mkdir(exist_ok=True)
        stub_locust = stub_dir / "locust_stub.py"
        stub_locust.write_text(STUB_LOCUST, encoding="utf-8")
        shim = root / ".venv" / "bin" / "python"
        shim.parent.mkdir(parents=True, exist_ok=True)
        shim.write_text(
            SHIM_PYTHON.replace("@REAL_PY@", _shell_path(real_python or sys.executable))
            .replace("@STUB_PY@", _shell_path(sys.executable))
            .replace("@STUB_LOCUST@", _shell_path(stub_locust)),
            encoding="utf-8",
        )
        shim.chmod(0o755)
        curl = stub_dir / "curl"
        curl.write_text(SHIM_CURL, encoding="utf-8")
        curl.chmod(0o755)

        for argv in (
            ["init", "-q"],
            ["config", "user.email", "perf-harness@example.invalid"],
            ["config", "user.name", "perf harness"],
            ["add", "-A"],
            ["commit", "-qm", "scratch checkout"],
        ):
            done = subprocess.run(["git", *argv], cwd=root, capture_output=True, text=True, check=False)
            assert done.returncode == 0, f"git {argv[0]} failed: {done.stderr}"
        return cls(root=root, bash=bash, stub_dir=stub_dir, log_path=stub_dir / "calls.log")

    # -- driving ------------------------------------------------------------

    def write_app_host_report(self, report: dict[str, Any]) -> Path:
        """Put the app host's report where the runner looks for it by default."""
        path = self.root / "reports" / "app_host_report.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return path

    def run(self, asserting: str, *args: str, tamper: str | None = None) -> subprocess.CompletedProcess[str]:
        """Run the real runner, recording the step. ``asserting`` has no default."""
        self._counter += 1
        environment = dict(os.environ)
        # Forward slashes: MSYS bash opens these and backslashes are escapes to
        # it, so a Windows path handed over verbatim would be a different name.
        environment["PERF_STUB_DIR"] = _msys_path(self.stub_dir)
        environment["PERF_STUB_LOG"] = _shell_path(self.log_path)
        environment["PERF_STUB_CLOCK"] = _shell_path(self.stub_dir / "clock.json")
        environment["NO_PROXY"] = "*"
        environment["no_proxy"] = "*"
        if tamper is not None:
            environment["PERF_STUB_TAMPER"] = tamper
        # PATH is prepended INSIDE bash, not in the environment: a Git-for-Windows
        # shell puts its own /mingw64/bin ahead of anything it inherited, and its
        # bundled curl would answer for the app host.
        command = 'PATH="$PERF_STUB_DIR:$PATH"; export PATH; exec "$@"'
        started = time.perf_counter()
        done = subprocess.run(
            [self.bash, "-c", command, "runner", str(self.root / "scripts" / "run_perf_baseline.sh"), *args],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        self.steps.append(
            Step(
                request_id=f"step-{self._counter:03d}",
                status_code=done.returncode,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                assertion=asserting,
            )
        )
        return done

    def measure(self, *extra: str, tamper: str | None = None) -> subprocess.CompletedProcess[str]:
        return self.run(
            "a full three-phase run against a declared app host completes and writes every artefact",
            "--host",
            "http://app-host.invalid:8000",
            "-u",
            "20",
            "-r",
            "5",
            "-t",
            "1m",
            "--cold-window",
            "20s",
            *extra,
            tamper=tamper,
        )

    # -- artefacts ----------------------------------------------------------

    def _read(self, pattern: str) -> dict[str, Any]:
        matches = sorted(self.root.glob(pattern))
        assert len(matches) == 1, f"expected exactly one {pattern}, found {matches}"
        return json.loads(matches[0].read_text(encoding="utf-8"))

    def start_manifest(self) -> dict[str, Any]:
        return self._read("reports/*_manifest.json")

    def end_manifest(self) -> dict[str, Any]:
        return self._read("reports/*_manifest_end.json")

    def phase_report(self) -> dict[str, Any]:
        return self._read("reports/*_phases.json")

    def run_log(self) -> str:
        logs = sorted(self.root.glob("locust_baseline_*.log"))
        return logs[-1].read_text(encoding="utf-8") if logs else ""

    def calls(self, prefix: str) -> list[str]:
        if not self.log_path.exists():
            return []
        return [line for line in self.log_path.read_text(encoding="utf-8").splitlines() if line.startswith(prefix)]


def _shell_path(path: Path | str) -> str:
    """A path the shell under test can open. Forward slashes on Windows."""
    text = str(path)
    return text.replace("\\", "/") if platform.system() == "Windows" else text


def _msys_path(path: Path | str) -> str:
    """A PATH entry a Git-for-Windows bash can split.

    ``PATH`` is colon-separated, and ``C:/Users/...`` contains a colon: handed
    over verbatim the entry is torn in half at the drive letter and the stub
    directory silently never reaches the front of PATH. MSYS addresses the same
    directory ``/c/Users/...``, and does split that correctly.
    """
    text = _shell_path(path)
    if len(text) > 2 and text[1] == ":" and text[2] == "/":
        return "/" + text[0].lower() + text[2:]
    return text


def _usable_bash() -> str | None:
    """A bash that can actually run a command, or None.

    ``shutil.which("bash")`` is not enough on Windows: it resolves to the WSL
    *launcher*, which exists, exits non-zero on ``-c :`` and would turn every
    assertion below into a pass for the wrong reason. Each candidate is probed.
    """
    candidates: list[str | Path | None] = [shutil.which("bash")]
    if platform.system() == "Windows":
        candidates += [
            Path(r"C:\Program Files\Git\bin\bash.exe"),
            Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
        ]
    for candidate in candidates:
        if candidate is None or not Path(candidate).exists():
            continue
        probe = subprocess.run([str(candidate), "-c", ":"], capture_output=True, check=False)
        if probe.returncode == 0:
            return str(candidate)
    return None


def declared_app_host_report(*, auto_ban: bool = True) -> dict[str, Any]:
    """The report a Linux app host would emit, built by the shipped code.

    ``build_app_host_report`` resolves the runtime and the deployment fields from
    the machine it runs on, and this machine is Windows without uvloop, so those
    values are declared. The report's SHAPE - and therefore every refusal the
    load host makes against it - is production code either way.
    """
    report = build_app_host_report(
        event_loop=EXPECTED_EVENT_LOOP,
        http_impl=EXPECTED_HTTP_IMPL,
        image_digest=DECLARED_IMAGE_DIGEST,
        deployment=dict(DECLARED_DEPLOYMENT),
    )
    report["limits"]["auto_ban_enabled"] = auto_ban
    return report


def dig(document: Any, dotted: str) -> Any:
    node = document
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


@pytest.fixture(scope="module")
def bash() -> str:
    found = _usable_bash()
    if found is None:
        pytest.skip("no bash on this host that can run a command; the runner's refusals cannot be executed")
    return found


@pytest.fixture
def runner(tmp_path: Path, bash: str) -> Runner:
    """A scratch checkout whose app-host report is a representative one."""
    built = Runner.build(tmp_path / "load-host", bash)
    built.write_app_host_report(declared_app_host_report())
    return built


class TestTheRunnerRefusesToMeasureItsOwnHost:
    """Acceptance criterion (b): same-host co-location is refused, not measured."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8000",
            "http://127.0.0.53:8000",
            "http://localhost:8000",
            "http://app.localhost:8000",
            "http://0.0.0.0:8000",
            "http://[::1]:8000",
            "http://[::]:8000",
        ],
    )
    def test_it_refuses_a_loopback_target(self, runner: Runner, url: str) -> None:
        done = runner.run("a loopback target is refused before any load", "--host", url)
        assert done.returncode == EXIT_REFUSED
        assert "separate hosts" in done.stderr
        assert runner.calls("load ") == [], "the runner generated load against its own host"

    def test_it_refuses_its_own_hostname(self, runner: Runner) -> None:
        name = socket.gethostname()
        if not name:
            pytest.skip("this host reports no name to compare against")
        done = runner.run("this host's own name is refused", "--host", f"http://{name}:8000")
        assert done.returncode == EXIT_REFUSED
        assert name in done.stderr
        assert runner.calls("load ") == []

    def test_it_requires_a_host_and_offers_no_default(self, runner: Runner) -> None:
        done = runner.run("there is no default target, because there is no server here", "-u", "20")
        assert done.returncode == EXIT_REFUSED
        assert "--host is required" in done.stderr

    def test_it_probes_the_apps_health_endpoint_before_measuring(self, runner: Runner) -> None:
        done = runner.measure()
        assert done.returncode == 0, done.stderr
        probes = runner.calls("probe ")
        assert probes, "the runner measured a server it had never spoken to"
        assert all("/api/v1/health" in probe for probe in probes)


class TestTheWindowsInterpreterBranchIsGuarded:
    """Acceptance criterion (c): a Windows run must not pass as a Linux one."""

    def test_the_posix_layout_is_probed_first(self) -> None:
        body = RUNNER.read_text(encoding="utf-8")
        assert body.index(".venv/bin/python") < body.index(".venv/Scripts/python.exe")

    def test_it_refuses_a_windows_only_virtualenv(self, tmp_path: Path, bash: str) -> None:
        built = Runner.build(tmp_path / "windows-host", bash)
        built.root.joinpath(".venv/bin/python").unlink()
        built.root.joinpath(".venv/Scripts").mkdir(parents=True)
        built.root.joinpath(".venv/Scripts/python.exe").write_text("not really a python", encoding="utf-8")
        built.write_app_host_report(declared_app_host_report())

        done = built.run("a Windows-only venv is refused", "--host", "http://app-host.invalid:8000")
        assert done.returncode == EXIT_REFUSED
        assert "Windows .venv" in done.stderr
        assert "A-5" in done.stderr
        assert built.calls("load ") == []


class TestTheHoldFloorIsTheHarnesssOwn:
    def test_a_hold_below_the_floor_is_refused_rather_than_clamped(self, runner: Runner) -> None:
        done = runner.run(
            "a hold shorter than P-5's floor is refused, not clamped",
            "--host",
            "http://app-host.invalid:8000",
            "--hold",
            f"{MIN_HOLD_SECONDS - 1}s",
        )
        assert done.returncode == EXIT_REFUSED
        assert f"{MIN_HOLD_SECONDS}s floor" in done.stderr
        assert "refused rather than clamped" in done.stderr

    def test_a_duration_it_cannot_parse_is_refused(self, runner: Runner) -> None:
        done = runner.run(
            "an unparseable hold is refused rather than guessed at",
            "--host",
            "http://app-host.invalid:8000",
            "--hold",
            "ages",
        )
        assert done.returncode == EXIT_REFUSED
        assert "not a duration this runner understands" in done.stderr


class TestAnUnusableAppHostReportAbortsBeforeAnyLoad:
    """Acceptance criterion (f): a hole in the report ABORTS; it does not default."""

    @pytest.mark.parametrize(
        ("hole", "expected"),
        [
            pytest.param("deployment", EXIT_NO_APP_HOST_REPORT, id="no-deployment-manifest"),
            pytest.param("image_digest", EXIT_NO_APP_HOST_REPORT, id="a-digest-that-is-not-one"),
            pytest.param("limits", EXIT_NO_APP_HOST_REPORT, id="no-effective-limits"),
        ],
    )
    def test_a_hole_in_the_report_aborts_the_run(self, runner: Runner, hole: str, expected: int) -> None:
        report = declared_app_host_report()
        if hole == "image_digest":
            report["image_digest"] = "sha256:not-a-digest"
        else:
            report.pop(hole)
        runner.write_app_host_report(report)

        done = runner.measure()
        assert done.returncode == expected, done.stderr
        assert runner.calls("load ") == [], "load was generated without a usable app-host report"
        assert list(runner.root.glob("reports/*_manifest*.json")) == []

    def test_a_missing_report_file_aborts_the_run(self, runner: Runner) -> None:
        runner.root.joinpath("reports/app_host_report.json").unlink()
        done = runner.measure()
        assert done.returncode == EXIT_NO_APP_HOST_REPORT
        # The banner names the log rather than repeating the gate's reason, and the
        # reason is only useful if an operator can find it, so both halves are asserted.
        assert "app-host report" in runner.run_log()
        assert "locust_baseline_" in done.stderr
        assert runner.calls("load ") == []


class TestTheRunEndMustMatchTheRunStart:
    """Acceptance criterion (e): the budgets are asserted at both ends."""

    def test_a_change_made_mid_run_voids_it(self, runner: Runner) -> None:
        done = runner.measure(tamper="budgets")
        assert done.returncode == EXIT_RUN_END_MISMATCH, done.stderr
        assert "run-end provenance gate" in done.stderr
        assert runner.start_manifest()["phase"] == "start"

    def test_an_unchanged_run_records_both_ends(self, runner: Runner) -> None:
        done = runner.measure()
        assert done.returncode == 0, done.stderr
        start, end = runner.start_manifest(), runner.end_manifest()
        assert start["phase"] == "start"
        assert end["phase"] == "end"
        assert end["limits"] == start["limits"]
        assert end["app_host"] == start["app_host"]
        assert end["git"] == start["git"]


class TestTheManifestTheRunnerWroteIsCompleteAndLabelled:
    """Acceptance criteria (d) and (f), through the runner's own argv."""

    @pytest.fixture
    def measured(self, runner: Runner) -> Runner:
        done = runner.measure()
        assert done.returncode == 0, done.stderr
        return runner

    def test_every_required_field_is_in_the_manifest_on_disk(self, measured: Runner) -> None:
        manifest = measured.start_manifest()
        missing = [field for field in REQUIRED_MANIFEST_FIELDS if dig(manifest, field) in (None, {}, [], "")]
        assert missing == [], f"the manifest the runner wrote is missing {missing}"

    def test_the_runtime_identity_asserted_is_the_containers(self, measured: Runner) -> None:
        app_host = measured.start_manifest()["app_host"]
        assert app_host["event_loop"] == EXPECTED_EVENT_LOOP
        assert app_host["http"] == EXPECTED_HTTP_IMPL
        assert app_host["uvicorn"]["loop_arg"] == "auto"
        assert app_host["uvicorn"]["http_arg"] == "auto"

    def test_the_limits_recorded_are_the_app_hosts(self, measured: Runner) -> None:
        manifest = measured.start_manifest()
        declared = json.loads(measured.root.joinpath("reports/app_host_report.json").read_text(encoding="utf-8"))
        assert manifest["limits"] == declared["limits"]
        assert manifest["stores"] == declared["stores"]

    def test_an_auto_ban_disabled_server_is_labelled_not_silently_accepted(self, tmp_path: Path, bash: str) -> None:
        built = Runner.build(tmp_path / "auto-ban-off", bash)
        built.write_app_host_report(declared_app_host_report(auto_ban=False))

        done = built.measure()
        assert done.returncode == 0, done.stderr
        manifest = built.start_manifest()
        assert manifest["limits"]["auto_ban_enabled"] is False
        assert "AUTO_BAN_ENABLED=false" in manifest["labels"]

    def test_the_label_is_derived_so_it_cannot_be_left_off(self, measured: Runner) -> None:
        labels = measured.start_manifest()["labels"]
        assert "AUTO_BAN_ENABLED=false" not in labels
        assert measured.start_manifest()["limits"]["auto_ban_enabled"] is True
        assert any("separate hosts" in label for label in labels)

    def test_the_runner_overrides_no_security_limit(self, measured: Runner) -> None:
        code = "\n".join(
            line for line in RUNNER.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
        )
        for override in (
            "AUTO_BAN_ENABLED",
            "SECURITY_BUDGETS",
            "DEFAULT_ANONYMOUS_LIMIT",
            "DEFAULT_AUTHENTICATED_LIMIT",
        ):
            assert f"export {override}" not in code

    def test_the_three_phases_are_recorded_apart(self, measured: Runner) -> None:
        report = measured.phase_report()
        assert set(report["phases"]) == {"cold", "hold", "warm"}
        assert report["phases"]["hold"]["reported"] is False
        assert report["comparison"], "the cold and the warm figure were not reported side by side"
        assert report["reset"]["detected"] is True
        assert report["warm_population"]["verified"] is True

    def test_the_holds_samples_never_reach_a_reported_percentile(self, measured: Runner) -> None:
        report = measured.phase_report()
        hold_median = report["phases"]["hold"]["endpoints"]["GET /api/v1/health"]["quantiles_ms"]["0.5"]
        warm = report["phases"]["warm"]["endpoints"]["GET /api/v1/health"]["quantiles_ms"]
        assert hold_median == 9999.0
        assert hold_median not in warm.values()


class TestEveryStepRecordsItsOwnIdStatusLatencyAndAssertion:
    """Acceptance criterion (g): per-step logging, recorded by construction."""

    def test_every_step_records_its_own_id_status_latency_and_assertion(self, runner: Runner) -> None:
        refused = runner.run("a loopback target is refused", "--host", "http://127.0.0.1:8000")
        assert refused.returncode == EXIT_REFUSED
        assert len(runner.steps) == 1
        step = runner.steps[0]
        assert step.request_id == "step-001"
        assert step.status_code == EXIT_REFUSED
        assert step.latency_ms > 0
        assert step.assertion == "a loopback target is refused"


@pytest.mark.e2e
@pytest.mark.skipif(platform.system() != "Linux", reason="[Unverified - Needs-Linux-measure] the Linux target")
def test_the_whole_stack_on_the_linux_target() -> None:
    """A real locust, a real application, a real second host.

    Not runnable here and not simulated: it needs the app host started the way
    the Dockerfile starts it, and a load host that is not that host. It stays
    ``[Unverified - Needs-Linux-measure]`` until it is run on the Linux target,
    and the answer to that is not a number invented in a test.
    """
    pytest.skip("[Unverified - Needs-Linux-measure] needs APP_HOST_URL for a server on another host")
