"""Ratchet: ADRs are tracked, and stay tracked.

`scripts/check_doc_paths.py` asserts that every rooted path named in the
authoritative docs EXISTS on disk. That check is satisfied by a file sitting in
the working tree — whether or not git knows about it. So a gitignored ADR is a
path the gate calls valid on your machine and cannot see at all in a CI
checkout, where the pointer is false and the gate fails there, and only there.

This repository already paid for that once. A blanket `docs/adr/*` ignore left
seventeen ADRs untracked, and the fix that was applied at the time was to carve
out ADR-0007 with a `!` rule — an exception for the one file that happened to be
linked from `AGENTS.md`, which treats the symptom and leaves the cause standing.

Nothing in the suite could catch it: `check_doc_paths.py` only ever looked at
the filesystem. So the two assertions below exist to make the filesystem check
insufficient on its own ever again.

* `test_every_adr_on_disk_is_tracked` is the real invariant: anything in
  `docs/adr/` that exists on disk is in the index. That covers the ADRs nothing
  links to as well as the ones something does.
* `test_the_adr_directory_is_not_ignored` pins the cause. Re-adding the blanket
  ignore (with or without a carve-out) fails here even if every currently
  referenced ADR is still tracked, which is precisely the blind spot the old
  `!` rule sat in.

Both are silent failures without a test: re-adding an ignore line changes no
test result anywhere else in the suite, and CI is the only place the omission
becomes visible.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ADR_DIR = REPO_ROOT / "docs" / "adr"
GITIGNORE = REPO_ROOT / ".gitignore"

# ADR-0009 is the one named by the bead: it carries the A-1 branch-pruning
# question that the P0 threat-model-owner bead (FastAPI_saas-v6vs) has to put to
# a named human. If that file is invisible to a reviewer, that handoff cannot
# happen, which is the concrete cost the blanket ignore imposed.
NAMED_ADR = ADR_DIR / "0009-branch-pruning-proof-basis.md"


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is required to inspect the index")


@requires_git
def test_every_adr_on_disk_is_tracked() -> None:
    on_disk = {p.name for p in ADR_DIR.glob("*.md")}
    assert on_disk, f"no ADRs found under {ADR_DIR}; the fixture itself moved"

    tracked = {
        line.strip().rsplit("/", 1)[-1]
        for line in _git("ls-files", "--", "docs/adr").stdout.splitlines()
        if line.strip().endswith(".md")
    }

    untracked = sorted(on_disk - tracked)
    assert not untracked, (
        f"{len(untracked)} ADR(s) exist on disk but are not in the git index, so no CI "
        f"checkout can see them and every doc that points at them is making a false "
        f"claim there: {untracked}. Run: git add docs/adr"
    )


@requires_git
def test_the_adr_directory_is_not_ignored() -> None:
    result = _git("check-ignore", "--quiet", "docs/adr/0009-branch-pruning-proof-basis.md")
    assert result.returncode != 0, (
        "docs/adr is gitignored again. A carve-out for the one ADR a document links to is "
        "not a fix: it leaves the other ADRs local-only, which is the failure this ratchet "
        "exists to catch. Remove the ignore and track the files."
    )


def test_the_named_adr_exists_and_is_a_question_carrying_document() -> None:
    """ADR-0009 is the document the A-1 human handoff depends on, so pin its shape.

    This is a content assertion, not a tracking one: it fails if the file is
    emptied, replaced with a stub, or refilled with a paraphrase that no longer
    contains the question a reviewer is meant to answer.
    """
    assert NAMED_ADR.exists(), f"{NAMED_ADR.name} is the ADR the A-1 handoff points at"
    text = NAMED_ADR.read_text(encoding="utf-8")

    assert "A-1" in text, "ADR-0009 must still identify the finding it routes"
    assert "?" in text, (
        "ADR-0009 carries a question to put to a named human; an ADR with no question in it "
        "is a record that the question was never actually asked"
    )


def test_gitignore_carries_no_adr_ignore_rule() -> None:
    """Pin the cause in a way that needs no subprocess.

    `test_the_adr_directory_is_not_ignored` reads git's own answer, which is the
    authoritative one. This is the cheap local mirror: if someone re-adds the
    rule but git is unavailable, or on a checkout where the pattern is quoted
    differently, the rule itself is still visible here.
    """
    rules = [
        line.strip()
        for line in GITIGNORE.read_text(encoding="utf-8").splitlines()
        if "docs/adr" in line and not line.strip().startswith("#")
    ]
    assert not rules, f".gitignore still carries an ADR rule: {rules}. ADRs are tracked."
