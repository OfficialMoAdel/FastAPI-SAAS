"""Tests for the comment-grease checker (``scripts/checks/comment_grease.py``).

The gate is only worth running if it can fail, so these tests pin the
distinction that matters and nothing else:

* the three genuine shapes are caught;
* the near-misses that dominate real production prose are NOT caught —
  especially "an unwired process would fail" and "must not match", which
  read as self-referential but describe the CODE. A gate that flags those
  is a gate that gets switched off, which is the failure mode this repo
  already records in docs/testing/source-text-ratchet.md;
* the live tree passes, and every allow-list entry is still live, so the
  checker cannot rot into either a false failure or a silent no-op.

Deliberately NOT a source-text test: this file reads its fixtures from
tmp_path, not from ``.py`` files in the repo, so it does not register
with scripts/count_source_text_tests.py and cannot move the ratchet
baseline.
"""

from pathlib import Path

import pytest

# scripts/ is not an installed package, but it IS a package on disk
# (scripts/checks/__init__.py), so the checker is importable as
# scripts.checks.comment_grease from the repository root.
from scripts.checks import comment_grease as checker

ROOT = Path(__file__).resolve().parents[3]

#: Comments whose reason for existing is a text scan. Each must be caught.
GREASE = [
    "# Kept as a named constant so grep still resolves the old key.",
    "# Do not reword this string - the arch test greps for it.",
    "# This comment is worded to avoid matching the forbidden-pattern scan.",
    "# Left in place so the source-text ratchet does not flag this line.",
]

#: Comments that mention tests or matching but explain the CODE. None may
#: be flagged; several are verbatim lines from the current tree, so a
#: regression here is a real false positive against production prose.
CLEAN = [
    "# An unwired process would fail on the first permission lookup, so "
    "check it at boot with the other DIP gates.",
    "# CORS is outermost so guard rejections (403/429/413) carry CORS "
    "headers. Pinned by tests/unit/test_middleware_order.py (R1-16).",
    "# run grep -r to find every caller of this seam.",
    "# the bucket key must use the same normalization or /API/V1/X and "
    "/api/v1/x mint separate buckets",
    "# Email/phone/SSN/card values keep their shape-preserving partial "
    "masks, so those key names must NOT appear here.",
    "# tests/core/test_stripe_adapter_boundary.py is the ratchet: it fails "
    "if any file imports the stripe SDK again.",
]


class TestCatchesGrease:
    @pytest.mark.parametrize("text", GREASE)
    def test_grease_is_flagged(self, text: str) -> None:
        assert checker.classify(text) is not None, f"missed grease: {text}"

    def test_grease_is_flagged_strong_rule(self) -> None:
        rule, _reason = checker.classify(GREASE[0])
        assert rule == checker.STRONG


class TestLeavesRealProseAlone:
    """The false-positive budget. A noisy gate is a disabled gate."""

    @pytest.mark.parametrize("text", CLEAN)
    def test_real_prose_is_not_flagged(self, text: str) -> None:
        assert checker.classify(text) is None, f"false positive on: {text}"

    def test_would_fail_alone_is_not_grease(self) -> None:
        """``would fail`` describes the code unless a scanner is named."""
        assert checker.classify("# an unwired process would fail here") is None

    def test_must_not_match_alone_is_not_grease(self) -> None:
        """Regexes are described by what must not match the INPUT."""
        assert (
            checker.classify("# a bare -- in prose must not match the pattern") is None
        )

    def test_empty_and_hash_only_comments(self) -> None:
        assert checker.classify("#") is None
        assert checker.classify("#   ") is None

    def test_docstrings_are_not_comments(self) -> None:
        """A docstring is API docs, so its text is not grease."""
        source = '"""Do not reword this - the test greps for it."""\nX = 1\n'
        assert checker.comment_tokens(source) == []


class TestTokensAndFiles:
    def test_comment_tokens_skip_docstring_but_keep_trailing(self) -> None:
        source = 'def f():\n    """Doc mentioning a test greps for it."""\n    x = 1  # reworded so the test greps\n'
        texts = [t.string for t in checker.comment_tokens(source)]
        assert texts == ["# reworded so the test greps"]

    def test_check_file_reports_line_and_path(self, tmp_path: Path) -> None:
        target = tmp_path / "src" / "app" / "thing.py"
        target.parent.mkdir(parents=True)
        target.write_text("X = 1\n# kept so grep still resolves the key\n", encoding="utf-8")
        found = checker.check_file(target, tmp_path)
        assert len(found) == 1
        finding, reason = found[0]
        assert finding.lineno == 2
        assert finding.path == "src/app/thing.py"
        assert reason is None

    def test_allowlist_entry_suppresses_with_reason(self, tmp_path: Path) -> None:
        target = tmp_path / "a.py"
        target.write_text("# kept so grep still resolves the key\n", encoding="utf-8")
        findings = checker.check_file(target, tmp_path)
        assert findings and findings[0][1] is None

    def test_path_outside_root_does_not_crash(self, tmp_path: Path) -> None:
        """Explicit file args may sit outside the repo; that must not raise."""
        outside = tmp_path / "elsewhere.py"
        outside.write_text("# kept so grep still resolves the key\n", encoding="utf-8")
        found = checker.check_file(outside, ROOT)
        assert found and isinstance(found[0][0].path, str)

    def test_syntax_error_is_skipped_not_raised(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.py"
        broken.write_text("def (:\n", encoding="utf-8")
        assert checker.comment_tokens("def (:\n") == []


class TestLiveRepository:
    """Guards the checker against rot in both directions."""

    def test_repository_has_no_comment_grease(self) -> None:
        findings: list = []
        for path in checker.iter_prod_files():
            findings.extend(
                finding
                for finding, reason in checker.check_file(path, ROOT)
                if reason is None and finding.rule == checker.STRONG
            )
        assert not findings, "unallow-listed comment-grease:\n" + "\n".join(
            f.format() for f in findings
        )

    def test_every_allowlist_entry_is_live(self) -> None:
        """A stale entry hides a future comment on the same line."""
        stale: list[str] = []
        for (rel, lineno), _reason in checker.ALLOWLIST.items():
            path = ROOT / rel
            if not path.is_file():
                stale.append(f"{rel}:{lineno} (file gone)")
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            if not (1 <= lineno <= len(lines)) or not lines[lineno - 1].lstrip().startswith("#"):
                stale.append(f"{rel}:{lineno} (no longer a comment)")
                continue
            if checker.classify(lines[lineno - 1]) is None:
                stale.append(f"{rel}:{lineno} (no longer matches a rule)")
        assert not stale, "stale allow-list entries: " + "; ".join(stale)

    def test_every_allowlist_entry_has_a_reason(self) -> None:
        missing = [f"{r}:{n}" for (r, n), reason in checker.ALLOWLIST.items() if not reason.strip()]
        assert not missing, f"allow-list entries missing a reason: {missing}"


class TestMainExitCodes:
    def test_clean_file_exits_zero(self, tmp_path: Path) -> None:
        clean = tmp_path / "clean.py"
        clean.write_text("# the backoff schedule is configurable per worker\nX = 1\n", encoding="utf-8")
        assert checker.main([str(clean)]) == 0

    def test_greasy_file_exits_one(self, tmp_path: Path) -> None:
        greasy = tmp_path / "greasy.py"
        greasy.write_text("# kept as a named constant so grep still resolves\n", encoding="utf-8")
        assert checker.main([str(greasy)]) == 1

    def test_default_run_covers_production_tree(self) -> None:
        """The no-argument invocation is the one CI runs; it must work."""
        assert checker.main([]) == 0
