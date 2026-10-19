"""S-8: the attack-scan method gate's debug events, counted per level.

What this pins (bead FastAPI_saas-uo44, "S-8 impl"):

S-8's own arithmetic is that the method gate "moves capacity, not p95": the
guard and the event it suppresses are 0 us of the 4.09 ms uncontended floor and
0.00 % of the 740 ms p95. A latency figure therefore cannot substantiate that
claim -- there is no duration to substantiate. What CAN be substantiated is a
COUNT: how many times the middleware evaluates the guard, and how many times
it invokes ``logger.debug``, on a bodyless GET, at each effective level.

The counter is a structural property of the call site and the configuration,
not a timing of a machine, so the same counts come out on any host. That is
what ``src/benchmarks/s8_logger_call_counter.py`` measures, and this oracle:

* runs the harness's own ``count_arm`` in-process and checks the counts;
* checks the RECORDED artefact against a fresh run, so the published numbers
  are the harness's numbers and not a hand-written summary;
* checks that the harness REFUSES a run whose arms disagree, because a counter
  that cannot show the call site being reached the same number of times in
  every arm has measured something other than what it claims;
* checks the harness is import-safe, because a harness that moves the
  process's cwd or environment at import time corrupts the whole test session.

The counter's own limits, carried in the artefact: the requests are driven
in-process through the middleware's ASGI ``__call__`` with a stub downstream
application (the END-TO-END-over-HTTP leg is a separate deliverable), the
detection paths are not exercised (the requests are benign by construction),
and the counter is meaningless against structlog's ambient default -- whose
predicate is structurally always True -- which is why the harness installs
this repository's real logging configuration first.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import logging
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import structlog

REPO_ROOT = Path(__file__).resolve().parents[3]
HARNESS_PATH = REPO_ROOT / "src" / "benchmarks" / "s8_logger_call_counter.py"
ARTEFACT_PATH = REPO_ROOT / "perf-results" / "RESULT-20261019-uo44" / "s8_logger_call_counter.json"

#: The module whose stdlib logger the middleware's guard consults.
MODULE_LOGGER = "src.app.middleware.attack_detection"

#: The event the middleware emits when it skips the body scan.
SKIP_EVENT = "attack_detection_skipped_body_scan_for_method"

#: A small number of requests: the counts are exact, not statistical.
REQUESTS = 3


# --- fixtures ---------------------------------------------------------------


@pytest.fixture(scope="module")
def harness() -> Any:
    """The harness, loaded the way an operator would run it.

    Loaded from its file rather than imported as ``src.benchmarks.*`` so that
    the import-safety checks below are REAL checks of this file's import
    behaviour, not of a package that has already been imported.
    """
    spec = importlib.util.spec_from_file_location("s8_logger_call_counter_under_test", HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module", autouse=True)
def _the_repositorys_logging_configuration() -> Iterator[None]:
    """Install this repo's REAL logging configuration for the whole module.

    A-3 makes ``is_enabled_for`` consult the effective stdlib level, which is
    only true once ``setup_logging`` has run -- nothing in a test session runs
    it for us. Without this fixture the predicate would be structlog's AMBIENT
    default (``BoundLoggerFilteringAtNotset``, structurally always True) and
    every arm would report a debug call per request, which would make the
    whole counter vacuous.

    ``setup_logging`` clears the root handlers, sets the root level, opens a
    rotating file handler, starts a QueueListener thread and silences
    ``uvicorn.access``/``httpx``/``httpcore``; all of it is restored here so the
    rest of the session is unaffected. The same save/restore is used by
    tests/unit/middleware/test_attack_scan_method_gate.py, and the two modules
    must not fight over the global root logger.
    """
    from src.app.core.logger import setup_logging, shutdown_logging

    root = logging.getLogger()
    saved_level = root.level
    saved_handlers = root.handlers[:]
    silenced = {name: logging.getLogger(name).level for name in ("uvicorn.access", "httpx", "httpcore")}
    setup_logging()
    try:
        yield
    finally:
        shutdown_logging()
        structlog.reset_defaults()
        root.setLevel(saved_level)
        root.handlers[:] = saved_handlers
        for name, level in silenced.items():
            logging.getLogger(name).setLevel(level)


@pytest.fixture(scope="module")
def artefact() -> dict[str, Any]:
    """The recorded artefact."""
    return json.loads(ARTEFACT_PATH.read_text(encoding="utf-8"))


async def _arm(harness: Any, name: str, module_level: int | None, requests: int = REQUESTS) -> dict[str, Any]:
    """Run one arm of the counter, in-process."""
    return await harness.count_arm(name=name, module_level=module_level, requests=requests)


# --- the counts -------------------------------------------------------------


class TestTheCounterCountsTheCallSite:
    """The counter's own claim, executed."""

    async def test_the_guard_is_evaluated_once_per_request_in_every_arm(self, harness: Any) -> None:
        """The invariant that makes the arm comparison mean anything.

        If the guard were evaluated a different number of times per arm, a
        difference in the debug count could be explained by the call site being
        reached a different number of times rather than by the predicate's
        value. The counter counts the evaluations separately for that reason.
        """
        for name, module_level in harness.ARMS:
            arm = await _arm(harness, name, module_level)
            assert arm["guard_evaluations"] == REQUESTS, (
                f"arm {name!r} evaluated the guard {arm['guard_evaluations']} time(s) for {REQUESTS} request(s); "
                "the arms are not comparable"
            )

    async def test_the_guard_asks_about_debug(self, harness: Any) -> None:
        """The guard is consulted about the DEBUG level, and about nothing else."""
        arm = await _arm(harness, "info", logging.INFO)
        assert arm["guard_evaluation_levels"] == ["DEBUG"]

    async def test_the_debug_arm_emits_one_event_per_request(self, harness: Any) -> None:
        """At DEBUG the skip is announced once per bodyless request."""
        arm = await _arm(harness, "debug", logging.DEBUG)
        assert arm["predicate_value"] is True
        assert arm["debug_calls"] == REQUESTS
        assert arm["debug_events"] == [SKIP_EVENT]

    async def test_the_info_arm_emits_nothing(self, harness: Any) -> None:
        """At INFO the guard closes, so the call is never attempted."""
        arm = await _arm(harness, "info", logging.INFO)
        assert arm["predicate_value"] is False
        assert arm["debug_calls"] == 0
        assert arm["debug_events"] == []

    async def test_the_default_arm_follows_the_root(self, harness: Any) -> None:
        """With the module logger at NOTSET, the ROOT's level decides.

        This is the arm that would have been misread: against structlog's
        ambient default the predicate is structurally always True, and the arm
        would report a debug call per request regardless of the root. Under
        this repository's configuration the root decides, and the arm agrees
        with the INFO arm whenever the root is at INFO.
        """
        arm = await _arm(harness, "default", None)
        root_level = logging.getLogger().getEffectiveLevel()
        assert arm["effective_level"] == logging.getLevelName(root_level)
        assert arm["predicate_value"] is (root_level <= logging.DEBUG)
        assert arm["debug_calls"] == (REQUESTS if arm["predicate_value"] else 0)

    async def test_the_three_arms_differ_only_in_the_predicate(self, harness: Any) -> None:
        """Every arm reaches the call site the same number of times.

        Read together with the counts above, this is the whole claim: the
        guard is evaluated once per request in EVERY arm, and the only thing
        that changes between them is whether the predicate lets the event out.
        """
        arms = {name: await _arm(harness, name, level) for name, level in harness.ARMS}
        assert {arm["guard_evaluations"] for arm in arms.values()} == {REQUESTS}
        assert [arms[name]["debug_calls"] for name, _ in harness.ARMS] == [REQUESTS, 0, 0]


# --- the recorded artefact -------------------------------------------------


class TestTheRecordedArtefactIsTheHarnesssNumbers:
    """The published counts are the harness's counts."""

    def test_the_artefact_is_present_and_not_excluded_from_version_control(self) -> None:
        assert ARTEFACT_PATH.is_file(), f"{ARTEFACT_PATH} is missing"
        assert ARTEFACT_PATH.stat().st_size > 0
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", str(ARTEFACT_PATH.relative_to(REPO_ROOT))],
            cwd=REPO_ROOT,
            capture_output=True,
        )
        assert ignored.returncode != 0, "the recorded artefact is excluded from version control"

    def test_the_artefact_records_what_it_is(self, artefact: dict[str, Any]) -> None:
        assert artefact["schema"] == "s8_logger_call_counter/1"
        assert artefact["bead"] == "FastAPI_saas-uo44"
        assert artefact["item"] == "S-8"
        assert artefact["method"]["in_repo"] is True
        assert artefact["method"]["driver"] == "src/benchmarks/s8_logger_call_counter.py"

    def test_the_artefact_makes_no_latency_claim(self, artefact: dict[str, Any]) -> None:
        """S-8's arithmetic is 0 us, so there is no duration to report.

        A reader must not be able to read a latency figure out of this
        artefact, because there is none: the quantity is a COUNT.
        """
        assert "COUNTS" in artefact["quantity"]
        assert "0 us" in artefact["quantity"]
        assert artefact["machine"]["host_relevant"] is False
        assert artefact["machine"]["host_relevant_why"]
        limits = " ".join(artefact["limits"])
        assert "not a latency measurement" in limits
        assert "0.00 %" in limits

    def test_the_recorded_counts_are_internally_consistent(self, artefact: dict[str, Any]) -> None:
        requests = artefact["requests_per_arm"]
        assert requests > 0
        for name, arm in artefact["arms"].items():
            assert arm["requests"] == requests
            assert arm["guard_evaluations"] == requests, f"arm {name!r} did not reach the call site once per request"
            expected = requests if arm["predicate_value"] else 0
            assert arm["debug_calls"] == expected, f"arm {name!r} made debug calls its predicate forbids"

    def test_the_recorded_verdict_is_the_recorded_counts(self, artefact: dict[str, Any]) -> None:
        verdict = artefact["verdict"]
        assert verdict["guard_evaluations_per_arm"] == {
            name: arm["guard_evaluations"] for name, arm in artefact["arms"].items()
        }
        assert verdict["debug_calls_per_arm"] == {name: arm["debug_calls"] for name, arm in artefact["arms"].items()}
        assert verdict["predicate_per_arm"] == {
            name: arm["predicate_value"] for name, arm in artefact["arms"].items()
        }
        assert artefact["arms"]["debug"]["debug_events"] == [SKIP_EVENT]
        assert artefact["arms"]["info"]["debug_events"] == []
        assert artefact["arms"]["default"]["debug_events"] == []

    async def test_a_fresh_run_reproduces_the_recorded_shape(self, artefact: dict[str, Any], harness: Any) -> None:
        """Re-run the counter and get the same counts, arm for arm.

        The recorded run used more requests than this oracle does, so the
        absolute numbers differ; what has to be identical is the SHAPE: the
        guard evaluated once per request in every arm, one debug call per
        request at DEBUG, and none at or above INFO.
        """
        recorded = artefact["arms"]
        for name, module_level in harness.ARMS:
            arm = await _arm(harness, name, module_level)
            assert arm["guard_evaluations"] == REQUESTS
            assert arm["predicate_value"] == recorded[name]["predicate_value"]
            assert (arm["debug_calls"] == REQUESTS) == recorded[name]["predicate_value"]
            assert arm["debug_events"] == recorded[name]["debug_events"]


# --- the refusals -----------------------------------------------------------


class TestTheCounterRefusesARunItCannotInterpret:
    """A counter that cannot show the call site being reached the same number
    of times in every arm has measured something other than what it claims."""

    def _arms(self) -> dict[str, dict[str, Any]]:
        return {
            "debug": {
                "arm": "debug",
                "requests": REQUESTS,
                "predicate_value": True,
                "guard_evaluations": REQUESTS,
                "debug_calls": REQUESTS,
                "debug_events": [SKIP_EVENT],
            },
            "info": {
                "arm": "info",
                "requests": REQUESTS,
                "predicate_value": False,
                "guard_evaluations": REQUESTS,
                "debug_calls": 0,
                "debug_events": [],
            },
            "default": {
                "arm": "default",
                "requests": REQUESTS,
                "predicate_value": False,
                "guard_evaluations": REQUESTS,
                "debug_calls": 0,
                "debug_events": [],
            },
        }

    def test_a_consistent_run_is_accepted(self, harness: Any) -> None:
        harness.check_arms(self._arms(), requests=REQUESTS)

    def test_an_arm_that_missed_the_call_site_is_refused(self, harness: Any) -> None:
        arms = self._arms()
        arms["info"]["guard_evaluations"] = REQUESTS - 1
        with pytest.raises(AssertionError, match="evaluated the guard"):
            harness.check_arms(arms, requests=REQUESTS)

    def test_a_debug_call_the_predicate_forbids_is_refused(self, harness: Any) -> None:
        arms = self._arms()
        arms["info"]["debug_calls"] = 1
        with pytest.raises(AssertionError, match="logger.debug call"):
            harness.check_arms(arms, requests=REQUESTS)

    def test_a_debug_arm_whose_predicate_was_false_is_refused(self, harness: Any) -> None:
        arms = self._arms()
        arms["debug"]["predicate_value"] = False
        arms["debug"]["debug_calls"] = 0
        with pytest.raises(AssertionError, match="debug arm's predicate"):
            harness.check_arms(arms, requests=REQUESTS)

    def test_an_info_arm_whose_predicate_was_true_is_refused(self, harness: Any) -> None:
        arms = self._arms()
        arms["info"]["predicate_value"] = True
        arms["info"]["debug_calls"] = REQUESTS
        with pytest.raises(AssertionError, match="info arm's predicate"):
            harness.check_arms(arms, requests=REQUESTS)


# --- import safety ----------------------------------------------------------


def _module_tree() -> ast.Module:
    return ast.parse(HARNESS_PATH.read_text(encoding="utf-8"))


def _is_main_guard(statement: ast.stmt) -> bool:
    """True for the ``if __name__ == "__main__":`` block, which does NOT run at import."""
    if not isinstance(statement, ast.If):
        return False
    test = statement.test
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and any(isinstance(op, ast.Eq) for op in test.ops)
    )


def _executed_at_import(tree: ast.Module) -> list[ast.stmt]:
    """The statements that ACTUALLY run when the module is imported.

    A function or class body does not run at import -- its statements are
    compiled and kept -- so a ``bootstrap()`` full of side effects is not a
    module-level side effect. The ``__main__`` guard does not run either,
    because on import ``__name__`` is not ``"__main__"``.
    """
    body = list(tree.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return [
        statement
        for statement in body
        if not isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and not _is_main_guard(statement)
    ]


def _calls_named(node: ast.AST, name: str) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name) and func.id == name:
                return True
            if isinstance(func, ast.Attribute) and func.attr == name:
                return True
    return False


class TestTheHarnessIsImportSafe:
    """A harness that changes the process at import time corrupts the session.

    The repository's rule is that a harness holds its side effects in a
    ``bootstrap()`` called only from ``__main__``: a developer ``.env`` in
    ``src/`` is gitignored by design, so a module-level
    ``load_dotenv(override=True)`` would clobber the environment
    ``tests/conftest.py`` sets for the WHOLE session.
    """

    def test_importing_the_harness_does_not_move_the_cwd_or_the_environment(self) -> None:
        import os

        before_cwd = Path.cwd()
        before_env = dict(os.environ)
        spec = importlib.util.spec_from_file_location("s8_logger_call_counter_import_probe", HARNESS_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert Path.cwd() == before_cwd
        assert dict(os.environ) == before_env

    def test_no_module_level_chdir_or_load_dotenv(self) -> None:
        for statement in _executed_at_import(_module_tree()):
            assert not _calls_named(statement, "chdir"), f"module-level chdir: {ast.dump(statement)}"
            assert not _calls_named(statement, "load_dotenv"), f"module-level load_dotenv: {ast.dump(statement)}"

    def test_no_module_level_environment_write(self) -> None:
        for statement in _executed_at_import(_module_tree()):
            for node in ast.walk(statement):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        source = ast.unparse(target)
                        assert "environ" not in source, f"module-level environment write: {source}"

    def test_no_module_level_import_of_the_application(self) -> None:
        for statement in _executed_at_import(_module_tree()):
            for node in ast.walk(statement):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("src.app"), f"module-level app import: {alias.name}"
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    assert not module.startswith("src.app"), f"module-level app import: {module}"

    def test_the_side_effects_live_in_bootstrap(self) -> None:
        tree = _module_tree()
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        assert "bootstrap" in functions
        bootstrap = functions["bootstrap"]
        assert _calls_named(bootstrap, "chdir")
        assert _calls_named(bootstrap, "load_dotenv")

    def test_bootstrap_runs_before_anything_under_src_app_is_imported(self) -> None:
        """``bootstrap()`` must precede the call that reaches the app.

        The harness's own entry point parses its arguments first and calls
        ``bootstrap()`` before it runs the measurement; what matters is that
        ``bootstrap()`` happens before the first statement that can import
        anything under ``src.app``, because the log level is read at
        settings-construction time.
        """
        tree = _module_tree()
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        cli = functions["cli"]
        body = [stmt for stmt in cli.body if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))]
        bootstrap_index = next(index for index, stmt in enumerate(body) if _calls_named(stmt, "bootstrap"))
        run_index = next(index for index, stmt in enumerate(body) if _calls_named(stmt, "run"))
        assert bootstrap_index < run_index, "bootstrap() must run before the measurement, which imports the app"

    def test_root_is_the_repository_root(self) -> None:
        tree = _module_tree()
        roots = [stmt for stmt in tree.body if isinstance(stmt, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "ROOT" for target in stmt.targets
        )]
        assert len(roots) == 1, "ROOT must be assigned exactly once, at module level"
        value = roots[0].value
        assert ast.unparse(value) == "Path(__file__).resolve().parents[2]"
        assert HARNESS_PATH.parents[2] == REPO_ROOT

    def test_the_harness_calls_no_clock_and_needs_no_raw_timer_allowlist(self) -> None:
        """A counter that calls no clock is not a microbenchmark.

        ``tests/unit/benchmarks/test_microbench_protocol.py`` classifies a
        file by whether it actually calls a clock, so a counter that calls
        none must be absent from that gate's lists rather than excused from
        them.
        """
        source = HARNESS_PATH.read_text(encoding="utf-8")
        for clock in ("perf_counter", "perf_counter_ns", "monotonic", "monotonic_ns", "process_time", "time.time"):
            assert clock not in source, f"the counter must not call a clock ({clock})"
        gate = REPO_ROOT / "tests" / "unit" / "benchmarks" / "test_microbench_protocol.py"
        gate_source = gate.read_text(encoding="utf-8")
        assert "s8_logger_call_counter" not in gate_source, (
            "a harness that calls no clock must not be classified as a microbenchmark"
        )
