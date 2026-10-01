#!/usr/bin/env python
"""Fail when AUTHORITATIVE documentation points at a `src/`, `tests/`, `scripts/`,
`docs/` or `.github/` path that does not exist.

A documented path is a CLAIM that the path exists. This script makes that claim
enforced instead of aspirational: a doc that names a file nobody can find costs
the next reader real time and trains them to distrust the docs.

SCOPE — WHAT COUNTS AS A CLAIM
Only the documents the project holds authoritative are scanned (see
``_authoritative_docs``): the root `AGENTS.md` / `ARCHITECTURE.md` / `CLAUDE.md`,
every `AGENTS.md` under `src/`, and `docs/adr/`. Those are the files a reader
follows a path from and the ones whose accuracy the root `AGENTS.md` promises.

Deliberately NOT scanned: ADJRNAL prose, benchmark reports, tutorial walkthroughs
and historical design notes (`docs/specs/`, `docs/user-guide/`, `docs/structure/`,
`tests/**/README.md`, `*_SUMMARY_*.md`, generated `graphify-out/`). A path in
those is often ILLUSTRATIVE rather than a claim — `src/app/models/product.py` in
an admin-panel tutorial is a worked example, and `scripts/quick-rollback.sh` in
a rollback runbook records a tool that existed when the note was written. Failing
those would demand the note be rewritten to agree with today's tree, which
destroys the note's value. They are excluded by DESIGN, and that exclusion is
stated here so it stays a decision rather than an accident.

Every deliberate exception within scope lives in ALLOWLIST with a reason. A
stale allowlist entry is itself a failure, so retiring the debt also retires the
entry.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Directories whose rooted paths are treated as claims. Anything else is prose.
SCANNED_ROOTS = ("src", "tests", "scripts", "docs", ".github")

#: Root-level documents the project holds authoritative.
ROOT_DOCS = ("AGENTS.md", "ARCHITECTURE.md", "CLAUDE.md")

#: Documentation subtrees that are authoritative (see module docstring).
AUTHORITATIVE_DIRS = ("docs/adr",)

#: Extensions that are documentation and therefore carry pointers.
DOC_SUFFIXES = (".md",)

#: Rooted path, followed by a segment, ending in a word char or a slash.
#: The trailing boundary is what keeps `src/app.py.bak` and `` `src/app` `` (a
#: module reference, not a path) from being read as claims.
_PATH_RE = re.compile(
    r"(?<![\w./-])("
    + "|".join(re.escape(root) for root in SCANNED_ROOTS)
    + r")((?:/[\w.@+-]+)+/?)",
)

#: Deliberately-dangling pointers, each with the reason it is allowed.
#: Every entry here is PRE-EXISTING rot found when the gate was introduced. A
#: stale entry fails the gate, so retiring the debt also retires the entry —
#: the ALLOWLIST is a ratchet, not a parking lot.
ALLOWLIST: dict[str, str] = {
    # --- ADRs naming a design that was decided AGAINST, or a module that has
    # since been refactored away. The reference is the record of the decision,
    # not a pointer a reader should follow.
    "src/app/core/service_registry.py": (
        "ADR-0001 records the REJECTED single-dependency-registry design. The file was never created; "
        "the ADR's value is the reasoning, not the path."
    ),
    "src/app/core/feature_flags.py": (
        "ADR-0002 names the pre-refactor flag module. Feature flags now live in "
        "src/app/modules/feature_flags/; the ADR predates the move."
    ),
    "src/app/core/config.py": (
        "Named as the config module before it became a PACKAGE (src/app/core/config/). "
        "A reference to the old single-file shape."
    ),
    "docs/adr/0001": (
        "Prose cross-reference to a sibling ADR by prefix, not a path claim."
    ),
    # --- A module with no crud.py. The module's registry is declared on the
    # model/service rather than in a repository module.
    "src/app/modules/api_keys/crud.py": "The api_keys module has no crud.py; its queries live in service.py.",
    "src/app/modules/posts/crud.py": "The posts module has no crud.py; its queries live in service.py.",
    # --- Tests that were renamed or consolidated into a per-module suite and
    # whose original file no longer exists under any name.
    "tests/test_audit.py": "Consolidated into tests/unit/modules/audit_logs/.",
    "tests/test_audit_decorator.py": "Consolidated into tests/unit/modules/audit_logs/.",
    "tests/test_webhook_delivery.py": "Consolidated into tests/unit/modules/webhooks/.",
    "tests/modules/test_users.py": (
        "There is no tests/modules/ tree; suites live under tests/unit, tests/integration or tests/contract."
    ),
    # --- Scripts that were renamed or never existed under this name.
    "scripts/create_first_tenant.py": "Tenant bootstrap is done through the API/admin panel, not a script.",
    # --- A migration filename recorded at a revision that has been squashed.
    "src/migrations/versions/2d75ef82a66a_add_notification_preferences_to_user_.py": (
        "Squashed into a later revision; the original per-revision filename is recorded for history only."
    ),
    "src/migrations/versions/README.MD": "No README in the versions directory; referenced case-sensitively by mistake.",
}


def _is_claim(candidate: str) -> bool:
    """True when ``candidate`` names a real file/dir claim rather than prose.

    ``src/app`` is a module reference in prose; ``src/app/core`` is a path. The
    distinguishing feature is the extension: a claim almost always carries one.
    A directory path (trailing slash, or an intermediate segment) also counts,
    because ``scripts/e2e/`` is a claim about a directory existing.

    An EXTENSIONLESS path whose ``.py`` sibling exists is a MODULE reference
    (``src/app/shared/exceptions`` meaning the module, not a file), so it is
    prose rather than a claim. Without this, every ``from src.x import y``
    narrative in an AGENTS.md reads as a broken pointer.
    """
    tail = candidate.rstrip("/").rsplit("/", 1)[-1]
    if "." in tail:
        return True
    if (REPO_ROOT / (candidate + ".py")).exists():
        return False  # module reference, not a path claim
    # No extension: treat as a directory claim only when the original had more
    # than one segment or an explicit trailing slash.
    return candidate.count("/") > 1 or candidate.endswith("/")


def _scan(text: str) -> set[str]:
    """Every rooted path claim in ``text``, normalised to repo-relative form."""
    found: set[str] = set()
    for match in _PATH_RE.finditer(text):
        root = match.group(1)
        rest = match.group(2)
        candidate = root + rest
        if not _is_claim(candidate):
            continue
        # Strip a trailing markdown/quote punctuation the regex may have eaten.
        found.add(candidate.rstrip(".,;:)"))
    return found


def _authoritative_docs() -> list[Path]:
    """Every document whose rooted paths are enforced claims.

    Root guidance docs, every ``AGENTS.md`` under ``src/`` (the JIT index a
    coding agent navigates by), and the ADRs (which name the code they govern).
    Everything else is journal prose or an illustrative example.
    """
    docs: list[Path] = [REPO_ROOT / name for name in ROOT_DOCS]
    docs.extend(p for p in REPO_ROOT.rglob("AGENTS.md") if ".venv" not in p.parts and "__pycache__" not in p.parts)
    for rel in AUTHORITATIVE_DIRS:
        docs.extend(sorted((REPO_ROOT / rel).rglob("*.md")))
    return sorted({p for p in docs if p.is_file()})


def main() -> int:
    missing: list[tuple[str, str]] = []

    for path in _authoritative_docs():
        rel_doc = path.relative_to(REPO_ROOT)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for claim in sorted(_scan(text)):
            if claim in ALLOWLIST:
                continue
            if not (REPO_ROOT / claim).exists():
                missing.append((str(rel_doc), claim))

    # An ALLOWLIST entry goes STALE in the one direction that matters: the path
    # it exempts now EXISTS, so the exemption is exempting nothing and is
    # hiding a real pointer behind a dead reason. (The opposite — a path that
    # does NOT exist — is the entire point of the entry.)
    stale = [c for c in ALLOWLIST if (REPO_ROOT / c).exists()]
    for claim in sorted(stale):
        print(f"STALE ALLOWLIST entry: {claim} now exists — remove the exemption", file=sys.stderr)

    if missing:
        print("Documented paths that do not exist:", file=sys.stderr)
        for doc, claim in missing:
            print(f"  {doc} -> {claim}", file=sys.stderr)
        print(
            "\nFix the pointer, or add a reasoned ALLOWLIST entry in "
            "scripts/check_doc_paths.py.",
            file=sys.stderr,
        )
        return 1

    if stale:
        print("Stale ALLOWLIST entries: see above", file=sys.stderr)
        return 1
    print(f"check_doc_paths: no dangling documented paths in {len(_authoritative_docs())} authoritative docs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
