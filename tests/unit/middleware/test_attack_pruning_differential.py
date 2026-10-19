"""A-1 oracle: branch-pruning differential for the attack-scan combined regex.

WHAT THIS PINS: the future A-1 diff prunes the 17-group ``COMBINED_ATTACK_RE``
to a subset of branches per payload (selected by necessary-literal presence)
and must produce byte-identical verdicts. This file proves, on HEAD, that the
pruning discipline is sound: a selection rule driven ONLY by the 15-literal
prefilter set yields ``(lastgroup, start, end)`` sequences identical to the
full regex across a seeded corpus, property arms, and a 120KB depth matrix.

WHY AN IN-TEST PRUNER (idl3 pattern): no ``select_branches`` and no
memoization exist in ``attack_detection.py`` (verified: the only scan entry is
``_check_for_patterns`` → prefilter → full finditer). The A-1 pruner is future
work, so the oracle SPECIFIES the contract with an in-test reference pruner
that the diff then satisfies. The pinned table mirrors the group→literal
comment at ``attack_detection.py:113-118`` and self-checks against the real
file in both directions.

STRUCTURE (bead acceptance mapping):
- table integrity (closed world, order, byte-exact reassembly, no dead
  literal) — the mapping cannot drift from the file silently;
- P1 completeness: every matched group is selected (the ONLY
  over-strengthened-literal catcher — a table demanding a literal the attack
  does not need fails HERE, via 17 directed minimal probes);
- seeded differential (random.Random(20261004), 20000 cases) through the REAL
  ``_check_for_patterns`` with a monkeypatched pruned pattern, comparing the
  full ``attacks`` lists (covers the sql_comment_end multipart skip, which
  lives downstream of the regex);
- P2 monotonicity (adding literals only grows the selection);
- P3 invariance (order/duplication do not move the selection);
- P4 sql_comment_end stability on multipart-boundary payloads;
- depth matrix: 8 payloads x 0/50/90/99% through a 120KB body;
- strictness proof (the pruner really prunes — not a select-all tautology);
- non-ASCII/NFKC + zero-width arms.

NOT DONE, BY USER INSTRUCTION: is-committed = NOT DONE (dont commit or push
any file). E2E-over-HTTP-on-Linux NOT delivered (needs the Linux target).
Perf tripwires live separately in tests/benchmarks/test_attack_scan_subset.py.
"""

from __future__ import annotations

import random
import re
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.app.middleware import attack_detection as ad

SEED = 20261004
N_DIFFERENTIAL_CASES = 20000
N_GROUPS = 17
DEPTH_FRACTIONS = (0.0, 0.5, 0.9, 0.99)
BIG_BODY_LEN = 120_000

# Pinned table: group -> frozenset of NECESSARY literals (lowercase ASCII,
# from the 15-literal _PREFILTER_LITERALS_BYTES set). A group is selected iff
# ANY of its necessary literals is present (ASCII-folded) in the payload.
# Mirrors the group->literal comment at attack_detection.py:113-118.
GROUP_NECESSARY_LITERALS: dict[str, frozenset[bytes]] = {
    "sql_union": frozenset({b"union"}),
    "sql_or": frozenset({b"or"}),
    "sql_drop": frozenset({b"drop"}),
    "sql_delete": frozenset({b"delete"}),
    "sql_comment": frozenset({b"--"}),
    "sql_semicomment": frozenset({b"--"}),
    "sql_exec": frozenset({b"exec"}),
    "xss_script": frozenset({b"<"}),
    "xss_jsuri": frozenset({b"javascript"}),
    "xss_event": frozenset({b"<"}),
    "xss_iframe_tag": frozenset({b"<"}),
    "xss_object_tag": frozenset({b"<"}),
    "path_dotdot": frozenset({b"../"}),
    "path_enc": frozenset({b"%2e%2e%2f"}),
    "path_penc": frozenset({b"%2e%2e/"}),
    "cmd_inj": frozenset({b";", b"||", b"&&"}),
    "ssrf_cloud": frozenset({b"169.254.169.254"}),
}


def _real_group_order() -> list[str]:
    """Alternative order straight from the compiled pattern (no hardcoding)."""
    return [m.group(1).decode("ascii") for m in re.finditer(rb"\(\?P<([A-Za-z_]+)>", ad.COMBINED_ATTACK_RE.pattern)]


_GROUP_ORDER: list[str] = _real_group_order()


def _real_group_subpatterns() -> dict[str, bytes]:
    """Per-group sub-pattern bytes, split exactly on top-level alternation."""
    chunks = ad.COMBINED_ATTACK_RE.pattern.split(b"|(?P<")
    out: dict[str, bytes] = {}
    for i, chunk in enumerate(chunks):
        name = _GROUP_ORDER[i]
        prefix = b"(?P<" + name.encode("ascii") + b">" if i == 0 else name.encode("ascii") + b">"
        assert chunk.startswith(prefix), (i, name, chunk[:40])
        body = chunk[len(prefix) :]
        assert body.endswith(b")"), (i, name, chunk[-40:])
        out[name] = body[:-1]
    return out


_GROUP_SUBPATTERNS: dict[str, bytes] = _real_group_subpatterns()

_FLAGS = re.IGNORECASE | re.MULTILINE

_compiled_cache: dict[frozenset[str], re.Pattern[bytes]] = {}


def _select(payload: bytes) -> list[str]:
    """Reference pruner: groups whose necessary literal is present (folded)."""
    lowered = payload.lower()
    present = frozenset(lit for lit in ad._PREFILTER_LITERALS_BYTES if lit in lowered)
    return [g for g in _GROUP_ORDER if GROUP_NECESSARY_LITERALS[g] & present]


def _pruned_pattern(selection: frozenset[str]) -> re.Pattern[bytes] | None:
    """Compiled subset regex in original alternative order (None = run nothing).

    An empty selection means no group can match, so the pruned scan runs no
    regex at all — the differential then proves the full scan also finds
    nothing, which is exactly the pruning claim.
    """
    if not selection:
        return None
    if selection not in _compiled_cache:
        subs = [
            b"(?P<" + g.encode("ascii") + b">" + _GROUP_SUBPATTERNS[g] + b")" for g in _GROUP_ORDER if g in selection
        ]
        _compiled_cache[selection] = re.compile(b"|".join(subs), _FLAGS)
    return _compiled_cache[selection]


def _attacks(mw: Any, content: bytes) -> list[dict]:
    """Drive the REAL _check_for_patterns and return its attacks list."""
    attacks: list[dict] = []
    mw._check_for_patterns(content, "request_body", attacks)
    return attacks


@pytest.fixture
def mw() -> Any:
    """Middleware instance with deterministic (redacted) matched_content."""
    instance = ad.AttackDetectionMiddleware(app=MagicMock())
    instance._log_full_request = False
    return instance


# --------------------------------------------------------------------------
# Corpus generation (seeded stdlib random only — random.Random(SEED) per bead)
# --------------------------------------------------------------------------

_BENIGN_TOKENS: list[bytes] = [
    b"hello",
    b"user",
    b"profile",
    b"name",
    b"email",
    b"GET",
    b"POST",
    b"/api/v1/health",
    b"200",
    b"application/json",
    b"ipsum",
    b"amet",
    b"sit",
    b"12345",
    b"67890",
    b"abc123",
    b"xyz",
    b" ",
    b"  ",
    b"\n",
    b"\t",
    b",",
    b".",
    b":",
    b"-",
    b"_",
    b"/",
]

_LITERAL_TOKENS: list[bytes] = sorted(ad._PREFILTER_LITERALS_BYTES)

_ATTACK_FRAGMENTS: list[bytes] = [
    b"UNION SELECT password FROM users",
    b"' OR '1'='1",
    b"or 1=1",
    b"DROP TABLE users",
    b"DELETE FROM sessions",
    b"'--",
    b";--",
    b"1;--",
    b"EXEC xp_cmdshell",
    b"<script>alert(1)</script>",
    b"javascript:alert(document.domain)",
    b'<img src=x onerror="alert(1)">',
    b"<iframe src='http://evil.example/'>",
    b"<object data='x'>",
    b"../../../../etc/passwd",
    b"%2e%2e%2fetc%2fpasswd",
    b"%2e%2e/etc/passwd",
    b";cat /etc/passwd",
    b"ping -c1 169.254.169.254",
    b"169.254.169.254",
]

_NONASCII_TOKENS: list[bytes] = [
    "café".encode(),
    "αβγ".encode(),
    "中文".encode(),
    "ＵＮＩＯＮ".encode(),
    "ｓｅｌｅｃｔ".encode(),
]

_ZERO_WIDTH_TOKENS: list[bytes] = [
    "​".encode(),
    "﻿".encode(),
    "a​b".encode(),
]

_SEPARATORS: list[bytes] = [b" ", b"", b"\n", b" "]


def _random_case(rng: random.Random) -> bytes:
    """One seeded payload: benign base + literals + occasional full attack."""
    parts: list[bytes] = [rng.choice(_BENIGN_TOKENS) for _ in range(rng.randint(0, 12))]
    for _ in range(rng.randint(0, 4)):
        parts.append(rng.choice(_LITERAL_TOKENS))
    if rng.random() < 0.3:
        parts.append(rng.choice(_ATTACK_FRAGMENTS))
    if rng.random() < 0.05:
        parts.append(rng.choice(_NONASCII_TOKENS))
    if rng.random() < 0.03:
        parts.append(rng.choice(_ZERO_WIDTH_TOKENS))
    out = bytearray()
    for p in parts:
        out += rng.choice(_SEPARATORS) + p
    return bytes(out[:300])


def _corpus(n: int, seed: int = SEED) -> list[bytes]:
    rng = random.Random(seed)
    return [_random_case(rng) for _ in range(n)]


# 8 payloads x 8 groups for the depth matrix (group, attack payload).
_DEPTH_PAYLOADS: list[tuple[str, bytes]] = [
    ("sql_union", b"UNION SELECT password FROM users"),
    ("sql_or", b"' OR '1'='1"),
    ("sql_comment", b"SELECT * FROM t WHERE a='x'-- comment"),
    ("xss_script", b"<script>alert(1)</script>"),
    ("xss_jsuri", b"javascript:alert(document.domain)"),
    ("path_dotdot", b"../../../../etc/passwd"),
    ("cmd_inj", b";cat /etc/passwd"),
    ("ssrf_cloud", b"ping -c1 169.254.169.254"),
]

# Per-group minimal probes: ONLY the necessary literal(s) may be present.
# If the table over-strengthens (demands a literal the attack does not need),
# the group is unselected here while the full regex still matches — P1 fails.
_MINIMAL_PROBES: list[tuple[str, bytes]] = [
    ("sql_union", b"union select 1"),
    ("sql_or", b"or 1=1"),
    ("sql_drop", b"drop table t"),
    ("sql_delete", b"delete from t"),
    ("sql_comment", b"a'--b"),
    ("sql_semicomment", b"x;--y"),
    ("sql_exec", b"exec xp_"),
    ("xss_script", b"<script >"),
    ("xss_jsuri", b"javascript :"),
    ("xss_event", b'<a onclick="x"'),
    ("xss_iframe_tag", b"<iframe >"),
    ("xss_object_tag", b"<object >"),
    ("path_dotdot", b"a/../b"),
    ("path_enc", b"a%2e%2e%2fb"),
    ("path_penc", b"a%2e%2e/b"),
    ("cmd_inj", b"x;cat y"),
    ("ssrf_cloud", b"169.254.169.254"),
]

_MULTIPART_SKIP = (
    b"--AaBbCcDdEeFfGgHh1234\r\n"
    b'Content-Disposition: form-data; name="x"\r\n\r\n1\r\n'
    b"--AaBbCcDdEeFfGgHh1234--\r\n"
)
_MULTIPART_KEEP = b"SELECT * FROM t WHERE a='x'-- trailing comment"


# --------------------------------------------------------------------------
# Table integrity: the pinned mapping cannot drift from the file silently
# --------------------------------------------------------------------------


class TestPinnedTableMatchesReality:
    def test_exactly_seventeen_groups_both_directions(self) -> None:
        assert set(GROUP_NECESSARY_LITERALS) == set(ad._GROUP_TO_ATTACK)
        assert len(GROUP_NECESSARY_LITERALS) == N_GROUPS
        assert len(ad._GROUP_TO_ATTACK) == N_GROUPS

    def test_group_order_matches_the_compiled_pattern(self) -> None:
        assert _GROUP_ORDER == list(ad._GROUP_TO_ATTACK)

    def test_subpattern_split_reassembles_byte_exact(self) -> None:
        rebuilt = b"|".join(
            b"(?P<" + g.encode("ascii") + b">" + _GROUP_SUBPATTERNS[g] + b")" for g in _GROUP_ORDER
        )
        assert rebuilt == ad.COMBINED_ATTACK_RE.pattern

    def test_every_table_literal_is_a_real_prefilter_literal(self) -> None:
        for group, needed in GROUP_NECESSARY_LITERALS.items():
            assert needed, group
            for lit in needed:
                assert lit in ad._PREFILTER_LITERALS_BYTES, (group, lit)

    def test_no_prefilter_literal_is_dead(self) -> None:
        """Every real literal is necessary for >= 1 group (else dead weight)."""
        for lit in ad._PREFILTER_LITERALS_BYTES:
            users = [g for g, needed in GROUP_NECESSARY_LITERALS.items() if lit in needed]
            assert users, lit

    def test_benign_tokens_really_are_benign(self) -> None:
        """No benign token contains any literal (else strictness is vacuous)."""
        for tok in _BENIGN_TOKENS:
            for lit in ad._PREFILTER_LITERALS_BYTES:
                assert lit not in tok.lower(), (tok, lit)


class TestMinimalProbesAreMinimal:
    @pytest.mark.parametrize("group,payload", _MINIMAL_PROBES)
    def test_probe_contains_a_necessary_literal(self, group: str, payload: bytes) -> None:
        """The probe contains AT LEAST ONE necessary literal (ANY-semantics).

        Demanding ALL of them would be the over-strengthening error in test
        form: cmd_inj needs `;` OR `||` OR `&&`, and `x;cat y` carries only
        `;`. Equality with the necessary set is unachievable where literals
        are inseparable (`;--` always contains both `;` and `--`); the
        non-empty intersection is the load-bearing direction.
        """
        lowered = payload.lower()
        present = frozenset(lit for lit in ad._PREFILTER_LITERALS_BYTES if lit in lowered)
        assert GROUP_NECESSARY_LITERALS[group] & present, (group, payload, present)


# --------------------------------------------------------------------------
# P1: completeness — every matched group is selected (over-strength catcher)
# --------------------------------------------------------------------------


class TestP1Completeness:
    @pytest.mark.parametrize("group,payload", _MINIMAL_PROBES)
    def test_minimal_probe_matches_and_is_selected(self, group: str, payload: bytes) -> None:
        """Full finds something real AND the group is selected.

        Note this does NOT assert ``group in found``: alternation order
        shadows some groups (``x;--y`` matches sql_comment first, so
        sql_semicomment never reports even though its branch matches) — the
        pruned pattern preserves alternative order, so shadowing agrees by
        construction and the corpus differential proves it case by case.
        """
        found = [m.lastgroup for m in ad.COMBINED_ATTACK_RE.finditer(payload)]
        assert found, (group, payload)
        assert group in _select(payload), (group, payload)

    def test_every_match_is_selected_across_corpus(self) -> None:
        bad: list[tuple[int, bytes, str | None]] = []
        for i, payload in enumerate(_corpus(N_DIFFERENTIAL_CASES)):
            selected = set(_select(payload))
            for m in ad.COMBINED_ATTACK_RE.finditer(payload):
                if m.lastgroup not in selected:
                    bad.append((i, payload, m.lastgroup))
        assert bad == []


# --------------------------------------------------------------------------
# Seeded differential through the REAL _check_for_patterns (P4 lives here too)
# --------------------------------------------------------------------------


def _assert_pruned_equals_full(monkeypatch: pytest.MonkeyPatch, mw: Any, payload: bytes) -> None:
    """Pruned-pattern verdict must equal full-pattern verdict, exactly.

    The FULL pattern is (re-)installed explicitly before reading the
    expectation: ``monkeypatch`` is function-scoped, so without this every
    call after the first would read its expectation through the PREVIOUS
    call's pruned pattern — a harness-ordering bug that once blamed the
    oracle for a divergence the production code never produced.
    """
    full = ad.COMBINED_ATTACK_RE
    monkeypatch.setattr(ad, "COMBINED_ATTACK_RE", full)
    expected = _attacks(mw, payload)
    pruned = _pruned_pattern(frozenset(_select(payload)))
    monkeypatch.setattr(ad, "COMBINED_ATTACK_RE", pruned if pruned is not None else re.compile(b"(?!)"))
    assert _attacks(mw, payload) == expected


class TestSeededDifferential:
    def test_pruned_equals_full_across_seeded_corpus(self, monkeypatch: pytest.MonkeyPatch, mw: Any) -> None:
        full = ad.COMBINED_ATTACK_RE
        for i, payload in enumerate(_corpus(N_DIFFERENTIAL_CASES)):
            monkeypatch.setattr(ad, "COMBINED_ATTACK_RE", full)
            expected = _attacks(mw, payload)
            pruned = _pruned_pattern(frozenset(_select(payload)))
            monkeypatch.setattr(ad, "COMBINED_ATTACK_RE", pruned if pruned is not None else re.compile(b"(?!)"))
            got = _attacks(mw, payload)
            assert got == expected, (i, payload)

    def test_empty_selection_means_empty_verdict(self, mw: Any) -> None:
        """Benign payloads select nothing AND the full scan finds nothing."""
        rng = random.Random(SEED + 1)
        checked = 0
        for _ in range(500):
            payload = b" ".join(rng.choice(_BENIGN_TOKENS) for _ in range(rng.randint(1, 10)))
            assert _select(payload) == []
            assert _attacks(mw, payload) == []
            checked += 1
        assert checked == 500


class TestP4SqlCommentEndStability:
    def test_multipart_boundary_skip_agrees(self, monkeypatch: pytest.MonkeyPatch, mw: Any) -> None:
        _assert_pruned_equals_full(monkeypatch, mw, _MULTIPART_SKIP)

    def test_non_boundary_comment_agrees(self, monkeypatch: pytest.MonkeyPatch, mw: Any) -> None:
        full = _attacks(mw, _MULTIPART_KEEP)
        assert any(a["pattern_name"] == "sql_comment_end" for a in full)
        _assert_pruned_equals_full(monkeypatch, mw, _MULTIPART_KEEP)


# --------------------------------------------------------------------------
# P2 monotonicity / P3 invariance (selection-only, no regex — fast)
# --------------------------------------------------------------------------


class TestP2Monotonicity:
    def test_adding_a_literal_only_grows_the_selection(self) -> None:
        rng = random.Random(SEED + 2)
        for _ in range(300):
            payload = _random_case(rng)
            before = set(_select(payload))
            for lit in ad._PREFILTER_LITERALS_BYTES:
                if lit not in payload.lower():
                    assert before <= set(_select(payload + b" " + lit))

    def test_removing_all_occurrences_only_shrinks_the_selection(self) -> None:
        rng = random.Random(SEED + 3)
        for _ in range(300):
            payload = _random_case(rng)
            before = set(_select(payload))
            lit = rng.choice(_LITERAL_TOKENS)
            assert set(_select(payload.replace(lit, b""))) <= before


class TestP3Invariance:
    def test_token_order_does_not_move_the_selection(self) -> None:
        # Joiner is NUL, not "|": an empty token between two "|" joiners
        # fabricates a "||" literal no token contains, so "|" as joiner
        # makes ORDER look significant when only the harness is. NUL appears
        # in no literal, so no literal can span a NUL boundary.
        rng = random.Random(SEED + 4)
        for _ in range(300):
            toks = [_random_case(rng) for _ in range(3)]
            a = _select(b"\x00".join(toks))
            shuffled = list(toks)
            rng.shuffle(shuffled)
            assert _select(b"\x00".join(shuffled)) == a

    def test_duplicating_a_present_literal_changes_nothing(self) -> None:
        rng = random.Random(SEED + 5)
        for _ in range(300):
            payload = _random_case(rng)
            present = [lit for lit in ad._PREFILTER_LITERALS_BYTES if lit in payload.lower()]
            if not present:
                continue
            lit = rng.choice(present)
            assert _select(payload + b" " + lit) == _select(payload)


# --------------------------------------------------------------------------
# Depth matrix / strictness / non-ASCII / zero-width
# --------------------------------------------------------------------------


def _embed(payload: bytes, fraction: float) -> bytes:
    """Payload embedded at fraction of a 120KB body, space-padded both sides.

    The padding is load-bearing, not cosmetic: the filler is ``q``s (word
    characters), so embedding flush would glue ``qUNION`` and kill the ``\\b``
    boundary the sql_union branch requires. Real bodies have structural
    separators (spaces, quotes, braces) around attack text; this models that.
    """
    filler_len = BIG_BODY_LEN - len(payload) - 2
    pos = int(filler_len * fraction)
    return b"q" * pos + b" " + payload + b" " + b"q" * (filler_len - pos)


class TestDepthMatrix:
    @pytest.mark.parametrize("group,payload", _DEPTH_PAYLOADS)
    @pytest.mark.parametrize("fraction", DEPTH_FRACTIONS)
    def test_payload_found_at_every_depth(
        self, monkeypatch: pytest.MonkeyPatch, mw: Any, group: str, payload: bytes, fraction: float
    ) -> None:
        body = _embed(payload, fraction)
        full = _attacks(mw, body)
        assert any(a["pattern_name"] == ad._GROUP_TO_ATTACK[group][0] for a in full), (group, fraction)
        _assert_pruned_equals_full(monkeypatch, mw, body)


class TestStrictness:
    def test_the_pruner_really_prunes(self) -> None:
        """Both extremes must occur, else the test is a select-all tautology."""
        corpus = _corpus(2000, SEED + 6)
        empty = sum(1 for p in corpus if not _select(p))
        strict = sum(1 for p in corpus if 0 < len(_select(p)) < N_GROUPS)
        assert empty > 0
        assert strict > 0

    def test_selection_never_exceeds_seventeen(self) -> None:
        for payload in _corpus(2000, SEED + 7):
            assert len(_select(payload)) <= N_GROUPS


class TestNonAsciiArms:
    @pytest.mark.parametrize(
        "payload",
        [
            "café UNION SELECT x".encode(),
            "αβ UNION SELECT x".encode(),
            "ＵＮＩＯＮ SELECT x".encode(),
            "中文' OR '1'='1".encode(),
            "test￾boundary ￾".encode(),
        ],
    )
    def test_folded_payloads_agree(self, monkeypatch: pytest.MonkeyPatch, mw: Any, payload: bytes) -> None:
        _assert_pruned_equals_full(monkeypatch, mw, payload)

    @pytest.mark.parametrize("payload", list(_ZERO_WIDTH_TOKENS) + ["a\u200b'--b".encode()])
    def test_zero_width_payloads_agree(self, monkeypatch: pytest.MonkeyPatch, mw: Any, payload: bytes) -> None:
        _assert_pruned_equals_full(monkeypatch, mw, payload)


class TestAntiVacuity:
    def test_the_full_scan_finds_something_in_the_corpus(self) -> None:
        assert any(next(ad.COMBINED_ATTACK_RE.finditer(p), None) is not None for p in _corpus(500))

    def test_the_selection_is_nonempty_somewhere(self) -> None:
        assert any(_select(p) for p in _corpus(500))


# --------------------------------------------------------------------------
# A-2 (li89): attack corpus + the gap-600 payload.
#
# The xss_event branch caps its tag scan at 512 chars
# (``<[^<>]{0,512}?`` at attack_detection.py:93). A payload whose event
# handler sits past that bound is LOST by the scan — deliberately and
# acceptedly, but the loss must be VISIBLE, not silent. The gap-600 payload
# sits just past the bound: if it ever starts matching, the bound moved; if
# any OTHER corpus payload stops matching, that is unnoticed loss and the
# bound must be raised, never accepted. The gap-500 control proves the
# boundary is exactly where the pattern says it is.
# --------------------------------------------------------------------------

_GAP_600 = b"<a " + b"x" * 600 + b' onclick="y">'
_GAP_500_CONTROL = b"<a " + b"x" * 500 + b' onclick="y">'

# One payload per group: the corpus the 512 bound must never silently shrink.
_CORPUS: list[tuple[str, bytes]] = [
    ("sql_union", b"UNION SELECT password FROM users"),
    ("sql_or", b"' OR '1'='1"),
    ("sql_drop", b"DROP TABLE users"),
    ("sql_delete", b"DELETE FROM sessions"),
    ("sql_comment", b"SELECT * FROM t WHERE a='x'-- comment"),
    ("sql_semicomment", b"SELECT 1;-- comment"),
    ("sql_exec", b"EXEC xp_cmdshell"),
    ("xss_script", b"<script>alert(1)</script>"),
    ("xss_jsuri", b"javascript:alert(document.domain)"),
    ("xss_event", b'<img src=x onerror="alert(1)">'),
    ("xss_iframe_tag", b"<iframe src='http://evil.example/'>"),
    ("xss_object_tag", b"<object data='x'>"),
    ("path_dotdot", b"../../../../etc/passwd"),
    ("path_enc", b"%2e%2e%2fetc%2fpasswd"),
    ("path_penc", b"%2e%2e/etc/passwd"),
    ("cmd_inj", b";cat /etc/passwd"),
    ("ssrf_cloud", b"http://169.254.169.254/latest/meta-data/"),
]


# --------------------------------------------------------------------------
# C-2 (FastAPI_saas-niix): the BENIGN user-agent whose reason was corrected
# --------------------------------------------------------------------------
#
# The reason this user-agent is benign is the POSITION of "id", not its
# absence. "Android" contains "id" (A-n-d-r-o-i-d); "Pixel" does not. The
# "id" inside "Android" is mid-word, so it is never a token a separator
# introduces, and no command word follows any ";" at a word boundary -- so
# the cmd_inj branch does not match and the full regex finds nothing.
#
# The reason is carried HERE, as a case the corpus CHECKS, because a reason
# written only in prose is a reason nobody checks. A future reader who
# "fixes" the wrong token -- dropping the "id" out of Android, or appending
# a command to the UA -- breaks these tests loudly instead of quietly
# changing what the corpus means.
_PIXEL_USER_AGENT = (
    b"Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36"
    b" (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"
)
#: The recorded reason, as a token a test can compare against.
_PIXEL_UA_REASON = "android_not_pixel"
#: The command words the cmd_inj branch accepts, in the regex's own order.
_CMD_INJ_COMMANDS: tuple[bytes, ...] = (b"cat", b"ls", b"id", b"whoami", b"ping", b"curl", b"wget")


class TestAttackCorpusAndGap600:
    def test_corpus_covers_all_seventeen_groups(self) -> None:
        assert sorted(g for g, _ in _CORPUS) == sorted(ad._GROUP_TO_ATTACK)

    @pytest.mark.parametrize("group,payload", [p for p in _CORPUS if p[0] != "sql_semicomment"])
    def test_every_corpus_payload_is_found(self, mw: Any, group: str, payload: bytes) -> None:
        """If any of these stops matching, that is UNNOTICED loss: raise the
        bound, never accept. (Green on HEAD; guards the A-2 diff.)

        sql_semicomment is EXCLUDED here by design (see the shadowing test
        below): it can never report on HEAD, so no payload can satisfy
        ``group in found`` for it.
        """
        found = [m.lastgroup for m in ad.COMBINED_ATTACK_RE.finditer(payload)]
        assert group in found, (group, payload, found)

    @pytest.mark.parametrize(
        "payload",
        [b";--", b"x;--y", b"SELECT 1;-- comment", b";--\n;--", b"a = 1;-- drop"],
    )
    def test_sql_semicomment_is_always_shadowed(self, mw: Any, payload: bytes) -> None:
        """sql_semicomment's branch matches, but sql_comment (earlier in the
        alternation) always wins the same ``;--`` text first, so the group
        never reports on HEAD. Pinned as a visible fact: if a future diff
        reorders the branches, this fails loudly instead of silently
        changing which pattern_name the verdicts carry."""
        found = [m.lastgroup for m in ad.COMBINED_ATTACK_RE.finditer(payload)]
        assert "sql_comment" in found, (payload, found)
        assert "sql_semicomment" not in found, (payload, found)

    @pytest.mark.parametrize("group,payload", _CORPUS)
    def test_every_corpus_payload_agrees_pruned(
        self, monkeypatch: pytest.MonkeyPatch, mw: Any, group: str, payload: bytes
    ) -> None:
        _assert_pruned_equals_full(monkeypatch, mw, payload)

    def test_gap_500_control_matches(self, mw: Any) -> None:
        """The handler at 500 chars is INSIDE the bound: must be found."""
        found = [m.lastgroup for m in ad.COMBINED_ATTACK_RE.finditer(_GAP_500_CONTROL)]
        assert "xss_event" in found

    def test_gap_600_is_lost_and_the_loss_is_visible(self, mw: Any) -> None:
        """The handler at 600 chars is PAST the 512 bound: the scan loses it.

        This is the ACCEPTED deliberate loss the bead names — asserted here so
        the loss is a visible, pinned fact rather than a silent one. The day
        this assertion fails (the payload starts matching), the bound moved
        and the corpus statement below must be re-derived, not deleted.
        """
        found = [m.lastgroup for m in ad.COMBINED_ATTACK_RE.finditer(_GAP_600)]
        assert "xss_event" not in found, found
        assert _attacks(mw, _GAP_600) == []

    def test_gap_600_pruned_agrees_with_full(
        self, monkeypatch: pytest.MonkeyPatch, mw: Any
    ) -> None:
        """Even the lost payload must agree: pruned finds nothing either."""
        _assert_pruned_equals_full(monkeypatch, mw, _GAP_600)
        _assert_pruned_equals_full(monkeypatch, mw, _GAP_500_CONTROL)


def _is_word_byte(char: int) -> bool:
    """True for a byte that can be part of an identifier."""
    return chr(char).isalnum() or char == 0x5F


def _id_occurrences_at_a_word_start(payload: bytes) -> list[int]:
    """The offsets of every "id" in `payload` that is NOT inside a word.

    An "id" at a word start is a candidate command word; one that is
    mid-word (as in "Android") can never be the token a ";" introduces.
    """
    lowered = payload.lower()
    offsets: list[int] = []
    start = 0
    while True:
        index = lowered.find(b"id", start)
        if index < 0:
            return offsets
        before = lowered[index - 1] if index > 0 else -1
        if before < 0 or not _is_word_byte(before):
            offsets.append(index)
        start = index + 1


def _command_word_after_each_semicolon(payload: bytes) -> list[bytes]:
    """The command word that follows each ";" at a word boundary, if any."""
    lowered = payload.lower()
    found: list[bytes] = []
    start = 0
    while True:
        index = lowered.find(b";", start)
        if index < 0:
            return found
        rest = lowered[index + 1 :].lstrip()
        token = rest.split(b" ")[0].split(b"\t")[0].split(b"\n")[0]
        if token in _CMD_INJ_COMMANDS:
            found.append(token)
        start = index + 1


class TestTheCorrectedAndroidReasonIsInTheCorpus:
    """C-2's acceptance: the corrected reason is a case the corpus checks.

    The reason is not "Pixel has no `id`" -- it is that "Android" HAS one
    and it sits in the wrong POSITION. These assertions are written so that
    the wrong reason fails them.
    """

    def test_the_case_is_carried_with_its_reason(self) -> None:
        assert _PIXEL_UA_REASON == "android_not_pixel"
        assert b"Android" in _PIXEL_USER_AGENT
        assert b"Pixel" in _PIXEL_USER_AGENT

    def test_the_user_agent_does_carry_an_id(self) -> None:
        """The reason is POSITION, not ABSENCE: the "id" is really there."""
        assert b"id" in _PIXEL_USER_AGENT.lower()
        assert b"id" not in b"pixel"

    def test_every_id_in_the_user_agent_is_mid_word(self) -> None:
        """No "id" sits at a word start, so none can be a command word."""
        assert _id_occurrences_at_a_word_start(_PIXEL_USER_AGENT) == []

    def test_no_command_word_follows_any_semicolon(self) -> None:
        assert _command_word_after_each_semicolon(_PIXEL_USER_AGENT) == []

    def test_the_user_agent_is_benign_under_the_full_regex(self, mw: Any) -> None:
        found = [m.lastgroup for m in ad.COMBINED_ATTACK_RE.finditer(_PIXEL_USER_AGENT)]
        assert found == [], found
        assert _attacks(mw, _PIXEL_USER_AGENT) == []

    def test_the_user_agent_pruned_agrees_with_full(
        self, monkeypatch: pytest.MonkeyPatch, mw: Any
    ) -> None:
        """The differential property holds for this case too."""
        _assert_pruned_equals_full(monkeypatch, mw, _PIXEL_USER_AGENT)
