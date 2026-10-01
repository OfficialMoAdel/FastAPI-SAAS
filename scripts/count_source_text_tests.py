"""Count source-text test assertions — the metric the ratchet is built on.

A "source-text assertion" reads a test file's own source (or a source file's
text) and asserts a string or pattern appears in it. It passes when the text
matches regardless of whether the code works, so it is a placeholder that
looks like a test.

``is_source_text_assertion`` is deliberately conservative on two axes, because
a detector that over-counts gets disabled and teaches the same habit:

* only a read whose target is a ``.py`` file counts — reading a ``.json``
  fixture or a ``.txt`` asset is ordinary test setup, not source inspection;
* a test that ALSO exercises behaviour (calls the code, uses a client, makes
  a request) is a behavioural test with an extra assertion, and is not the
  defect. The behavioural check runs FIRST, so such a test is never counted
  even when it also calls ``inspect.getsource``.

Run directly to print the current count, which is what the ratchet compares:

    python scripts/count_source_text_tests.py [--json]
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TESTS = REPO / "tests"

#: Calls that mean a test actually exercises code rather than reading it.
BEHAVIOURAL_CALLS = frozenset(
    {
        "get", "post", "put", "patch", "delete", "request", "options", "head",
        "run", "gather", "wait_for", "sleep",
        "assert_called", "assert_awaited", "assert_called_once",
        "assert_any_call", "assert_not_called", "assert_awaited_once",
    }
)

SOURCE_READERS = frozenset({"read_text", "read_bytes", "read", "loads", "load"})


@dataclass(frozen=True, slots=True)
class SourceTextFinding:
    path: str
    lineno: int
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {"path": self.path, "lineno": self.lineno, "reason": self.reason}


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _mentions_python_file(node: ast.AST) -> bool:
    """True when a string constant in ``node`` names a .py file or glob."""
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            if child.value.endswith(".py") or ".py" in child.value:
                if any(ch in child.value for ch in "*?/"):
                    return True
                if child.value.endswith(".py"):
                    return True
        # Path("...") / sorted(root.rglob("*.py")) — the constant may be
        # nested, which the walk above already covers.
    return False


def _reads_python_source(func: ast.AST) -> bool:
    """True when a source-reader call is aimed at a .py file.

    Covers both ``Path("x.py").read_text()`` and ``root.rglob("*.py")`` loops:
    the reader and the ``.py`` filename may appear in different sub-expressions,
    so the test is "a source reader exists AND a .py path is mentioned".
    """
    has_reader = any(
        isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
        and child.func.attr in SOURCE_READERS
        for child in ast.walk(func)
    ) or any(
        isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
        and child.func.id in {"open"}
        for child in ast.walk(func)
    )
    if not has_reader:
        return False
    return _mentions_python_file(func)


def _test_functions(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
    ]


def is_source_text_assertion(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Return a reason string when ``func`` only asserts on .py source text.

    Returns None when the test exercises behaviour, even if it also reads
    source — that is a behavioural test with an extra assertion. The
    behavioural check runs first, so such a test is never miscounted.
    """
    calls = _called_names(func)
    if calls & BEHAVIOURAL_CALLS:
        return None
    if "getsource" in calls:
        return "asserts on inspect.getsource() output"
    if _reads_python_source(func):
        return "asserts on a .py source file's text"
    return None


def scan(tests_root: Path = TESTS) -> list[SourceTextFinding]:
    findings: list[SourceTextFinding] = []
    for path in sorted(Path(tests_root).rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for func in _test_functions(tree):
            reason = is_source_text_assertion(func)
            if reason:
                findings.append(
                    SourceTextFinding(
                        path=path.as_posix(),
                        lineno=func.lineno,
                        reason=reason,
                    )
                )
    return findings


def count(tests_root: Path = TESTS) -> int:
    return len(scan(tests_root))


def main() -> int:
    findings = scan()
    if "--json" in sys.argv:
        print(json.dumps([f.as_dict() for f in findings], indent=2))
    else:
        for f in findings:
            print(f"{f.path}:{f.lineno}: {f.reason}")
        print(f"\nsource-text test count: {len(findings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
