"""Count blind broad ``except`` handlers in ``src/`` — the metric the ratchet is built on.

The blind-except campaign (muse 22) works one site at a time, and the failure
mode of that work is drift: a broad handler added "just to be safe", a triage
that annotates instead of narrowing, an exception swallowed on a new code path.
None of those fail a test on their own, so without a number they accumulate
silently and the campaign stalls with no visible way to tell it is stalled.

Two numbers are reported because they mean different things:

* ``total`` — every ``except Exception``/``except BaseException`` handler in
  ``src/``. This is what the ratchet ceiling applies to.
* ``annotated`` — the subset carrying ``# noqa: BLE001`` with a reason. A new
  broad except that *declares itself deliberate* is an improvement over an
  undeclared one, so it is tracked separately rather than folded in: folding
  it in would let a campaign that annotates everything report progress while
  narrowing nothing.

The detector parses with ``ast`` rather than grepping text, so a broad except
in a comment, a docstring, or a string literal is not counted. An earlier
grep-based count put the total at 358 against 357 real handlers; the
docstrings explaining these decisions are exactly the text that inflated it.

Run directly to print the current counts:

    python scripts/count_broad_excepts.py [--json]
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

#: The two broad types BLE001 flags. ``Exception`` is the common case;
#: ``BaseException`` is broader still (it would also catch CancelledError and
#: KeyboardInterrupt) and is counted here so that widening to it cannot
#: quietly ratchet upward.
BROAD_TYPES = frozenset({"Exception", "BaseException"})

@dataclass(frozen=True, slots=True)
class BroadExceptFinding:
    path: str
    lineno: int
    kind: str
    annotated: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "lineno": self.lineno,
            "kind": self.kind,
            "annotated": self.annotated,
        }


def is_broad(handler: ast.ExceptHandler) -> str | None:
    """Return the broad type name when this handler catches one, else None."""
    if handler.type is None:
        return None
    if isinstance(handler.type, ast.Name) and handler.type.id in BROAD_TYPES:
        return handler.type.id
    return None


def _all_handlers(src_root: Path = SRC) -> list[ast.ExceptHandler]:
    """Every except handler under ``src_root``, broad, narrow, or bare."""
    handlers: list[ast.ExceptHandler] = []
    for path in sorted(Path(src_root).rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        handlers.extend(n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler))
    return handlers


def bare_except_count(src_root: Path = SRC) -> int:
    """Count bare ``except:`` handlers — which also catch KeyboardInterrupt.

    Reported separately from the broad-typed count because it is a strictly
    worse version of the same problem: ``except Exception`` at least names a
    type, so a rule can flag it, whereas a bare ``except:`` is invisible to
    any detector that looks at the named type, and the broad total would not
    move at all if one were introduced.
    """
    return sum(1 for h in _all_handlers(src_root) if h.type is None)


def scan(src_root: Path = SRC) -> list[BroadExceptFinding]:
    """Every broad ``except`` handler under ``src_root``."""
    findings: list[BroadExceptFinding] = []
    for path in sorted(Path(src_root).rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except (SyntaxError, UnicodeDecodeError):
            # A file that does not parse is not counted. Reporting it as zero
            # handlers would let a syntax error look like progress.
            continue
        lines = text.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            kind = is_broad(node)
            if kind is None:
                continue
            head = lines[node.lineno - 1] if node.lineno - 1 < len(lines) else ""
            findings.append(
                BroadExceptFinding(
                    path=path.relative_to(REPO).as_posix(),
                    lineno=node.lineno,
                    kind=kind,
                    annotated="noqa: BLE001" in head,
                )
            )
    return findings


def count(src_root: Path = SRC) -> int:
    return len(scan(src_root))


def annotated_count(src_root: Path = SRC) -> int:
    return sum(1 for f in scan(src_root) if f.annotated)


if __name__ == "__main__":
    found = scan()
    if "--json" in sys.argv:
        print(
            json.dumps(
                {
                    "total": len(found),
                    "annotated": sum(1 for f in found if f.annotated),
                    "findings": [f.as_dict() for f in found],
                },
                indent=2,
            )
        )
    else:
        for f in found:
            mark = "annotated" if f.annotated else "UNANNOTATED"
            print(f"{f.path}:{f.lineno}: {f.kind} ({mark})")
        print(f"\nbroad except total: {len(found)}")
        print(f"broad except annotated: {sum(1 for f in found if f.annotated)}")
