"""F-1 differential gate: the linear scanner is byte-identical to the regex.

WHAT THIS PROVES
    ``attack_detection.scan_content_for_attacks`` reads the four super-linear
    ordering branches (``sql_union`` / ``sql_drop`` / ``sql_delete`` /
    ``sql_exec`` -- the ones whose unbounded ``.*?`` makes ``re`` O(n^2)) with
    ``attack_scan_linear`` instead of the backtracking regex. The observable
    is ``COMBINED_ATTACK_RE.finditer``'s ordered sequence of
    ``(lastgroup, start, end)``, because findings are consumed in that order
    and the ban reason reads ``detected_attacks[0]``. This file asserts the
    merged sequence is identical: 0 mismatches on the corpus, the adversarial
    fixtures, the depth injections, and a seeded token-soup fuzz.

WHY THE FIX IS NOT A DROP-IN REWRITE
    A NAIVE merge -- precompute the 13-branch ``finditer`` stream once and
    greedily merge it with the 4-branch candidates -- is WRONG, and this file
    pins the case that caught it (``TestTheCursorIsTheWholePoint``). The
    regex asks "leftmost match at/after the cursor", so when a 4-branch match
    ends INSIDE an earlier 13-branch match, the 13-branch side must be
    re-queried FROM THE CURSOR; a precomputed non-overlapping stream has
    already let that earlier match suppress the later one the cursor needs.
    The scanner therefore drives ``linear_re.search(content, cursor)``.

THE DRIFT RATCHET
    The 13-branch pattern is DERIVED from ``COMBINED_ATTACK_RE`` (four
    branches removed by name) rather than re-spelled, so it cannot drift. The
    ``test_the_derived_pattern_cannot_drift`` oracle re-derives it and
    reassembles the branches, failing loudly if a future change adds an
    unnamed branch or renames one of the four.
"""

from __future__ import annotations

import random
import time

import pytest

from src.app.core.config import settings
from src.app.middleware import attack_detection as ad
from src.app.middleware.attack_detection import COMBINED_ATTACK_RE
from src.app.middleware.attack_scan_linear import (
    QUADRATIC_GROUPS,
    build_linear_pattern,
    build_priority_map,
    split_branches,
)
from tests.helpers.attack_scan_corpus import ADVERSARIAL, BENIGN_128K, CLEAN_60K, depth_injections, fuzz_bodies

#: A minimal probe per production branch: a byte string that branch matches.
#: Drives the per-group equality check and the "the corpus is live for the
#: MERGED scanner too" claim (a branch only the old regex reached would be a
#: hole that a whole-corpus equality check can still hide behind an offset).
GROUP_PROBES: dict[str, bytes] = {
    "sql_union": b"x union all select y",
    "sql_or": b"x or 1=1",
    "sql_drop": b"x drop table y",
    "sql_delete": b"x delete from y",
    "sql_comment": b"'abc--",
    "sql_semicomment": b";\n--",
    "sql_exec": b"x exec y xp_cmdshell",
    "xss_script": b"<script src",
    "xss_jsuri": b"javascript:alert(1)",
    "xss_event": b"<img onerror=x",
    "xss_iframe_tag": b"<iframe src",
    "xss_object_tag": b"<object data",
    "path_dotdot": b"a/../b",
    "path_enc": b"a%2e%2e%2fb",
    "path_penc": b"a%2e%2e/b",
    "cmd_inj": b"a; cat /etc/passwd",
    "ssrf_cloud": b"http://169.254.169.254/latest",
}


def _reference(content: bytes) -> list[tuple[str | None, int, int]]:
    """The scanner's answer before F-1: the real production regex."""
    return [(m.lastgroup, m.start(), m.end()) for m in COMBINED_ATTACK_RE.finditer(content)]


def _reference_pairs(content: bytes) -> list[tuple[int, int, str]]:
    """The reference regex as ``(start, end, group)`` -- the scanner's tuple order."""
    return [(m.start(), m.end(), m.lastgroup or "") for m in COMBINED_ATTACK_RE.finditer(content)]


def _linear(content: bytes) -> list[tuple[str | None, int, int]]:
    """The scanner's answer after F-1: the merged linear path."""
    return [(group, start, end) for start, end, group in ad._iter_scan_matches(content)]


def test_the_derived_pattern_cannot_drift() -> None:
    """The 13-branch pattern is COMBINED_ATTACK_RE minus EXACTLY the four."""
    full = split_branches(COMBINED_ATTACK_RE.pattern)
    full_names = [name for name, _branch in full]
    linear_names = [name for name, _branch in split_branches(build_linear_pattern(COMBINED_ATTACK_RE.pattern))]

    assert set(full_names) == set(GROUP_PROBES), "a branch was added or renamed without a probe"
    assert len(full_names) == 17
    assert set(full_names) - set(linear_names) == set(QUADRATIC_GROUPS)
    # Every retained branch is byte-identical -- a byte-exact reassembly, so
    # the removal cannot have silently rewritten a neighbour.
    retained = {name: branch for name, branch in full if name not in QUADRATIC_GROUPS}
    for name, branch in split_branches(build_linear_pattern(COMBINED_ATTACK_RE.pattern)):
        assert branch == retained[name]
    assert set(build_priority_map(COMBINED_ATTACK_RE.pattern)) == set(full_names)


@pytest.mark.parametrize("group", sorted(GROUP_PROBES))
def test_every_branch_agrees_on_its_minimal_probe(group: str) -> None:
    """Each branch's own witness: the merged path must reproduce the regex."""
    probe = GROUP_PROBES[group]
    assert _linear(probe) == _reference(probe)
    assert group in {name for name, _s, _e in _reference(probe)}


class TestTheCursorIsTheWholePoint:
    """Regression probes for the naive-merge bug (precomputed 13-branch stream).

    Each payload makes a 4-branch match end INSIDE an earlier 13-branch match,
    which the cursor must re-query past. A naive merge loses the later match.
    """

    @pytest.mark.parametrize(
        "payload",
        [
            # sql_delete ends inside a sql_comment that swallowed the region;
            # ssrf_cloud after the cursor must still be found.
            b" table  delete xxp_' or 1=1 --from 169.254.169.254\n",
            b"delete select <script>' or 1=1 --../from ; cat ",
        ],
    )
    def test_a_match_after_a_cursor_inside_a_previous_match_is_not_lost(self, payload: bytes) -> None:
        assert _linear(payload) == _reference(payload)
        assert len(_reference(payload)) >= 2


class TestDifferentialOverTheCorpus:
    def test_adversarial_fixtures(self) -> None:
        for payload in ADVERSARIAL:
            assert _linear(payload) == _reference(payload), payload[:80]

    def test_depth_injections(self) -> None:
        for payload in depth_injections():
            assert _linear(payload) == _reference(payload), payload[:80]

    def test_large_benign_bodies_agree(self) -> None:
        # Iterated rather than parametrized: parametrizing on the bodies makes
        # pytest build a ~128 KB test id, which trips the runner.
        for body in (BENIGN_128K, CLEAN_60K):
            assert _linear(body) == _reference(body)

    def test_seeded_fuzz(self) -> None:
        # The corpus's own fuzz bodies, plus a token-soup fuzz that is dense in
        # the four openers and their closers -- the shapes most likely to
        # expose an ordering difference (the corpus fuzz is mostly benign).
        cases = list(fuzz_bodies())[:6000]
        rng = random.Random(20261023)
        tokens = [
            b"union ", b"select ", b"drop ", b"table ", b"delete ", b"from ", b"exec ", b"xp_",
            b"<script>", b"' or 1=1 --", b"; cat ", b"169.254.169.254", b"../", b"\n", b" ",
            b"x", b"javascript:", b"<a onx=", b"-- ", b"union", b"select",
        ]
        cases += [b"".join(rng.choice(tokens) for _ in range(rng.randint(0, 14))) for _ in range(20000)]

        mismatches = 0
        for body in cases:
            if _linear(body) != _reference(body):
                mismatches += 1
        assert mismatches == 0, f"{mismatches} mismatches over {len(cases)} cases"


class TestFindingsAndKillSwitch:
    def test_findings_dicts_are_identical(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payloads = [GROUP_PROBES[g] for g in GROUP_PROBES] + list(ADVERSARIAL)[:10]
        for payload in payloads:
            monkeypatch.setattr(settings.attack_detection, "ATTACK_DETECTION_LINEAR_SCAN", True)
            linear = ad.scan_content_for_attacks(payload, "request_body")
            monkeypatch.setattr(settings.attack_detection, "ATTACK_DETECTION_LINEAR_SCAN", False)
            legacy = ad.scan_content_for_attacks(payload, "request_body")
            assert linear == legacy, payload[:80]

    def test_kill_switch_restores_the_original_pattern(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """False runs the whole regex -- the pre-fix, super-linear path."""
        payload = b"union all select 1"
        monkeypatch.setattr(settings.attack_detection, "ATTACK_DETECTION_LINEAR_SCAN", False)
        assert _linear(payload) == _reference(payload)

    def test_the_quadratic_trigger_is_still_detected(self) -> None:
        assert [(g, s, e) for s, e, g in ad._iter_scan_matches(b"1 union all select 2")] == [
            ("sql_union", 2, 18)
        ]


class TestTheScannerIsLinear:
    """A tripwire, not a benchmark: the fix is O(n), so doubling cannot 4x."""

    def test_quadratic_class_no_longer_explodes(self) -> None:
        def scan(size_kb: int) -> float:
            body = b"union " * (size_kb * 1024 // 6)  # no `select` anywhere
            started = time.perf_counter()
            assert list(ad._iter_scan_matches(body)) == []
            return time.perf_counter() - started

        _ = scan(8)  # warm
        small = scan(16)
        large = scan(64)
        # 4x the input must stay well under an order of magnitude (linear ~4x;
        # the old O(n^2) path was ~16x and ~5 s at 64 KB).
        assert large < small * 12 + 0.05, f"super-linear: 16KB={small:.3f}s 64KB={large:.3f}s"
        assert large < 1.0, f"64 KB quadratic-class scan took {large:.3f}s"

    def test_repeat_class_where_both_tokens_are_present(self) -> None:
        """`union select` * k: the 4-branch candidates DENSE, 13-branch side empty.

        The other linear test only covers `union ` * k, where the 13-branch
        `search` returns None IMMEDIATELY (no `select` on any line) and the
        loop exits. That is the one shape that cannot expose a re-search per
        candidate. Here `select` is present on every line, so the cursor walks
        the whole body one candidate at a time and the 13-branch side is
        queried from each of them: an unmemoised `search(content, cursor)`
        pays O(n) per candidate for O(n^2) total (measured 85 s at 64 KB).
        The memoised match is reused, so the cost is linear in the body.

        Also an isomorphism assertion, because a memo that is dropped too
        eagerly (or never refreshed) silently loses matches rather than
        merely being slow.
        """

        def scan(size_kb: int) -> tuple[float, list[tuple[int, int, str]]]:
            body = b"union select " * (size_kb * 1024 // 14)
            started = time.perf_counter()
            found = [(s, e, g) for s, e, g in ad._iter_scan_matches(body)]
            return time.perf_counter() - started, found

        _ = scan(8)  # warm
        small, small_matches = scan(16)
        large, large_matches = scan(64)
        # 4x the input must stay well under an order of magnitude.
        assert large < small * 12 + 0.05, f"super-linear: 16KB={small:.3f}s 64KB={large:.3f}s"
        assert large < 1.0, f"64 KB repeat-class scan took {large:.3f}s"
        # One match per `union` opener: the count tracks the body size (the
        # byte count is not an exact 4x -- `16*1024//14` rounds down), so the
        # ratio is asserted against the actual byte lengths, not against 4.
        assert len(small_matches) > 0 and len(large_matches) > 0
        assert len(large_matches) == pytest.approx(
            len(small_matches) * 4, rel=0.05
        ), f"match count did not scale with the body: 16KB={len(small_matches)} 64KB={len(large_matches)}"
        assert all(group == "sql_union" for _s, _e, group in large_matches)
        # The memoisation must not change WHAT is found, only how often it is
        # searched for: identical sequence to the reference regex.
        body = b"union select " * (16 * 1024 // 14)
        assert [(s, e, g) for s, e, g in ad._iter_scan_matches(body)] == _reference_pairs(body)

    def test_repeat_class_mixed_with_thirteen_branch_matches(self) -> None:
        """Dense 4-branch candidates WITH 13-branch matches forcing a re-search.

        Exercises the memo-invalidation path: a `<script>` match ends past the
        memoised 13-branch start, so the cursor advances beyond it and the
        next iteration must re-search rather than reuse a stale memo. Getting
        this wrong loses the tail matches.
        """
        block = b"union select " * 8 + b"<script>x</script>"
        body = block * 32
        assert [(s, e, g) for s, e, g in ad._iter_scan_matches(body)] == _reference_pairs(body)
        assert len(list(ad._iter_scan_matches(body))) == len(list(COMBINED_ATTACK_RE.finditer(body)))
