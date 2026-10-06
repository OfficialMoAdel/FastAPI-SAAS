"""A-9 oracle tests: the microbenchmark arm stays labelled and the lag probe works.

Two halves, matching the two `u71j` deliverables:

1. STATIC WORKFLOW TESTS (parsed YAML, not text grep): the `microbenchmark`
   job exists in .github/workflows/performance.yml, carries the bead label
   verbatim, and contains NO compare step, NO baseline overwrite, and NO
   p95/throughput/RTT columns. Plus a pin that the old auto-commit baseline
   step is neutralized (PF-12/AMD-A13 + A-13): it must not `cp`, commit, or
   push benchmark numbers over the hand-authored stub.
2. PROBE BEHAVIOUR TESTS: `scripts/probe_loop_lag.py` sampling math
   (summarize on synthetic errors, empty-input and bad-arg rejection,
   JSON shape, signal-handler install). The probe's own `--verify`
   self-check is NOT driven here (it sleeps ~2.5s+ of wall time by design).

`import yaml` below is a TRANSITIVE dependency (in uv.lock, not a direct
pyproject dep; full `uv sync` in CI installs the lock so it resolves). That
is deliberate, not sloppiness: if pyyaml ever disappears, this gate must
fail LOUD (ImportError), never silently skip — an importorskip here would
be a vacuous pass on the very file that proves CI labelling.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]  # transitive dep; stubs not vendored

from scripts import probe_loop_lag

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "performance.yml"

MICROBENCHMARK_LABEL = "microbenchmark only - shared runner, co-located stores, NOT a capacity measurement"

#: Tokens that must never appear inside the microbenchmark job: any of them
#: would turn the labelled arm back into an unlabelled capacity claim.
_FORBIDDEN_IN_MICROBENCH = (
    "--benchmark-only",
    "--benchmark-json",
    "--benchmark-columns",
    "benchmark-results.json",
    "compare-summary",
    "git push",
    "git commit",
    "Update repository baseline",
    "p95",
    "throughput",
)


def _workflow() -> dict:
    assert WORKFLOW.is_file(), f"workflow file is gone: {WORKFLOW}"
    with WORKFLOW.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    assert isinstance(doc, dict) and doc, "workflow YAML parses to nothing"
    return doc


def _microbenchmark_job() -> dict:
    jobs = _workflow().get("jobs", {})
    assert "microbenchmark" in jobs, sorted(jobs)
    assert "benchmark" in jobs, "the existing arm must still be there"
    job = jobs["microbenchmark"]
    assert isinstance(job, dict)
    return job


def _job_text(job: dict) -> str:
    """Every shell line of the job, joined (steps may hold lists or maps)."""
    out: list[str] = []
    steps = job.get("steps", [])
    assert steps, "microbenchmark job has no steps"
    for step in steps:
        if isinstance(step, dict):
            for key in ("run", "name", "uses"):
                value = step.get(key)
                if isinstance(value, str):
                    out.append(value)
    assert out, "no readable step text in the microbenchmark job"
    return "\n".join(out)


class TestMicrobenchmarkJobIsLabelled:
    def test_job_exists_and_carries_the_label_verbatim(self) -> None:
        job = _microbenchmark_job()
        assert MICROBENCHMARK_LABEL in str(job.get("name", "")), job.get("name")

    def test_no_compare_baseline_or_capacity_columns(self) -> None:
        text = _job_text(_microbenchmark_job())
        for token in _FORBIDDEN_IN_MICROBENCH:
            assert token not in text, token

    def test_artefact_carries_disclaimer_and_no_gate_a(self) -> None:
        text = _job_text(_microbenchmark_job())
        # The heredoc below is a PYTHON script (json.dump renders False as
        # false at runtime), so the source pins the Python literal exactly.
        assert '"gate_a_satisfiable": False,' in text
        assert "microbench-run.json" in text

    def test_probe_runs_in_background_and_results_are_uploaded(self) -> None:
        text = _job_text(_microbenchmark_job())
        assert "probe_loop_lag.py" in text
        assert "loop-lag.json" in text
        job = _microbenchmark_job()
        uses = [s.get("uses", "") for s in job["steps"] if isinstance(s, dict)]
        assert any("upload-artifact" in u for u in uses), uses


class TestMicrobenchmarkJobPinsUvloop:
    """AMD A-9 delta (FastAPI_saas-u0ko): the arm's platform claim is ubuntu +
    uvloop, so the job must assert uvloop is installed (fail LOUD, not silent)
    and the artefact must record it."""

    def _step_names(self) -> list[str]:
        job = _microbenchmark_job()
        return [str(s.get("name", "")) for s in job["steps"] if isinstance(s, dict)]

    def test_assert_uvloop_step_exists(self) -> None:
        names = self._step_names()
        assert any("uvloop" in n.lower() for n in names), names

    def test_assert_step_imports_uvloop(self) -> None:
        text = _job_text(_microbenchmark_job())
        assert "import uvloop" in text

    def test_artefact_records_uvloop_version(self) -> None:
        text = _job_text(_microbenchmark_job())
        assert '"uvloop": uvloop.__version__,' in text


class TestBaselineOverwriteIsNeutralized:
    def _baseline_step(self) -> dict:
        jobs = _workflow().get("jobs", {})
        steps = jobs["benchmark"]["steps"]
        matches = [
            s
            for s in steps
            if isinstance(s, dict) and str(s.get("name", "")).lower() == "update repository baseline"
        ]
        assert matches, "baseline step is gone entirely (neutralize, don't delete)"
        assert len(matches) == 1, [s.get("name") for s in matches]
        return matches[0]

    def test_no_commit_or_push_of_benchmark_numbers(self) -> None:
        text = str(self._baseline_step())
        assert "git push" not in text
        assert "git commit" not in text
        assert "cp benchmark-results.json" not in text

    def test_neutralization_names_its_reason(self) -> None:
        text = str(self._baseline_step())
        assert "PF-12" in text or "AMD-A13" in text


class TestProbeBehaviour:
    def test_summarize_math_on_synthetic_errors(self) -> None:
        errors = [0.0001 * i for i in range(100)]
        stats = probe_loop_lag.summarize(errors, 100.0, 0.0, 0.99)
        assert stats["commanded_rate_hz"] == 100.0
        assert stats["samples"] == 100
        assert stats["measured_rate_hz"] == pytest.approx(100 / 0.99, rel=0.02)
        assert stats["mean_abs_error_us"] == pytest.approx(4950.0, rel=0.01)
        assert stats["max_abs_error_us"] == pytest.approx(9900.0, rel=0.01)
        assert stats["overruns"] == 0

    def test_overruns_counted_not_averaged_away(self) -> None:
        errors = [0.001] * 90 + [0.05] * 10
        stats = probe_loop_lag.summarize(errors, 100.0, 0.0, 0.99)
        assert stats["overruns"] == 10

    def test_empty_errors_rejected(self) -> None:
        with pytest.raises(ValueError):
            probe_loop_lag.summarize([], 100.0, 0.0, 1.0)

    def test_non_positive_args_rejected(self) -> None:
        with pytest.raises(ValueError):
            asyncio.run(probe_loop_lag.sample_lag(0.0, 1.0))
        with pytest.raises(ValueError):
            asyncio.run(probe_loop_lag.sample_lag(100.0, 0.0))

    def test_stats_json_round_trips_exactly(self) -> None:
        errors = [0.0002 * i for i in range(50)]
        stats = probe_loop_lag.summarize(errors, 50.0, 1.0, 1.98)
        text = json.dumps(stats)
        assert json.loads(text) == stats

    def test_term_handler_installs_and_state_is_testable(self) -> None:
        assert probe_loop_lag._install_term_handler() in (True, False)
        assert "terminated" in probe_loop_lag._term_state
