"""Reject production comments whose only content is a test grep.

The defect this gates: a comment written, reworded, or kept in place
*because* some test greps production source for a literal. Such a comment
carries no engineering information — it describes the check instead of the
code — and it is a landmine, because the next person to reword the message
or tighten the regex breaks a test they cannot see the reason for.

A comment may legitimately NAME the test that enforces it. "Pinned by
tests/unit/test_middleware_order.py" tells the next reader where the
guarantee comes from, and deleting it loses real information. That is a
pointer to an enforcer, not grease. This script fails only the stronger
case: the comment's reason for existing IS the grep.

The gate is therefore textual, because that is the only observable
difference. The offending shapes are the ones that state the comment's
own purpose in terms of a scanner:

    # Kept as a named constant so grep still resolves.
    # Do not reword this — the test greps for it.
    # This is worded to avoid matching the forbidden-pattern scan.
    # ... so the source-text test does not flag it.

A comment that explains the CODE and *also* mentions a test is untouched.
So is a comment that mentions grep as a tool a human would run
("run grep to find the callers") — the script requires the grep clause to
be self-referential, i.e. about the comment's own text or visibility.

AST-based on purpose: comments are tokens, not code, so every hit is
collected via ``tokenize`` and a comment inside an f-string, a docstring,
or a dead branch is treated the same as a module-level one. Docstrings
are explicitly excluded, because a docstring is API documentation and the
"comment" exemption below is about prose next to code.

Usage:
    uv run python scripts/check_comment_grease.py
    uv run python scripts/check_comment_grease.py --verbose

Exit codes:
    0 - no comment-grease found
    1 - one or more comments exist only to steer a grep
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

# Repo root = parent of scripts/
ROOT = Path(__file__).resolve().parent.parent

# Production code only. Tests are allowed to talk about grep - a test that
# asserts on source text is the whole point of the ratchet, and its
# comments legitimately explain what it greps for. Scaffolding the repo
# ships for others to copy (src/scripts/create_module.py) is likewise not
# gated, so a generated module does not inherit the rule.
PROD_ROOTS = ("src",)

IGNORED_DIRS = frozenset({"__pycache__", ".venv", "node_modules", ".git"})

#: Words that mark a comment as talking about a text-matching CHECK rather
#: than about code. "the lint" alone is too weak - production comments say
#: "avoid a lint cycle" about a circular import - so the bare nouns are
#: paired with a scanner verb by the patterns below.
SCANNER_WORDS = (
    r"(?:test|pytest|grep|scan|scanner|ratchet|regex|pattern|assert(?:ion)?"
    r"|arch(?:itecture)?[- ]test|contract[- ]test|static[- ]analys[ei]s)"
)

#: A comment is grease only when it is about a text-matching CHECK whose
#: input is the comment itself. See the two-halves note below the rules.
# A grease comment needs BOTH halves to be present in the same comment:
#
#   1. a SCANNER word - the comment is about a text-matching check; and
#   2. a SELF-REFERENTIAL shape - the comment's own text or visibility is
#      what that check consumes.
#
# Requiring both is what keeps this gate honest. "An unwired process would
# fail on the first permission lookup" contains a would-fail shape and no
# scanner at all - it describes the CODE, and the pattern below never sees
# it. Equally, "must not match" appears in regex explanations about input
# validation, where the thing that must not match is user data, not a
# comment. Anchoring each rule to SCANNER_WORDS is what separates the two.
#
# Both halves are also required to be in the SAME comment rather than merely
# in the same file, because a multi-line block is often a legitimate
# explanation that happens to end by naming its test.
SCANNER = re.compile(SCANNER_WORDS, re.IGNORECASE)

SELF_REFERENTIAL = re.compile(
    r"(?i)\b(?:"
    r"this\s+(?:comment|line|string|literal|word(?:ing)?|phras(?:e|ing)|text)"
    r"|comments?\s+(?:like|such\s+as)\s+(?:this|these)"
    r"|(?:so|to\s+keep)\s+(?:the\s+|this\s+)?(?:test|pytest|grep|scan|checker|assertion|ratchet)"
    r"|(?:avoid|prevent|defeat|stop|sidestep|suppress|satisf(?:y|ies))\w*\s+"
    r"(?:a\s+|the\s+|any\s+)?(?:test|pytest|grep|scan|checker|assertion|regex|pattern|match|false\s+positive)"
    r"|(?:do\s+not|don'?t|never)\s+(?:reword|rewrite|rephrase|rename|remove)"
    r"|(?:kept|leave|leave\s+it|stay|keep)\s+(?:this|it)\b"
    r"|(?:comment|comments)\b"
    r")\b"
)

#: The comment is present so a human grep can still find the code. Distinct
#: from SELF_REFERENTIAL because it makes a claim about the CODE ("history
#: still resolves"), which is a real, if weak, rationale - and because it
#: is the shape most likely to be a genuine finding rather than a false
#: positive. Flagged at a lower severity so an allowlist entry can record a
#: reason without pretending it is identical to the strong case.
GREP_RESOLUTION = re.compile(
    r"(?i)\b(?:"
    r"(?:so|so\s+that)\s+(?:a\s+|the\s+|someone\s+|future\s+)?(?:grep|search|ripgrep|rg)\b"
    r"|(?:grep|search)\s+still\s+(?:resolves?|finds?|works|turns\s+up)"
    r"|(?:kept|keep|leave)\s+(?:as\s+a\s+\w+\s+)?(?:so|for)\s+(?:grep|search|ripgrep|history)"
    r"|history\s*/\s*grep"
    r")\b"
)

#: A comment that says grep is a tool a reader may use. Legitimate, and
#: must not be flagged even though it contains "grep".
HUMAN_GREP = re.compile(
    r"(?i)\b(?:"
    r"(?:run|use|try)\s+grep"
    r"|grep\s+(?:-r|-n|-i|-e|--)\w*"
    r"|ripgrep\b"
    r"|(?:you\s+can|to\s+find)\s+grep"
    r")\b"
)

#: Severity per rule. The strong shape is an outright failure; the weak one
#: is reported so a maintainer must consciously say it is fine.
STRONG = "grep-grease"
WEAK = "grep-resolution-only"

RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (STRONG, SELF_REFERENTIAL, "comment states it exists to satisfy a text scan"),
    (WEAK, GREP_RESOLUTION, "comment justifies itself by grep-ability alone"),
)

#: Comments the rules match that are NOT grease, each with the reason it
#: stays. Mirrors the ALLOWLIST convention of ``check_doc_paths.py``: a
#: reviewer cannot wave a finding through by muting it, they have to say
#: why. Removing an entry turns the comment back into a CI failure, so
#: the decision to keep it is explicit and traceable in review.
_SG = "src/app/modules/users/session_invalidation.py"
ALLOWLIST: dict[tuple[str, int], str] = {
    # Names a test because the seam's whole purpose is that HTTP tests
    # observe the same metrics production does; "metrics are observed" is
    # the real claim, and the test is the instrument, not the subject.
    (_SG, 419): "documents the real requirement (metrics observable via UserService)",
    # A retired key kept so a historical search still lands here. It makes a
    # claim about the CODE's history, which is why it is allow-listed rather
    # than rewritten - but it is the weakest form of the habit, so it is
    # recorded here where a future reader can see it and reconsider.
    ("src/app/core/events/bus.py", 51): "retired key kept discoverable; asserts about code history",
}

@dataclass(frozen=True, order=True)
class Finding:
    """One comment that exists only to steer a grep."""

    path: str
    lineno: int
    rule: str
    text: str

    def format(self) -> str:
        return f"{self.path}:{self.lineno}: [{self.rule}] {self.text}"


def comment_tokens(source: str) -> list[tokenize.TokenInfo]:
    """Every COMMENT token in ``source`` (docstrings excluded).

    A string statement is a docstring, which is API documentation rather
    than a comment next to code, so it is filtered out. Comments inside
    f-strings and expressions survive, which is what a tokenizer gives us
    for free.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (SyntaxError, tokenize.TokenError):
        return []

    docstring_spans: set[tuple[int, int]] = set()
    parsed = _safe_parse(source)
    if parsed is not None:
        for node in ast_walk_docstrings(parsed):
            docstring_spans.add((node[0], node[1]))

    out: list[tokenize.TokenInfo] = []
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        if any(start <= token.start[0] <= end for start, end in docstring_spans):
            continue
        out.append(token)
    return out


def _safe_parse(source: str):
    try:
        import ast

        return ast.parse(source)
    except SyntaxError:
        return None


def ast_walk_docstrings(parsed):
    """Yield (first_line, last_line) for every docstring in the module."""
    import ast

    for node in ast.walk(parsed):
        if not isinstance(node, ast.Expr | ast.AsyncFunctionDef | ast.FunctionDef | ast.ClassDef | ast.Module):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue
        if not hasattr(first, "end_lineno") or first.end_lineno is None:
            continue
        yield (first.lineno, first.end_lineno)


def classify(text: str) -> tuple[str, str] | None:
    """Return ``(rule, reason)`` when ``text`` is grep-grease, else None.

    Both halves must be present: the comment must mention a scanner, and
    must be self-referential about it. Either alone is ordinary prose -
    "unwired process would fail" is about the code, and a comment may name
    the test that pins a real invariant.
    """
    body = text.lstrip("#").strip()
    if not body:
        return None
    if not SCANNER.search(body):
        return None
    if HUMAN_GREP.search(body):
        return None
    for name, pattern, reason in RULES:
        if pattern.search(body):
            return (name, reason)
    return None


def check_file(path: Path, root: Path) -> list[tuple[Finding, str | None]]:
    """Return ``(finding, allowlist_reason)`` pairs for one production file.

    An allow-listed comment is still returned, tagged with its reason, so
    the caller can distinguish "no grease" from "grease that was
    consciously accepted" and so a stale entry is visible rather than
    invisible.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    rel = _display_path(path, root)
    results: list[tuple[Finding, str | None]] = []
    for token in comment_tokens(source):
        hit = classify(token.string)
        if hit is None:
            continue
        name, _reason = hit
        finding = Finding(
            path=rel,
            lineno=token.start[0],
            rule=name,
            text=token.string.strip()[:150],
        )
        results.append((finding, ALLOWLIST.get((rel, token.start[0]))))
    return results


def _display_path(path: Path, root: Path) -> str:
    """Repo-relative when possible, else the path as given.

    The script accepts explicit file arguments, which may sit outside the
    repo (a scratch file under review, an odd CI checkout path).
    ``relative_to`` raises there, so fall back to the literal path rather
    than crashing on a file the user explicitly asked to check.
    """
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def iter_prod_files(roots: tuple[str, ...] = PROD_ROOTS, root: Path = ROOT):
    """Yield every production ``.py`` file under ``roots``."""
    for rel in roots:
        base = root / rel
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if IGNORED_DIRS.intersection(path.parts):
                continue
            yield path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("paths", nargs="*", help="files or dirs to check (default: src/)")
    parser.add_argument("--verbose", action="store_true", help="also list weak-rule findings")
    args = parser.parse_args(argv)

    targets: list[Path] = []
    if args.paths:
        for raw in args.paths:
            p = Path(raw)
            targets.extend(sorted(p.rglob("*.py")) if p.is_dir() else [p])
    else:
        targets = list(iter_prod_files())

    strong: list[Finding] = []
    weak: list[Finding] = []
    accepted: list[tuple[Finding, str]] = []
    for path in targets:
        if not path.is_file() or path.suffix != ".py":
            continue
        for finding, reason in check_file(path, ROOT):
            if reason is not None:
                accepted.append((finding, reason))
            elif finding.rule == STRONG:
                strong.append(finding)
            else:
                weak.append(finding)

    for finding, reason in accepted:
        print(f"allow-listed: {finding.format()}\n    reason: {reason}")
    if accepted:
        print()

    if not strong and not (weak and args.verbose):
        note = f" ({len(weak)} weak hit(s) hidden; --verbose)" if weak else ""
        print(f"OK: no comment-grease in {len(targets)} file(s){note}")
        return 0

    if strong:
        print(f"FAIL: {len(strong)} comment(s) exist only to steer a grep\n")
        for finding in strong:
            print(f"  {finding.format()}")
        print(
            "\nA comment must explain the CODE. Naming the test that pins it\n"
            "is fine; existing only so the grep matches is not."
        )
    if weak and args.verbose:
        print(f"\nweak: {len(weak)} comment(s) justify themselves by grep-ability\n")
        for finding in weak:
            print(f"  {finding.format()}")
    return 1 if strong else 0


if __name__ == "__main__":
    sys.exit(main())
