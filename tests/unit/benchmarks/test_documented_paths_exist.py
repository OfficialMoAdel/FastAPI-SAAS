"""A harness must not be documented at a path it does not have.

`FastAPI_saas-cje6` found that a published "Baseline-first" block invoked
`tests/benchmarks/capture_baseline.py`, which has never existed. The audit that
recorded this lives in an untracked, gitignored scratch report, so the claim was
unverifiable from the repo and the command stayed unrunnable indefinitely.

Resolution (b) was correct over (a): point readers at the real producer rather
than create a second baseline-capture path, because two capture paths are two
instruments, which is the exact problem this whole programme exists to end. The
correction itself had already landed in tracked docs (`RUNBOOK.md`,
`baselines/README.md`, `test_benchmarks.py` all name `scripts/capture_baseline.py`).
What had NOT landed is anything that keeps it true.

So this file is the part that was missing: an enforced invariant, not a
correction to one scratch report.

WHAT THE INVARIANT IS, AND WHAT IT DELIBERATELY IS NOT
The obvious generalisation - "every .py path in every tracked document must
exist" - was implemented first and is UNSATISFIABLE, so it is not here. It
reported several hundred misses that are correct documentation: how-to guides
name `src/app/models/product.py`, `src/app/api/v1/items.py`,
`src/app/modules/your_module/models.py` and `src/migrations/versions/20241215_1430_...py`,
all of which the reader is about to CREATE. A check that flags correct prose
gets switched off, and a switched-off check protects nothing.

That is also why `scripts/check_doc_paths.py` scopes itself to AGENTS.md and
ARCHITECTURE.md: those are claims about the repo as it is, whereas a tutorial is
an instruction for constructing something. So the rule here is anchored on a
specific, unambiguous subject instead - any path naming the `capture_baseline`
harness - which is what this bead is actually about.

What it found, immediately, in a TRACKED file rather than the scratch report:

    tests/e2e/static_analysis/capture_baseline.py
        uv run python tests/e2e/r1_opus_advanced/capture_baseline.py

The file lives in `tests/e2e/static_analysis/`; `tests/e2e/r1_opus_advanced/`
does not exist. It had been moved without its docstring following, which is the
same defect as the original finding and had been sitting in git the whole time.

The scan therefore covers tracked module DOCSTRINGS as well as tracked docs,
because that is where the live instance was. Two rules plus two direct checks;
all four failed at least once while this file was written.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Where tracked prose lives.
DOC_GLOBS = ("*.md", "*.rst", "*.txt")

#: The specific regression: any path naming this harness.
CAPTURE_BASELINE = re.compile(r"(?<![\w./])((?:[\w.-]+/)*capture_baseline\.py)")

#: The producers the docs are expected to name. Both must exist.
PRODUCERS = ("scripts/capture_baseline.py", "tests/e2e/static_analysis/capture_baseline.py")


def _git(*args: str) -> str | None:
    proc = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return None if proc.returncode != 0 else proc.stdout


def _tracked_files(*globs: str) -> list[Path]:
    """Every git-TRACKED file matching these globs.

    Tracked, not merely present: an untracked scratch report is precisely what
    made the original finding unverifiable, so scanning the working tree would
    re-introduce the problem this test exists to close.
    """
    out = _git("ls-files", *globs)
    if not out:
        return []
    return [REPO_ROOT / line for line in out.splitlines() if line]


def _tracked_docs() -> list[Path]:
    docs: set[Path] = set()
    for suffix in DOC_GLOBS:
        docs.update(_tracked_files(f"*{suffix}"))
    return sorted(docs)


def _tracked_docstrings() -> list[tuple[Path, str]]:
    """``(file, module docstring)`` for every tracked python file that has one.

    Module docstrings only: a nested docstring is documentation for a symbol
    inside the file and cannot claim a repo path the reader would try to run.
    """
    found: list[tuple[Path, str]] = []
    for path in _tracked_files("*.py"):
        try:
            docstring = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8", errors="replace")))
        except SyntaxError:
            # A tracked file that does not parse has its own problem, and a
            # repo-wide python syntax gate is not this test's job.
            continue
        if docstring:
            found.append((path, docstring))
    return found


def _sources() -> list[tuple[str, str]]:
    """Every place a reader could be told to run a harness: docs and docstrings."""
    sources = [
        (doc.relative_to(REPO_ROOT).as_posix(), doc.read_text(encoding="utf-8", errors="replace"))
        for doc in _tracked_docs()
    ]
    sources.extend((path.relative_to(REPO_ROOT).as_posix(), docstring) for path, docstring in _tracked_docstrings())
    return sources


def _missing_capture_baseline_refs() -> list[str]:
    missing: list[str] = []
    for where, text in _sources():
        for named in sorted(set(CAPTURE_BASELINE.findall(text))):
            if not (REPO_ROOT / named).exists():
                missing.append(f"{where} names {named}, which does not exist")
    return missing


def test_the_scan_finds_the_references_it_is_meant_to_find():
    """A rule that reads nothing passes every other test in this file."""
    sources = _sources()
    docs = [where for where, _ in sources if not where.endswith(".py")]
    docstrings = [where for where, _ in sources if where.endswith(".py")]
    assert len(docs) > 20, f"only {len(docs)} tracked docs found; the scan is not seeing the repo"
    assert "tests/performance/RUNBOOK.md" in docs
    assert "README.md" in docs
    assert len(docstrings) > 20, f"only {len(docstrings)} tracked module docstrings found"

    named = {named for _, text in sources for named in CAPTURE_BASELINE.findall(text)}
    assert named, "the pattern matched nothing anywhere, so every rule below is vacuous"


def test_the_producer_this_bead_pointed_at_exists():
    """Resolution (b) redirected readers here; assert it directly.

    The other rule only proves SOME producer is named and exists, so a rename
    could leave every doc dangling while that rule stayed satisfied.
    """
    for producer in PRODUCERS:
        assert (REPO_ROOT / producer).is_file(), f"{producer} is a producer the docs name, and it is missing"
        assert _git("ls-files", "--error-unmatch", producer) is not None, (
            f"{producer} is untracked, so it can never be committed"
        )


def test_tracked_documents_and_docstrings_never_name_a_capture_baseline_that_does_not_exist():
    """The invariant this bead exists for.

    The instance found while writing this file was in a tracked module
    docstring, which is why module docstrings are scanned and not just docs.
    """
    offenders = _missing_capture_baseline_refs()
    assert not offenders, "sources name a capture_baseline.py that does not exist:\n  " + "\n  ".join(offenders)


def test_the_repo_still_names_a_producer_that_exists():
    """So the rule above cannot be satisfied by deleting every reference.

    A repo naming no baseline-capture reference at all would pass that rule
    while having lost the runbook step, leaving the reader with nothing - the
    same outcome the bead set out to prevent.
    """
    named = {named for _, text in _sources() for named in CAPTURE_BASELINE.findall(text)}
    existing = sorted(n for n in named if (REPO_ROOT / n).exists())
    assert existing, (
        "no tracked source names an existing capture_baseline.py; they name "
        f"{sorted(named) or 'nothing at all'}. Either the runbook step was deleted, "
        "or every reference points at a file that is gone."
    )


def test_the_documented_invocation_of_the_producer_is_runnable():
    """`python scripts/capture_baseline.py --round 1` is what the docs promise.

    Asserted as a string in the source, not by executing it: running it would
    overwrite a baseline, which is exactly the side effect a check must not
    have.
    """
    source = (REPO_ROOT / "scripts" / "capture_baseline.py").read_text(encoding="utf-8")
    assert "--round" in source, "the documented --round flag is gone from the producer"
    assert "--output" in source, "the documented --output flag is gone from the producer"
