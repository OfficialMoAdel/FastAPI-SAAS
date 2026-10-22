"""Reject log templates with a dangling placeholder slot.

The defect this gates: a structlog event message that contains a slot
where a value was meant to be interpolated — a trailing colon with
nothing after it (``"Failed to upload file :"``), a doubled space where
a value should be (``"Uploaded file: , size: bytes"``), or empty quotes
where a value should be (``"Preloaded script ''"``).

Why it matters even though the report called it "purely cosmetic":
the message is the operator's first line of triage. A template that
renders as ``"Failed to upload file :"`` reads as truncated output, and
the structured fields that carry the actual values are off to the right
in a key-value renderer. The log looks broken precisely when it is
reporting a failure.

The tempting fix — replacing the slot with a ``{name}`` placeholder — is
WRONG for this repo, and the gate exists partly to stop someone from
making that change. structlog does not interpolate ``{}`` from keyword
arguments: ``logger.info("Uploaded file: {path}", path=p)`` renders the
literal text ``Uploaded file: {path}`` and then ``path=p`` as a separate
field. Verified against this repo's configured processor chain
(``src/app/core/logger.py``): the ``{}`` survives into the rendered
output. The correct fix is to drop the dangling slot and let the
structured keyword argument carry the value, which is what the
remediation did.

The check is textual and deliberately narrow, because a broad "message
contains a colon" rule would flag the many legitimate messages that end
in a colon as a label prefix (``"Deleted file:"`` is fine — nothing is
missing after it, and the repo uses that shape in ~14 places). The shapes
that are always defects are:

1. a colon preceded by a space and followed by the end of the string
   (``"Failed to upload file :"``);
2. a colon followed by a comma, or by a space and then a comma
   (``"Uploaded file: , size: bytes"``);
3. empty double quotes inside the message (``"Preloaded script ''"``).

Rule 1 catches the majority of the reported sites — a space before the
colon is the fingerprint of a value that was removed from the template
and left the spacing behind. Rule 2 catches the emptied slot between
two labels. Rule 3 catches the empty-quotes variant.

A bare trailing colon with no space before it (``"Deleted file:"``) is
NOT gated. It is a label prefix rather than a dangling slot, and the
repo uses it widely. The one reported site with that shape
(``lua_manager.py``'s "Script failed, using fallback:") was fixed in the
same sweep for consistency, but the gate leaves that shape alone
because gating it would have demanded unrelated edits to a dozen
out-of-scope modules.

Usage:
    uv run python scripts/checks/log_templates.py
    uv run python scripts/checks/log_templates.py --verbose

Exit codes:
    0 - no dangling placeholder slot found
    1 - one or more log templates have a dangling placeholder slot
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

# Repo root = two levels up (scripts/checks/<gate>.py)
ROOT = Path(__file__).resolve().parents[2]

PROD_ROOTS = ("src",)

IGNORED_DIRS = frozenset({"__pycache__", ".venv", "node_modules", ".git", "logs"})

#: The structlog methods whose first positional argument is the event
#: message. Only these are inspected — a ``logger.exception(...)`` with a
#: non-message first argument is not a thing, but ``logger.log`` and
#: ``logger.msg`` are not in this list because the repo does not use them.
LOG_METHODS = frozenset({"info", "debug", "warning", "error", "critical", "exception"})


@dataclass(frozen=True)
class Offense:
    """One malformed log template."""

    path: Path
    line: int
    message: str
    reason: str

    def render(self, root: Path) -> str:
        try:
            location = str(self.path.relative_to(root))
        except ValueError:
            location = str(self.path)
        return f"  {location}:{self.line}: {self.message}  ({self.reason})"


def _message_reason(message: str) -> str | None:
    """Return why ``message`` is malformed, or None when it is fine."""
    if "''" in message:
        return "empty quotes where a value belongs"
    # A space before the colon: the fingerprint of a value that was
    # removed from the template and left the spacing behind.
    if message.endswith(" :"):
        return "space before a trailing colon - an emptied value slot"
    # A colon followed by a comma: the value slot was emptied.
    if ": ," in message or ", :" in message:
        return "empty value slot after a colon"
    return None


def _iter_calls(tree: ast.AST) -> list[ast.Call]:
    """Yield every call node in the tree."""
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def _call_receiver(call: ast.Call) -> str | None:
    """Return the attribute name of a call whose func is ``<something>.<name>``."""
    if not isinstance(call.func, ast.Attribute):
        return None
    return call.func.attr


def check_file(path: Path) -> list[Offense]:
    """Return the malformed log templates in one file."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    offenses: list[Offense] = []
    for call in _iter_calls(tree):
        if _call_receiver(call) not in LOG_METHODS:
            continue
        if not call.args:
            continue
        first = call.args[0]
        # f-strings interpolate at runtime, so the template cannot be
        # judged textually; an f-string is never a dangling-slot defect
        # because its placeholders are filled by the expression.
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue
        reason = _message_reason(first.value)
        if reason is None:
            continue
        offenses.append(
            Offense(
                path=path,
                line=first.lineno,
                message=first.value,
                reason=reason,
            )
        )
    return offenses


def _iter_python_files(root: Path) -> list[Path]:
    """Yield every Python file under the production roots."""
    for prod_root in PROD_ROOTS:
        base = root / prod_root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if any(part in IGNORED_DIRS for part in path.parts):
                continue
            yield path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="also report the scanned file count and the remediation")
    args = parser.parse_args(argv)

    files = sorted(_iter_python_files(ROOT))
    offenses: list[Offense] = []
    for path in files:
        offenses.extend(check_file(path))

    if not offenses:
        print("OK: no dangling log-template placeholder found.")
        if args.verbose:
            print(f"Scanned {len(files)} Python files under {', '.join(PROD_ROOTS)}.")
        return 0

    print(f"FAIL: {len(offenses)} log template(s) have a dangling placeholder slot:")
    for offense in offenses:
        print(offense.render(ROOT))
    print(
        "\nFix: drop the dangling slot and let the structured keyword argument\n"
        'carry the value - e.g. logger.error("Failed to upload file", path=path,\n'
        "error=e). Do NOT replace the slot with a {placeholder}: structlog does\n"
        "not interpolate braces from keyword arguments, so the braces would be\n"
        "rendered literally."
    )
    if args.verbose:
        print(f"\nScanned {len(files)} Python files under {', '.join(PROD_ROOTS)}.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
