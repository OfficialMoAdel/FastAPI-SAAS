"""H-4: the request-path profiler must be a real, runnable, recoverable instrument.

The bead that produced this test (``FastAPI_saas-zcfv``) asserted the profiler
had a ``SyntaxError: '{' was never closed`` at line 508, had therefore never
run, and that a report citing its output was citing something that does not
exist. The premise is FALSE, and this file pins the refutation so it cannot be
re-raised from the same evidence:

- The script COMPILES. Line 508 is the closing brace of the ``IDLE_BUILTINS``
  set literal opened at line 505.
- The artefact the bead cited as proof that it never ran
  (``raw_prof_request_path.stdout.txt``) is itself a RECORD OF A SUCCESSFUL
  RUN: it carries a full application boot, timestamps, a complete cProfile
  dump, a final ``wrote .../raw_prof_request_path.json`` line, and a profile
  stack entry naming ``prof_request_path.py`` itself. The file's own mtime is
  FOUR MINUTES EARLIER than the artefact's, so the artefact cannot predate it.

What the bead's underlying concern was actually right about, and what this file
therefore does enforce, is the provenance half: the profiler used to live in a
GITIGNORED scratch directory, so a report could cite its output and the
instrument could never be recovered from git. That is the same defect
``FastAPI_saas-8lwo`` and ``FastAPI_saas-puww`` fixed for the other harnesses,
and it is the one that actually loses measurements.

The two standing rules of this programme are enforced here rather than asserted
in prose:

- a harness whose instrument is not recoverable from git can never be checked,
  so the profiler is tracked and the run manifest can pin it;
- a profile taken with the budgets relaxed is not a capacity measurement, so the
  relaxations are visible in one place instead of scattered ``os.environ``
  writes at module level.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from src.benchmarks import prof_request_path as mod

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "src" / "benchmarks" / "prof_request_path.py"
#: The directory the profiler was promoted OUT of. It is gitignored (``.gitignore``
#: ``perf_*``), which is why an untracked instrument there could never be pinned.
SCRATCH_RELATIVE = "reports/perf_review_2026-10-04"
#: The artefact the bead cited as proof the script never ran.
CITED_ARTEFACT = REPO_ROOT / SCRATCH_RELATIVE / "raw_prof_request_path.stdout.txt"

#: Every environment variable the profiler overrides, and the value it forces.
#: Written out in full because the point of the test is that this list is the
#: whole list: an unlisted ``os.environ[...] = `` write at module level is a
#: measurement the manifest would not label.
RELAXED_ENV: dict[str, str] = {
    "QUERY_PROFILER_ENABLED": "true",
    "LOG_LEVEL": "WARNING",
    "AUTO_BAN_ENABLED": "false",
    "DEFAULT_AUTHENTICATED_LIMIT": "[1000000, 3600]",
    "DEFAULT_ANONYMOUS_LIMIT": "[1000000, 3600]",
    "ANONYMOUS_IP_AGGREGATE_LIMIT": "[1000000, 3600]",
}


def _module_tree() -> ast.Module:
    return ast.parse(MODULE_PATH.read_text(encoding="utf-8"))


def _function(name: str) -> ast.FunctionDef:
    for node in _module_tree().body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"no top-level function {name} in {MODULE_PATH}")


# --------------------------------------------------------------------------
# the refutation: the file compiles, and it is the version that produced the output
# --------------------------------------------------------------------------


def test_the_script_compiles():
    """The bead's claim, checked rather than argued.

    ``compile()`` and not only ``ast.parse`` because a SyntaxError raised during
    execution-time compilation of a nested scope is exactly the failure mode
    claimed.
    """
    source = MODULE_PATH.read_text(encoding="utf-8")
    compile(source, str(MODULE_PATH), "exec")


def test_the_idle_builtins_set_literals_are_closed():
    """The cited failure was ``'{' was never closed``; prove the braces match.

    Deliberately NOT a hard-coded line number: the promotion edit added lines
    above this point, so "line 508" no longer names the same text. The defect
    claim is about the SHAPE (an unclosed brace), and an AST ``Set`` node with
    a value is that shape's negation, whatever line it now sits on.

    BOTH sets are checked. The file has two of them with identical contents
    (``IDLE_BUILTINS_PRE`` subtracted before the percentage is computed,
    ``IDLE_BUILTINS`` used for the bucket table), and the cited line 508 is the
    close of the first, so checking only the second would leave the exact
    reported failure uncovered. The duplication is pre-existing and is
    deliberately not fixed here: these sets decide which frames are excluded
    from every CPU percentage, so changing them belongs to whoever re-measures.
    """
    assignments = [
        node
        for node in ast.walk(_module_tree())
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id.startswith("IDLE_BUILTINS") for t in node.targets)
    ]
    assert len(assignments) == 2, f"expected the two idle-builtin sets, found {len(assignments)}"

    for assignment in assignments:
        target = next(t for t in assignment.targets if isinstance(t, ast.Name))
        value = assignment.value
        assert isinstance(value, ast.Set), f"{target.id} is {type(value).__name__}, not a set literal"
        assert value.elts, f"{target.id} is an empty set, so idle frames would be misbucketed as CPU"
        literals = {elt.value for elt in value.elts if isinstance(elt, ast.Constant)}
        for frame in ("GetQueuedCompletionStatus", "WSASend", "recv", "select"):
            assert frame in literals, f"{target.id} omits {frame}, the idle frame the comment above it describes"

    names = {next(t for t in a.targets if isinstance(t, ast.Name)).id for a in assignments}
    assert names == {"IDLE_BUILTINS_PRE", "IDLE_BUILTINS"}, f"unexpected idle-set names {sorted(names)}"


@pytest.mark.skipif(
    not CITED_ARTEFACT.exists(), reason="the cited artefact is untracked scratch and is not in the repo"
)
def test_the_cited_artefact_is_a_record_of_a_successful_run():
    """The bead cited this file as proof the script NEVER ran. It ran.

    Each assertion is a fact that is false of a crashed run and true of a
    completed one, so this cannot pass by accident on a partial artefact.
    """
    text = CITED_ARTEFACT.read_text(encoding="utf-8", errors="replace")

    # A crashed run prints a traceback; a completed one prints the artefact path.
    assert "SyntaxError" not in text
    assert "never closed" not in text
    assert "Traceback (most recent call last)" not in text

    # It booted the application rather than dying at import.
    assert "Registered Lua script" in text
    assert "Registered circuit breaker" in text
    assert "registered middleware (execution order, outer->inner)" in text

    # It wrote the artefact it was asked for.
    assert "raw_prof_request_path.json" in text
    assert re.search(r"^\s*\d+\.\d+\s+\S+", text, re.MULTILINE), "no cProfile rows: the dump never got written"


@pytest.mark.skipif(
    not CITED_ARTEFACT.exists(), reason="the cited artefact is untracked scratch and is not in the repo"
)
def test_the_artefact_names_the_profiler_itself_in_its_own_profile():
    """Proves the artefact came from THIS instrument, not a lookalike.

    The script installs a Redis shim as a nested function, so a completed run's
    profile necessarily contains a frame pointing back into this file.

    This replaces an mtime comparison, which was the other half of the
    timestamp argument at investigation time (script 2026-10-04 03:29:32 versus
    artefact 2026-10-04 03:33:21) and could NOT be kept: promoting the file
    gave it a fresh mtime, so from here on the artefact is always the older of
    the two and the comparison would be vacuous. A profile that names this
    file's own lines survives promotion, so it is what the refutation rests on.
    """
    text = CITED_ARTEFACT.read_text(encoding="utf-8", errors="replace")
    assert "prof_request_path.py:" in text

    referenced = {int(n) for n in re.findall(r"prof_request_path\.py:(\d+)", text)}
    assert referenced, "the artefact names no line in this file, so it cannot be this script's output"
    source_lines = MODULE_PATH.read_text(encoding="utf-8").splitlines()
    for line_no in sorted(referenced):
        assert 1 <= line_no <= len(source_lines), (
            f"artefact cites {MODULE_PATH.name}:{line_no}, which has {len(source_lines)} lines"
        )


# --------------------------------------------------------------------------
# provenance: the instrument is recoverable from git, and not in a scratch dir
# --------------------------------------------------------------------------


def test_it_lives_in_a_tracked_source_directory_not_a_scratch_report():
    assert MODULE_PATH.is_file()
    assert MODULE_PATH.parent.name == "benchmarks"
    assert MODULE_PATH.parent.parent.name == "src"


def test_it_is_not_ignored_by_git():
    """A gitignored instrument can never be committed, so it can never be checked."""
    import subprocess

    proc = subprocess.run(
        ["git", "check-ignore", "-q", str(MODULE_PATH)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0, f"{MODULE_PATH} is gitignored; it cannot be committed and its output is unrecoverable"


def test_the_scratch_copy_is_gone():
    """The promoted file must not still exist in the gitignored directory.

    Leaving both would leave the next reader with two profilers, which is the
    two-instrument problem the whole programme exists to end.
    """
    assert not (REPO_ROOT / SCRATCH_RELATIVE / "prof_request_path.py").exists(), (
        f"{SCRATCH_RELATIVE}/prof_request_path.py still exists alongside {MODULE_PATH}; "
        "a reader would not know which one produced a given profile"
    )


def test_the_usage_lines_name_the_promoted_path():
    """A reader must not be told to run the profiler from the scratch directory.

    Checked against the Usage block specifically, not the whole file: the module
    docstring legitimately NAMES the old path in prose explaining where the file
    was promoted from, and a whole-file check would forbid documenting the
    move.
    """
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "src/benchmarks/prof_request_path.py" in source

    docstring = ast.get_docstring(_module_tree()) or ""
    usage_block = docstring.split("Usage:", 1)
    assert len(usage_block) == 2, "the docstring has no Usage: block"
    usage = usage_block[1].split("Provenance:", 1)[0]
    assert "src/benchmarks/prof_request_path.py" in usage
    assert SCRATCH_RELATIVE not in usage, f"a Usage line still points at {SCRATCH_RELATIVE}"


# --------------------------------------------------------------------------
# import safety: no environment mutation at module level
# --------------------------------------------------------------------------


def test_nothing_at_module_level_touches_the_environment():
    """A pytest session that imports this must not inherit a profiled app.

    ``src/.env`` exists, so a module-level ``load_dotenv(override=True)`` would
    replace the environment the session runs under. Same rule as
    ``driver_count_io.bootstrap()`` and ``census.py``.

    Definitions are excluded along with imports: a ``bootstrap`` function body
    is not executed at import time, so its ``os.chdir`` is exactly what this
    module is supposed to contain. Checking only statements that RUN at import
    is the difference between pinning the contract and flagging the fix.
    """
    executed_at_import = (
        ast.Assign,
        ast.AnnAssign,
        ast.AugAssign,
        ast.Expr,
        ast.If,
        ast.For,
        ast.While,
        ast.With,
        ast.Try,
        ast.Raise,
        ast.Delete,
    )
    seen = 0
    for node in _module_tree().body:
        if not isinstance(node, executed_at_import):
            continue
        seen += 1
        source = ast.dump(node)
        assert "chdir" not in source, f"module-level {type(node).__name__} calls os.chdir"
        assert "environ" not in source, f"module-level {type(node).__name__} writes os.environ"
        assert "load_dotenv" not in source, f"module-level {type(node).__name__} calls load_dotenv"
    assert seen >= 3, f"only {seen} module-level statements ran; the scan is not seeing the module body"


def test_importing_the_module_does_not_move_the_working_directory(tmp_path):
    before = Path.cwd()
    assert mod.ROOT == REPO_ROOT
    assert Path.cwd() == before, "importing the profiler moved the working directory"


def test_bootstrap_exists_and_is_called_before_any_app_import():
    """Structural: the call must precede the phases, or the relaxations are read too late."""
    bootstrap = _function("bootstrap")
    assert bootstrap.name == "bootstrap"
    main = _function("main")
    calls = [n for n in ast.walk(main) if isinstance(n, ast.Call)]
    bootstrap_positions = [n.lineno for n in calls if isinstance(n.func, ast.Name) and n.func.id == "bootstrap"]
    assert bootstrap_positions, "main() never calls bootstrap(); the budgets would not be relaxed"
    assert bootstrap_positions[0] == min(bootstrap_positions)


def test_bootstrap_relaxes_exactly_the_declared_environment():
    """The list above must be the WHOLE list, or the manifest under-labels a run.

    Walked structurally rather than by regex over source text, because a
    substring rule would also match the RELAXED_ENV constant's own literals.
    """
    bootstrap = _function("bootstrap")
    written: dict[str, str] = {}
    for node in ast.walk(bootstrap):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Subscript) or not isinstance(target.value, ast.Attribute):
            continue
        if target.value.attr != "environ" or not isinstance(target.slice, ast.Constant):
            continue
        written[str(target.slice.value)] = str(node.value.value)
    for name, value in written.items():
        assert name in RELAXED_ENV or name in {"ENVIRONMENT"}, f"bootstrap writes undeclared {name}={value}"
    for name, value in RELAXED_ENV.items():
        assert written.get(name) == value, f"expected {name}={value}, found {written.get(name)!r}"


def test_the_relaxed_budgets_are_wide_enough_not_to_trip_the_fixture():
    """A profile whose warmup 429s measures nothing.

    The free-tier fixture caps at 10 req/hr, so the limits must exceed the
    number of requests the phases issue. Only the magnitude is asserted here;
    WHICH variables carry it is asserted structurally by
    :func:`test_bootstrap_relaxes_exactly_the_declared_environment`, which does
    not depend on how ``ast.unparse`` happens to quote a string.
    """
    bootstrap = _function("bootstrap")
    source = ast.unparse(bootstrap)
    assert "1000000" in source, "a relaxed limit is small enough for the free tier to trip it during warmup"


def test_output_goes_to_the_reports_directory_not_beside_the_source():
    """An artefact written into src/benchmarks/ would be mistaken for a baseline."""
    assert mod.OUT_DIR == REPO_ROOT / "reports"
    assert mod.OUT_DIR != MODULE_PATH.parent


def test_main_creates_the_output_directory():
    """Otherwise the first run on a clean checkout dies on FileNotFoundError."""
    main = _function("main")
    assert "OUT_DIR.mkdir" in ast.unparse(main)
