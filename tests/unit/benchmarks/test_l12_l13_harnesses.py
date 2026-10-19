"""L-12 / L-13: the recovered harnesses, and the claim that the recorded reports
are the harness's own numbers.

Two beads are involved: ``FastAPI_saas-liy7`` (L-12, pool throughput ratios)
and ``FastAPI_saas-e0ss`` (L-13, endpoint p95 effect of the body-scan offload).
Both were measured on 2026-10-16 on the Linux target and both recorded an
artefact that says, of its own instrument::

    "instrument": {"file": "/home/ansible/l12_pool_ratios.py", "in_repo": false}

That ``in_repo: false`` was TRUE of the run, and it was also the reason the run
could not be cited: the Phase 0 provenance gate refuses a measurement whose
instrument cannot be recovered from git, and the instrument existed only on one
machine's disk. The two drivers were recovered from that machine and re-homed
as ``src/benchmarks/l12_pool_ratios.py`` and
``src/benchmarks/l13_body_offload.py``.

The measurement is UNCHANGED - the same code, re-homed - and the recorded
artefacts keep their historical ``in_repo: false``, because that is what was
true of the run. What this file pins is the chain that made the numbers
checkable in the first place:

    the raw driver output  ->  the curated report  ->  the verdict

Each link is asserted, none of it is taken on trust:

- the RAW outputs (``perf-results/RESULT-20261016-l12/raw/l12_result.json`` and
  ``.../l13/raw/l13_result.json``) are the shape the in-repo harness still
  emits, and the L-12 harness's protocol block is byte-identical to the
  recorded one;
- the CURATED reports' every published figure is re-derived from the raw
  output - arm medians, ratios, noise floors, knee sweep, blocks at the knee;
- the VERDICT is re-derived from the published figures, rigorously: an arm's
  advantage is a result only when the two arms' measured ranges do not
  overlap. This is the check that the report's "0.86x is INSIDE its own 8%
  spread" is not a matter of opinion but a property of the numbers, and it
  reproduces the report's three-way claim exactly - the process arm is outside
  the noise in BOTH regimes, the thread arm is outside it in the miss regime
  and INSIDE it in the hit regime.

The harness-side tests are the house rules: no module-level environment
mutation, no module-level ``src.app`` import, ``bootstrap()`` first in the
entry point, and the app never imports either harness.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

L12_DIR = REPO_ROOT / "perf-results" / "RESULT-20261016-l12"
L12_REPORT = L12_DIR / "l12_pool_ratios.json"
L12_RAW = L12_DIR / "raw" / "l12_result.json"

L13_DIR = REPO_ROOT / "perf-results" / "RESULT-20261016-l13"
L13_REPORT = L13_DIR / "l13_body_offload.json"
L13_RAW = L13_DIR / "raw" / "l13_result.json"

L12_HARNESS = REPO_ROOT / "src" / "benchmarks" / "l12_pool_ratios.py"
L13_HARNESS = REPO_ROOT / "src" / "benchmarks" / "l13_body_offload.py"

#: The two regimes, as the raw artefact names them.
L12_REGIMES = ("miss_60kb", "hit_40kb")
#: The nine arms, as the raw artefact names them.
L12_ARMS = (
    "inline",
    "thread_pool1",
    "process_pool1",
    "thread_pool2",
    "process_pool2",
    "thread_pool4",
    "process_pool4",
    "thread_pool8",
    "process_pool8",
)
#: The four arm x regime blocks the L-13 report publishes at the knee.
L13_BLOCKS = ("inline/miss60k", "thread/miss60k", "inline/hit40k", "thread/hit40k")

#: A published figure is the raw figure rounded to two decimals, so the two can
#: differ by up to half of the last published digit. The extra epsilon absorbs
#: the binary representation of a value that lands exactly on a .xx5 boundary
#: (743.545 -> 743.54, not 743.55).
ROUNDING_TOLERANCE = 0.0051

#: "679.41/743.54 = 0.914 (9%)" - the shape of a hand-written noise-floor line.
SPREAD_PATTERN = re.compile(
    r"^(?P<num>\d+\.\d+)/(?P<den>\d+\.\d+) = (?P<ratio>\d+\.\d+) \((?P<pct>\d+)%\)$"
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _close(published: float, measured: float, tolerance: float = ROUNDING_TOLERANCE) -> bool:
    """True when ``published`` is ``measured`` rounded for publication."""
    return abs(float(published) - float(measured)) <= tolerance


def _fresh_import(path: Path, name: str) -> ModuleType:
    """Execute a harness module afresh, under a name of its own.

    A fresh execution is what makes the import-safety assertions meaningful:
    if the module was already imported, ``import`` is a no-op and the checks
    would pass without the module having done anything.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _module_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _entry_point(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not a top-level function of the module")


def _protocol_constants(path: Path, function_name: str) -> dict[str, Any]:
    """The constant entries of the artefact's ``protocol`` block, read from source.

    The L-13 harness builds its protocol dict inside ``main``; this pulls the
    literal entries out of the AST so the comparison is against what the
    harness WOULD write, not against a copy of it.
    """
    tree = _module_tree(path)
    entry = _entry_point(tree, function_name)
    assignment = next(
        node
        for node in ast.walk(entry)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "result"
        and isinstance(node.value, ast.Dict)
    )
    assert isinstance(assignment.value, ast.Dict)
    protocol_node = next(
        value
        for key, value in zip(assignment.value.keys, assignment.value.values, strict=True)
        if isinstance(key, ast.Constant) and key.value == "protocol"
    )
    assert isinstance(protocol_node, ast.Dict)
    constants: dict[str, Any] = {}
    for key, value in zip(protocol_node.keys, protocol_node.values, strict=True):
        assert isinstance(key, ast.Constant)
        try:
            constants[str(key.value)] = ast.literal_eval(value)
        except ValueError:
            # A Name (N_ACCOUNTS) or a computed list: checked against the
            # module constant instead, by the tests that own those constants.
            continue
    return constants


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope="module")
def l12_report() -> dict[str, Any]:
    return _read_json(L12_REPORT)


@pytest.fixture(scope="module")
def l12_raw() -> dict[str, Any]:
    return _read_json(L12_RAW)


@pytest.fixture(scope="module")
def l13_report() -> dict[str, Any]:
    return _read_json(L13_REPORT)


@pytest.fixture(scope="module")
def l13_raw() -> dict[str, Any]:
    return _read_json(L13_RAW)


@pytest.fixture(scope="module")
def l12_harness() -> ModuleType:
    return _fresh_import(L12_HARNESS, "_l12_pool_ratios_probe")


@pytest.fixture(scope="module")
def l13_harness() -> ModuleType:
    return _fresh_import(L13_HARNESS, "_l13_body_offload_probe")


# ---------------------------------------------------------------------------
# The evidence is present, and it is not excluded from version control
# ---------------------------------------------------------------------------


class TestTheRecoveredEvidenceIsPresent:
    def test_the_reports_and_their_raw_outputs_all_exist(self):
        for path in (L12_REPORT, L12_RAW, L13_REPORT, L13_RAW, L12_HARNESS, L13_HARNESS):
            assert path.is_file(), f"{path} is missing"
            assert path.stat().st_size > 0, f"{path} is empty"

    def test_the_recovered_harness_and_the_raw_outputs_are_not_git_ignored(self):
        """Not excluded from version control - which is the gap being closed.

        ``git check-ignore`` is used rather than ``git ls-files`` because it
        answers the question for a file that is not staged yet: the point is
        that the recovered instrument and its raw output CAN be committed, and
        the run_manifest gate is what refuses a run whose files are not
        tracked.
        """
        for path in (L12_HARNESS, L13_HARNESS, L12_RAW, L13_RAW):
            result = _git("check-ignore", "-q", str(path.relative_to(REPO_ROOT)))
            assert result.returncode != 0, f"{path} is excluded from version control"

    def test_the_reports_still_record_the_driver_as_it_was_run(self, l12_report, l13_report):
        """The historical ``in_repo: false`` is left as it was recorded.

        Rewriting it to ``true`` would be a lie about the run: the report was
        produced by a file that was on one machine's disk and nowhere else.
        What changed is that the file is NOW in the repository, which the
        harness's own docstring and this directory's FINDINGS.md record.
        """
        assert l12_report["instrument"]["file"] == "/home/ansible/l12_pool_ratios.py"
        assert l12_report["instrument"]["in_repo"] is False
        assert l13_report["instrument"]["file"] == "/home/ansible/l13_body_offload.py"
        assert l13_report["instrument"]["in_repo"] is False

    def test_the_harnesses_name_the_driver_they_were_recovered_from(self):
        for path, driver in ((L12_HARNESS, "l12_pool_ratios.py"), (L13_HARNESS, "l13_body_offload.py")):
            docstring = ast.get_docstring(_module_tree(path))
            assert docstring is not None
            assert f"/home/ansible/{driver}" in docstring
            assert "in_repo" in docstring
            assert "FINDINGS.md" in docstring


# ---------------------------------------------------------------------------
# The raw output is the harness's own output
# ---------------------------------------------------------------------------


class TestTheRawOutputIsTheHarnessOutput:
    def test_the_l12_protocol_block_is_the_recorded_one(self, l12_harness, l12_raw):
        """Byte-identical protocol block.

        Every value in the L-12 protocol block is a module constant, so the
        harness can be asked for the block without running a measurement. If
        the harness's constants drift, this fails - which is the point: the
        recorded protocol says "5 repetitions, 12 iterations, warmup batch 12,
        pools 1/2/4/8" and the harness must still be the thing that did that.
        """
        produced = l12_harness.build_artefact({}, {})["protocol"]
        assert produced == l12_raw["protocol"]

    def test_the_l12_artefact_has_the_recorded_shape(self, l12_harness, l12_raw):
        produced = l12_harness.build_artefact({}, {})
        assert set(produced) == set(l12_raw)
        assert produced["schema"] == l12_raw["schema"]
        assert produced["item"] == l12_raw["item"]
        assert produced["bead"] == l12_raw["bead"]
        assert set(produced["machine"]) == set(l12_raw["machine"])

    def test_the_l12_raw_protocol_agrees_with_the_published_report(self, l12_report, l12_raw):
        """The two artefacts describe the same run, not two runs.

        The published report rewords two strings for readability ("fixed
        iteration count per repetition, never a time-based ceiling" against
        "fixed iteration count, never time-based"), so only the values that
        must not differ are compared.
        """
        for key in (
            "warm_pools_only",
            "warmup_batch_discarded",
            "repetitions",
            "iterations_per_repetition",
            "gc_collect_before_each_rep",
            "pool_sizes",
            "criterion",
        ):
            assert l12_report["protocol"][key] == l12_raw["protocol"][key], key

    def test_the_l13_protocol_constants_are_the_recorded_ones(self, l13_raw):
        constants = _protocol_constants(L13_HARNESS, "main")
        for key, value in constants.items():
            assert key in l13_raw["protocol"], key
            assert l13_raw["protocol"][key] == value, key

    def test_the_l13_constants_are_the_recorded_ones(self, l13_harness, l13_raw):
        protocol = l13_raw["protocol"]
        assert l13_harness.N_ACCOUNTS == protocol["accounts"] == 250
        assert l13_harness.REQUESTS_PER_BLOCK == protocol["requests_per_block"] == 100
        assert l13_harness.REPS == protocol["reps"] == 5
        assert list(l13_harness.KNEE_SWEEP) == protocol["knee_sweep"] == [1, 2, 4, 8, 16, 32, 64]
        assert l13_harness.KNEE_FACTOR == protocol["knee_factor"] == 2.0

    def test_the_l12_constants_are_the_recorded_ones(self, l12_harness, l12_raw):
        protocol = l12_raw["protocol"]
        assert l12_harness.REPS == protocol["repetitions"] == 5
        assert l12_harness.ITERS_PER_REP == protocol["iterations_per_repetition"] == 12
        assert l12_harness.WARMUP_BATCH == protocol["warmup_batch_discarded"] == 12
        assert list(l12_harness.POOL_SIZES) == protocol["pool_sizes"] == [1, 2, 4, 8]
        assert l12_harness.PRODUCTION_CORE_COUNT == 4

    def test_the_l13_bodies_are_the_recorded_ones(self, l13_harness, l13_raw, l13_report):
        """The bytes on the wire are the bytes that were measured.

        The report publishes the wire size, and the harness builds the body
        from a filler and a sentence; if either changed, the measurement would
        no longer be of what it says it is.
        """
        assert len(l13_harness.MISS_WIRE) == l13_raw["regimes"]["miss60k"]["wire_bytes"] == 61473
        assert len(l13_harness.HIT_WIRE) == l13_raw["regimes"]["hit40k"]["wire_bytes"] == 40992
        assert l13_report["regimes"]["miss60k"]["wire_bytes"] == 61473
        assert l13_report["regimes"]["hit40k"]["wire_bytes"] == 40992

    @pytest.mark.parametrize("regime", L12_REGIMES)
    def test_every_offload_arm_submitted_one_scan_per_task(self, l12_raw, regime):
        """The bug the report's own ``instrument`` block calls out.

        The first version of this harness submitted ONE task per repetition and
        that task looped over all iterations internally, so a pool of four
        received one task and three workers idled: every arm came out flat and
        the ratios measured dispatch overhead instead of parallel throughput.
        The property that fixes it is per-arm, so it is pinned per-arm.
        """
        for arm, measured in l12_raw["regimes"][regime]["arms"].items():
            assert measured["scans_per_task"] == 1, arm
            assert measured["tasks_submitted_per_repetition"] == 12, arm
            assert measured["iterations_per_repetition"] == 12, arm
            assert measured["concurrency"] == measured["pool_size"], arm

    def test_the_harness_submits_one_scan_per_task(self):
        """The harness itself, not just the record of it.

        Every ``executor.submit`` in ``measure`` must ask for exactly ONE
        iteration. A harness that regressed to a task per repetition would
        reproduce the flat arms silently: the numbers would still be published
        and would still be internally consistent, and the defect would only
        show up as "pool size has no effect".
        """
        function = _entry_point(_module_tree(L12_HARNESS), "measure")
        submits = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "submit"
        ]
        assert submits, "measure() submits nothing"
        for call in submits:
            assert len(call.args) == 3, ast.unparse(call)
            iteration_count = call.args[2]
            assert isinstance(iteration_count, ast.Constant), ast.unparse(call)
            assert iteration_count.value == 1, ast.unparse(call)

    def test_the_l12_bodies_sit_on_the_prefilter_boundary(self, l12_harness, l12_raw):
        """The two regimes must be on OPPOSITE sides of the prefilter.

        The raw artefact records the properties it asserted BEFORE timing. If
        the filler or the sentence changed such that both regimes ran the same
        code, every ratio in the report would be a measurement of nothing.
        """
        miss, hit = l12_harness.build_bodies()
        assert len(miss) == 60 * 1024
        assert len(hit) == 40 * 1024
        recorded = l12_raw["regimes"]["miss_60kb"]["properties"]
        assert recorded["miss_asserted_miss"] is True
        assert recorded["hit_asserted_hit"] is True
        assert recorded["both_asserted"] is True
        assert recorded["miss_bytes"] == len(miss)
        assert recorded["hit_bytes"] == len(hit)


# ---------------------------------------------------------------------------
# The published report is the raw numbers
# ---------------------------------------------------------------------------


class TestThePublishedL12FiguresAreTheMeasuredOnes:
    @pytest.mark.parametrize("regime", L12_REGIMES)
    @pytest.mark.parametrize("arm", L12_ARMS)
    def test_each_published_arm_figure_is_the_measured_median(self, l12_report, l12_raw, regime, arm):
        published = l12_report["regimes"][regime]["arms"][arm]
        measured = l12_raw["regimes"][regime]["arms"][arm]
        assert _close(published["scans_per_sec"], measured["median_of_medians_scans_per_sec"]), arm
        assert _close(published["min_of_medians"], measured["min_of_medians_scans_per_sec"]), arm

    @pytest.mark.parametrize("regime", L12_REGIMES)
    @pytest.mark.parametrize("arm", L12_ARMS)
    def test_each_published_ratio_is_the_measured_ratio(self, l12_report, l12_raw, regime, arm):
        published = l12_report["regimes"][regime]["arms"][arm]
        measured = l12_raw["regimes"][regime]["arms"][arm]
        if arm == "inline":
            # The inline arm is the reference, so it carries no ratio in the
            # raw output; the report publishes it as 1.0 by definition.
            assert "ratio_vs_inline_median_of_medians" not in measured
            assert published["ratio_median"] == 1.0
            assert published["ratio_min"] == 1.0
            return
        assert _close(published["ratio_median"], measured["ratio_vs_inline_median_of_medians"], 0.0011), arm
        assert _close(published["ratio_min"], measured["ratio_vs_inline_min_of_medians"], 0.0011), arm

    @pytest.mark.parametrize("regime", L12_REGIMES)
    def test_the_headline_is_the_production_core_count_figures(self, l12_report, l12_raw, regime):
        headline = l12_report["headline"][regime]
        production = l12_raw["regimes"][regime]["at_production_core_count"]
        assert production["core_count"] == l12_report["headline"]["production_core_count"] == 4
        assert production["pool_size"] == 4
        assert _close(headline["inline_scans_per_sec"], production["median_of_medians"]["inline_scans_per_sec"])
        assert _close(headline["thread_pool4_scans_per_sec"], production["median_of_medians"]["thread_scans_per_sec"])
        assert _close(
            headline["process_pool4_scans_per_sec"], production["median_of_medians"]["process_scans_per_sec"]
        )
        assert _close(headline["thread_ratio"], production["ratios_vs_inline"]["thread"], 0.0011)
        assert _close(headline["process_ratio"], production["ratios_vs_inline"]["process"], 0.0011)

    @pytest.mark.parametrize("regime", L12_REGIMES)
    def test_process_over_thread_is_the_ratio_of_the_two_published_figures(self, l12_report, l12_raw, regime):
        """The report's ``process_over_thread`` is computed from the PUBLISHED
        (two-decimal) figures, which is why it is not the exact inverse of the
        raw three-decimal ``thread_over_process``.
        """
        headline = l12_report["headline"][regime]
        recomputed = headline["process_pool4_scans_per_sec"] / headline["thread_pool4_scans_per_sec"]
        assert _close(headline["process_over_thread"], recomputed, 0.0011)
        raw_over_process = l12_raw["regimes"][regime]["at_production_core_count"]["thread_over_process"]
        assert _close(raw_over_process, 1 / headline["process_over_thread"], 0.0011)

    @pytest.mark.parametrize(
        "key",
        (
            "miss_inline_spread",
            "miss_thread_pool4_spread",
            "miss_process_pool4_spread",
            "hit_inline_spread",
            "hit_thread_pool4_spread",
            "hit_process_pool4_spread",
        ),
    )
    def test_the_noise_floor_line_quotes_the_published_figures(self, l12_report, key):
        """A hand-written noise-floor line must quote the figures it describes.

        The verdict rests on these numbers ("the thread arm on the hit regime
        at 0.86x is INSIDE its own 8% spread"), so a line that quoted a
        different arm's figures would make the verdict a different claim.
        """
        line = l12_report["noise_floor"][key]
        match = SPREAD_PATTERN.match(line)
        assert match is not None, f"{key} is not in the published shape: {line!r}"
        regime = "miss_60kb" if key.startswith("miss_") else "hit_40kb"
        arm = key[len("miss_") :] if key.startswith("miss_") else key[len("hit_") :]
        arm = arm[: -len("_spread")]
        published = l12_report["regimes"][regime]["arms"][arm]
        assert float(match["num"]) == published["min_of_medians"]
        assert float(match["den"]) == published["scans_per_sec"]

    @pytest.mark.parametrize(
        "key",
        (
            "miss_inline_spread",
            "miss_thread_pool4_spread",
            "miss_process_pool4_spread",
            "hit_inline_spread",
            "hit_thread_pool4_spread",
            "hit_process_pool4_spread",
        ),
    )
    def test_the_noise_floor_line_arithmetic_is_right(self, l12_report, key):
        match = SPREAD_PATTERN.match(l12_report["noise_floor"][key])
        assert match is not None
        numerator = float(match["num"])
        denominator = float(match["den"])
        ratio = float(match["ratio"])
        percentage = int(match["pct"])
        assert _close(ratio, numerator / denominator, 0.0011)
        assert abs(percentage - round((1 - ratio) * 100)) <= 1


class TestThePublishedL13FiguresAreTheMeasuredOnes:
    @pytest.mark.parametrize("block", L13_BLOCKS)
    def test_each_published_block_is_the_measured_block(self, l13_report, l13_raw, block):
        published = l13_report["blocks_at_knee"][block]
        measured = l13_raw["blocks"][block]
        for key in (
            "p95_median_of_medians_ms",
            "p95_min_of_medians_ms",
            "p95_max_of_medians_ms",
            "p50_median_of_medians_ms",
            "p50_min_of_medians_ms",
        ):
            assert _close(published[key], measured[key], 0.0011), f"{block}.{key}"
        assert published["per_rep_p95_ms"] == [round(value, 3) for value in measured["per_rep_p95_ms"]]
        assert published["statuses"] == measured["statuses"]

    def test_the_knee_sweep_rows_are_the_measured_rows(self, l13_report, l13_raw):
        published_rows = l13_report["knee_sweep"]["rows"]
        measured_rows = l13_raw["knee_sweep"]
        assert len(published_rows) == len(measured_rows)
        for published, measured in zip(published_rows, measured_rows, strict=True):
            assert published["concurrency"] == measured["concurrency"]
            assert _close(published["p50_ms"], measured["p50_ms"], 0.011)
            assert _close(published["p95_ms"], measured["p95_ms"], 0.011)
            assert _close(published["p95_over_baseline"], measured["p95_over_baseline"], 0.011)
            assert published["statuses"] == measured["statuses"]

    def test_the_knee_is_the_measured_knee(self, l13_report, l13_raw):
        assert l13_report["headline"]["knee_concurrency"] == l13_raw["knee"] == 4
        assert l13_report["knee_sweep"]["knee"] == 4

    def test_the_knee_is_the_first_concurrency_past_the_factor(self, l13_raw):
        """The knee rule, executed against the recorded sweep."""
        baseline = l13_raw["knee_sweep"][0]["p95_ms"]
        expected = next(
            row["concurrency"]
            for row in l13_raw["knee_sweep"]
            if row["p95_ms"] / baseline > l13_raw["protocol"]["knee_factor"]
        )
        assert l13_raw["knee"] == expected == 4

    def test_the_published_ratios_are_the_measured_ratios(self, l13_report, l13_raw):
        published = l13_report["headline"]["ratios_thread_over_inline_p95"]
        for regime, prefix in (("miss60k", "miss60k"), ("hit40k", "hit40k")):
            measured = l13_raw["ratios"][regime]
            assert _close(published[f"{prefix}_median_of_medians"], measured["ratio_median_of_medians"], 0.00011)
            assert _close(published[f"{prefix}_min_of_medians"], measured["ratio_min_of_medians"], 0.00011)

    def test_the_published_noise_floor_is_the_measured_spread(self, l13_report, l13_raw):
        published = l13_report["headline"]["noise_floor_pct"]
        for regime, prefix in (("miss60k", "miss60k"), ("hit40k", "hit40k")):
            measured = l13_raw["ratios"][regime]
            assert _close(published[f"{prefix}_inline"], measured["inline_spread_pct"], 0.011)
            assert _close(published[f"{prefix}_thread"], measured["thread_spread_pct"], 0.011)

    def test_the_published_protocol_agrees_with_the_measured_protocol(self, l13_report, l13_raw):
        for key in (
            "accounts",
            "requests_per_block",
            "reps",
            "knee_sweep",
            "knee_factor",
            "budgets_relaxed",
            "gc_collect_before_each_rep",
            "fixed_iteration_ceiling",
            "criterion",
        ):
            assert l13_report["protocol"][key] == l13_raw["protocol"][key], key

    def test_the_budgets_were_satisfied_not_relaxed(self, l13_raw, l13_report):
        """Both arms' blocks record the statuses they actually got.

        A 429 inside the sample is a budget being hit, which the report has to
        carry rather than hide: the L-13 report's own ``limits`` name it.
        """
        for block in L13_BLOCKS:
            statuses = l13_raw["blocks"][block]["statuses"]
            assert statuses.get("201", 0) > 0, block
            assert l13_raw["protocol"]["budgets_relaxed"] is False
            assert l13_report["protocol"]["budgets_relaxed"] is False


# ---------------------------------------------------------------------------
# The verdict follows from the figures
# ---------------------------------------------------------------------------


class TestTheVerdictFollowsFromThePublishedFigures:
    """An arm's advantage is a result only when the arms do not overlap.

    The report's verdict is three-way - the process arm is solid in both
    regimes, the thread arm is probably real but modest in the miss regime,
    and the thread arm in the hit regime is "INSIDE its own 8% spread" and
    therefore indistinguishable from parity. That is reproduced here from the
    published figures alone, using the overlap of the two arms' measured
    ranges: the ratio's plausible interval is
    ``[arm.min / other.median, arm.median / other.min]``, and parity is
    EXCLUDED exactly when that interval does not contain 1.0.
    """

    @staticmethod
    def _interval(report: dict[str, Any], regime: str, arm: str, other: str = "inline") -> tuple[float, float]:
        arms = report["regimes"][regime]["arms"]
        return (
            arms[arm]["min_of_medians"] / arms[other]["scans_per_sec"],
            arms[arm]["scans_per_sec"] / arms[other]["min_of_medians"],
        )

    def test_the_process_arm_is_outside_the_noise_in_both_regimes(self, l12_report):
        for regime in L12_REGIMES:
            low, high = self._interval(l12_report, regime, "process_pool4")
            assert low > 1.0, f"{regime}: the process arm's advantage is not established: {low}..{high}"

    def test_the_thread_arm_is_outside_the_noise_in_the_miss_regime(self, l12_report):
        low, high = self._interval(l12_report, "miss_60kb", "thread_pool4")
        assert low > 1.0, f"the thread arm's miss-regime advantage is not established: {low}..{high}"

    def test_the_thread_arm_is_inside_the_noise_in_the_hit_regime(self, l12_report):
        """The one arm the report declines to call a result.

        Parity is NOT excluded: the interval contains 1.0, so the arm is
        indistinguishable from inline and the report is right to call it
        "no reliable gain" rather than a regression.
        """
        low, high = self._interval(l12_report, "hit_40kb", "thread_pool4")
        assert low < 1.0 < high, f"parity IS excluded, which contradicts the report: {low}..{high}"

    def test_the_l13_ratios_are_all_inside_their_own_noise_floor(self, l13_report):
        """L-13's conclusion is parity, and the conclusion must follow.

        Every published ratio is within the arm's own spread of 1.0, so no arm
        is claimed faster or slower than the other.
        """
        ratios = l13_report["headline"]["ratios_thread_over_inline_p95"]
        floor = l13_report["headline"]["noise_floor_pct"]
        for regime in ("miss60k", "hit40k"):
            for statistic in ("median_of_medians", "min_of_medians"):
                ratio = ratios[f"{regime}_{statistic}"]
                spread = max(floor[f"{regime}_inline"], floor[f"{regime}_thread"])
                assert abs(ratio - 1.0) * 100 <= spread, f"{regime}/{statistic}: {ratio} against {spread}%"


# ---------------------------------------------------------------------------
# The process-arm checks are still empirical
# ---------------------------------------------------------------------------


class TestTheProcessArmChecksAreStillExecuted:
    def test_the_recorded_default_executor_check_refused(self, l13_raw):
        check = l13_raw["process_arm"]["1_default_executor_rejects_process_pools"]
        assert check["set_default_executor_succeeded"] is False
        assert "ThreadPoolExecutor" in check["error"]

    def test_the_recorded_pickling_check_refused(self, l13_raw):
        check = l13_raw["process_arm"]["2_production_callable_is_not_picklable"]
        assert check["lambda_pickles"] is False
        assert check["error"].startswith("AttributeError:")
        # The qualification names the ENCLOSING FUNCTION, which is why the
        # harness's lambda has to live in ``main`` and not one frame deeper.
        assert "main.<locals>.<lambda>" in check["error"]

    def test_the_recorded_in_place_mutation_would_be_lost(self, l13_raw):
        check = l13_raw["process_arm"]["3_in_place_mutation_would_be_lost"]
        assert check["mutation_survives"] is False
        assert check["parent_list"] == [1]
        assert check["child_copy"] == [1, 2]

    def test_the_harness_still_produces_those_three_outcomes(self, l13_harness):
        """Re-run the checks; the recorded conclusions must still hold.

        The lambda is built inside a function called ``main`` so the recorded
        error text's qualification is reproduced - a lambda defined one frame
        deeper would print a different name and the check would pass for the
        wrong reason.
        """
        detected: list[Any] = []

        def main() -> None:  # noqa: D103 - the NAME is the point, see below.
            class _Fake:
                def _check_for_patterns(self, *a: Any) -> None:
                    detected.append(1)

            prod_callable = lambda: _Fake()._check_for_patterns(b"x", "request_body", detected)  # noqa: E731
            checks = l13_harness._process_arm_checks(prod_callable, detected)
            assert checks["1_default_executor_rejects_process_pools"]["set_default_executor_succeeded"] is False
            assert "ThreadPoolExecutor" in checks["1_default_executor_rejects_process_pools"]["error"]
            assert checks["2_production_callable_is_not_picklable"]["lambda_pickles"] is False
            assert "main.<locals>.<lambda>" in checks["2_production_callable_is_not_picklable"]["error"]
            assert checks["3_in_place_mutation_would_be_lost"]["mutation_survives"] is False

        main()


# ---------------------------------------------------------------------------
# The harnesses obey the house rules
# ---------------------------------------------------------------------------


class TestTheHarnessesAreImportSafe:
    def test_importing_either_harness_does_not_move_the_working_directory(self, l12_harness, l13_harness):
        before = Path.cwd()
        _fresh_import(L12_HARNESS, "_l12_cwd_probe")
        _fresh_import(L13_HARNESS, "_l13_cwd_probe")
        assert Path.cwd() == before

    def test_importing_either_harness_does_not_change_the_environment(self, l12_harness, l13_harness):
        before = dict(os.environ)
        _fresh_import(L12_HARNESS, "_l12_env_probe")
        _fresh_import(L13_HARNESS, "_l13_env_probe")
        assert dict(os.environ) == before

    @pytest.mark.parametrize("path", (L12_HARNESS, L13_HARNESS))
    def test_neither_harness_imports_the_app_at_module_level(self, path):
        """``src.app`` builds its settings at import time.

        Importing it at module level would construct the settings with whatever
        environment happened to be around - a test session's, in this repo -
        so the app import is deferred into a function that runs after
        bootstrap().
        """
        for node in _module_tree(path).body:
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert not name.startswith("src.app"), f"{path.name} imports {name} at module level"

    @pytest.mark.parametrize("path,entry", ((L12_HARNESS, "main"), (L13_HARNESS, "cli")))
    def test_the_entry_point_calls_bootstrap_first(self, path, entry):
        """The environment is set before anything that needs it is imported.

        A harness whose entry point forgot to call bootstrap() would import
        ``src.app`` with no environment, and the failure would look like a
        configuration error rather than a missing call.
        """
        function = _entry_point(_module_tree(path), entry)
        body = [
            node
            for node in function.body
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
        ]
        assert body, f"{path.name}:{entry} has no body"
        first = body[0]
        assert isinstance(first, ast.Expr), f"{path.name}:{entry} does not start with a call"
        assert isinstance(first.value, ast.Call), f"{path.name}:{entry} does not start with a call"
        callee = first.value.func
        assert isinstance(callee, ast.Name) and callee.id == "bootstrap", (
            f"{path.name}:{entry} does not call bootstrap() first"
        )

    @pytest.mark.parametrize("path", (L12_HARNESS, L13_HARNESS))
    def test_each_harness_root_is_the_repository_root(self, path):
        """``ROOT`` is the repository root, not the harness's own directory.

        ``parents[2]`` from ``src/benchmarks/`` is the repository root; a
        harness that resolved its own directory would write its artefact
        somewhere nobody looks.
        """
        tree = _module_tree(path)
        root_assignment = next(
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "ROOT" for target in node.targets)
        )
        assert ast.unparse(root_assignment) == "ROOT = Path(__file__).resolve().parents[2]"
        assert (path.parents[2]).resolve() == REPO_ROOT.resolve()

    @pytest.mark.parametrize("path", (L12_HARNESS, L13_HARNESS))
    def test_the_app_never_imports_either_harness(self, path):
        """Operator-run harnesses are never on the application's import path."""
        name = path.stem
        offenders = [
            candidate
            for candidate in (REPO_ROOT / "src" / "app").rglob("*.py")
            if name in candidate.read_text(encoding="utf-8", errors="replace")
        ]
        assert not offenders, f"{name} is referenced by the app: {offenders}"

    def test_the_app_only_imports_the_side_effect_free_benchmark_modules(self):
        """``src.benchmarks`` is operator-run, but not every module in it is.

        ``src.app.core.monitoring`` deliberately delegates to
        ``src.benchmarks.run_manifest`` and cites ``src.benchmarks.protocol``
        and ``src.benchmarks.percentiles``; those modules have no import-time
        side effects. What must never happen is the app pulling in a module
        that CHDIRs, loads a dotenv file or mutates the environment at import
        time - which is what every operator harness in the package does, and
        what the two harnesses here are.
        """
        side_effect_free = {
            "src.benchmarks.run_manifest",
            "src.benchmarks.protocol",
            "src.benchmarks.percentiles",
            "src.benchmarks.security_state",
        }
        imported: set[str] = set()
        for candidate in (REPO_ROOT / "src" / "app").rglob("*.py"):
            for node in ast.walk(_module_tree(candidate)):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("src.benchmarks"):
                    imported.add(node.module or "")
                elif isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names if alias.name.startswith("src.benchmarks"))
        assert imported <= side_effect_free, f"the app imports side-effecting benchmark modules: {imported}"


if __name__ == "__main__":  # pragma: no cover - the module is a test file.
    sys.exit(pytest.main([__file__]))
