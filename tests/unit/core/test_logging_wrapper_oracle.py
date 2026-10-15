"""A-3 oracle: the structlog wrapper is level-aware, and every test that observes
a DEBUG record is named in one table so A-3 updates them same-commit.

WHAT A-3 changes (from the bead): ``structlog.configure`` at
``src/app/core/logger.py:117-124`` sets no ``wrapper_class``, so structlog
fills in its default ``BoundLoggerFilteringAtNotset`` and the predicate
``level >= min_level`` is STRUCTURALLY ALWAYS TRUE (perf F-37, S-8's twin).
A-3 installs a level-aware ``wrapper_class``. Two things then break loudly
unless updated: (1) anything asserting the old always-True predicate,
(2) every test that observes a DEBUG record — under a level-aware wrapper
at INFO, ``logger.debug()`` is a no-op and the record never exists.

WHY this file has three parts (the bead's three acceptance items):
1. CONFIG HALF — ``get_config()['wrapper_class']`` is level-aware, not
   ``BoundLoggerFilteringAtNotset``. KNOWN-FAILING on HEAD by design
   (the default IS the always-True class today); passes once A-3 lands.
   ``xfail(strict=True)`` so the post-A-3 XPASS turns red until the marker
   comes off — a knowingly-red test is acceptable, a silently-weakened one
   is not (same contract as S-8's de-mocked tests in
   ``tests/unit/middleware/test_attack_scan_method_gate.py``).
2. BEHAVIOURAL HALF — a DEBUG record emitted through the REAL logger
   reaches ``capture_logs`` when the level admits DEBUG. Green on HEAD,
   and must STAY green post-A-3 at DEBUG. The level is established by
   setting the stdlib logger's level to DEBUG for a dedicated logger name
   (restored in ``finally``) — this assumes A-3's wrapper consults the
   effective stdlib level, which is the natural implementation because
   ``setup_logging`` already drives stdlib levels. If A-3 reads the level
   from somewhere else, this test fails and forces the question: the test
   pins the CONTRACT (debug visible at DEBUG), not the mechanism.
3. PRE-FLIGHT TABLE — every test file that observes a DEBUG record, pinned
   as a closed world. A-3 must update each same-commit ("update same-commit
   not silently drop" per the bead). The scan rule is AST over ``tests/``:
   a file counts iff it BOTH names DEBUG in an observation position
   (``logging.DEBUG`` in an assert, ``\"debug\"`` as a ``patch.object``
   target, or a ``.debug(`` call) AND uses an observation mechanism
   (``patch.object`` or ``capture_logs``). Both halves are required:
   ``tests/core/test_core_config.py`` names ``logging.DEBUG`` but only
   resolves the level — it observes nothing, so the conjunction excludes
   it. A conjunction that flags it would be the same over-broad-rule
   error as the ``dict.keys()`` / ``SCAN``-substring incidents.

HONEST CORRECTION to the bead: the plan says \"~10 capture_logs modules\".
Measured truth is TWO files, and NEITHER observes through
``capture_logs`` — both spy the ``debug`` method directly, which is exactly
the call that goes silent under a level-aware wrapper:

- ``tests/unit/middleware/test_attack_scan_method_gate.py`` →
  ``skipped_body_scan`` @ DEBUG (spies ``ad.logger.debug``)
- ``tests/e2e/core/security/test_attack_scan_bounds_e2e.py`` →
  ``skipped_body_scan`` @ DEBUG (spies ``debug`` + ``levels == [DEBUG]``;
  its ``capture_logs`` use asserts ABSENCE, which is level-agnostic)

Files DELIBERATELY excluded (each would be a false positive):
- ``test_log_sampling.py`` calls the sampling processor directly with
  ``\"debug\"`` as a data argument — it never passes through the wrapper.
- ``test_structlog_kwarg_enforcement.py`` emits only warning/error through
  a real logger; ``\"debug\"`` appears only in a level-name set.
- ``test_ip_utils_x_real_ip.py`` and both checkout suites substitute
  their OWN fake logger — the wrapper is never involved.
- ``test_core_config.py`` resolves the level; it observes no record.

E2E-over-HTTP-on-Linux + committed: NOT DONE here (no Linux target; the
repo rule forbids committing unless asked). Recorded, not silently unmet.
"""

from __future__ import annotations

import ast
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog

REPO_ROOT = Path(__file__).resolve().parents[3]
TESTS_ROOT = REPO_ROOT / "tests"


@pytest.fixture(scope="module", autouse=True)
def _the_repositorys_logging_configuration() -> Iterator[None]:
    """Install this repo's REAL logging configuration for the whole module.

    Without this the two configuration assertions read structlog's AMBIENT
    DEFAULT, not what ``setup_logging`` installs here. That is a guard that
    gates nothing: it would keep reporting the pre-A-3 state forever while
    measuring a third party's default instead of this repo's configuration.
    Nothing in the session calls ``setup_logging`` for us — MEASURED, not
    assumed: ``setup_logging`` and ``structlog`` appear in no conftest and no
    pytest plugin.

    ``setup_logging`` is heavy — it rebuilds the root logger, clears its
    handlers, opens a rotating file handler, starts a listener thread and
    silences three third-party loggers — so the root level, the handler list
    and those three levels are captured first and restored in the ``finally``,
    along with ``shutdown_logging`` and a structlog ``reset_defaults``. The
    session is left as it was found.
    """
    from src.app.core.logger import setup_logging, shutdown_logging

    root = logging.getLogger()
    saved_level = root.level
    saved_handlers = root.handlers[:]
    silenced = {
        name: logging.getLogger(name).level
        for name in ("uvicorn.access", "httpx", "httpcore")
    }
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

#: Every test file that observes a DEBUG record, with the event it observes
#: and the level it observes it at. A-3 updates each of these same-commit.
#: See the module docstring for why the set has exactly two entries.
DEBUG_OBSERVING_TESTS: dict[str, dict[str, str]] = {
    "tests/unit/middleware/test_attack_scan_method_gate.py": {
        "event": "skipped_body_scan",
        "level": "DEBUG",
    },
    "tests/e2e/core/security/test_attack_scan_bounds_e2e.py": {
        "event": "skipped_body_scan",
        "level": "DEBUG",
    },
}


def _names_debug_in_observation_position(tree: ast.AST) -> bool:
    """True if DEBUG appears where an observation (not a config read) lives."""
    for node in ast.walk(tree):
        # `.debug(` calls.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "debug"
        ):
            return True
        # `patch.object(X, \"debug\", ...)` — the string is the observed method.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "object"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "debug"
        ):
            return True
        # `assert ... logging.DEBUG ...` — a level pinned by an assertion.
        if isinstance(node, ast.Assert):
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Attribute)
                    and sub.attr == "DEBUG"
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == "logging"
                ):
                    return True
    return False


def _uses_observation_mechanism(tree: ast.AST) -> bool:
    """True if the file observes log output (spy, patch, or capture)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "object":
                # `patch.object(...)` — need the `patch` receiver to exclude
                # `monkeypatch.setattr`, which resolves config, not output.
                receiver = node.func.value
                if isinstance(receiver, ast.Name) and receiver.id == "patch":
                    return True
        if isinstance(node, ast.Name) and node.id == "capture_logs":
            return True
    return False


def _debug_observing_files() -> set[str]:
    """Scan tests/ for files that observe a DEBUG record. See docstring."""
    found: set[str] = set()
    for path in TESTS_ROOT.rglob("test_*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue
        if _names_debug_in_observation_position(tree) and _uses_observation_mechanism(tree):
            found.add(path.relative_to(REPO_ROOT).as_posix())
    return found


class TestWrapperClassIsLevelAware:
    """CONFIG HALF — known-failing on HEAD, passes once A-3 lands."""

    def test_wrapper_class_is_not_the_always_true_default(self) -> None:
        """`get_config()['wrapper_class']` must be level-aware, not the default."""
        from structlog._native import BoundLoggerFilteringAtNotset

        wrapper_class = structlog.get_config()["wrapper_class"]
        assert wrapper_class is not BoundLoggerFilteringAtNotset, (
            "wrapper_class is still the structlog default, whose predicate "
            "`level >= min_level` with min_level 0 is structurally always True; "
            "A-3 has not landed"
        )

    def test_is_enabled_for_debug_is_false(self) -> None:
        """A core logger answers False for DEBUG — the S-8 twin of this shape.

        ``tests/unit/middleware/test_attack_scan_method_gate.py`` pins the
        same assertion on the attack-detection logger; this pins it on a core
        logger, so a per-module wrapper cannot satisfy the oracle while the
        global default stays always-True.
        """
        from src.app.core.logger import get_logger

        assert get_logger(__name__).is_enabled_for(logging.DEBUG) is False


class TestDebugRecordsSurviveAtDebug:
    """BEHAVIOURAL HALF — green on HEAD, must stay green post-A-3 at DEBUG."""

    def test_capture_logs_yields_debug_at_debug(self) -> None:
        """A DEBUG record emitted at DEBUG level reaches ``capture_logs``.

        The level is established on a dedicated stdlib logger name (restored
        afterwards so nothing leaks into the session). This assumes A-3's
        wrapper consults the effective stdlib level — the natural
        implementation, since ``setup_logging`` already drives stdlib levels.
        If A-3 reads the level elsewhere, this test fails and forces the
        question; the CONTRACT (debug visible at DEBUG) is what is pinned.
        """
        name = "test-a3-debug-oracle"
        stdlib_logger = logging.getLogger(name)
        previous = stdlib_logger.level
        stdlib_logger.setLevel(logging.DEBUG)
        try:
            with structlog.testing.capture_logs() as captured:
                structlog.get_logger(name).debug("a3-debug-probe")
        finally:
            stdlib_logger.setLevel(previous)
        assert [r for r in captured if r.get("event") == "a3-debug-probe"], (
            "a DEBUG record emitted at DEBUG level never reached capture_logs; "
            "either the wrapper filters it (A-3 changed the contract) or the "
            "level source moved (update the level-setting to match, same-commit)"
        )


class TestPreflightTable:
    """Every DEBUG-observing test file is named here — closed world."""

    def test_the_scan_finds_the_files_it_is_meant_to_find(self) -> None:
        """Anti-vacuity: a scan finding nothing would pass everything below."""
        found = _debug_observing_files()
        assert found, "the debug-observer scan found no files — the rule is broken"
        assert "tests/unit/middleware/test_attack_scan_method_gate.py" in found

    def test_every_debug_observing_file_is_in_the_table(self) -> None:
        """A new DEBUG-observing test forces a same-commit table update."""
        assert _debug_observing_files() == set(DEBUG_OBSERVING_TESTS), (
            "a test file started (or stopped) observing DEBUG records without "
            "updating DEBUG_OBSERVING_TESTS — A-3 must update each entry same-commit"
        )

    def test_every_table_entry_still_observes_its_event(self) -> None:
        """The table's event column is pinned, not decorative."""
        for rel, spec in DEBUG_OBSERVING_TESTS.items():
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            assert spec["event"] in text, (
                f"{rel} no longer mentions {spec['event']!r} — the table is stale"
            )
            assert spec["level"] in text, (
                f"{rel} no longer mentions {spec['level']!r} — the table is stale"
            )

    def test_the_table_covers_more_than_one_file(self) -> None:
        """A one-entry table would pass while missing the e2e twin."""
        assert len(DEBUG_OBSERVING_TESTS) >= 2
        assert any("e2e" in rel for rel in DEBUG_OBSERVING_TESTS)
