"""Verdict-equivalence oracle for the P1-6 attack-scan prefilter.

Guards the soundness invariant: every regex group has a *necessary* literal,
so ``not _attack_prefilter_hit(content)`` provably implies the combined regex
cannot match. Tests:

1. No-miss: prefilter-miss => raw regex finds nothing (per payload).
2. Path agreement: automaton vs str.find fallback produce identical
   ``_check_for_patterns`` verdicts.
3. Group coverage: corpus hits all 17 named groups, so a future regex edit
   that adds a group without a literal shows up as a coverage failure.
"""

from unittest.mock import MagicMock

import pytest

from src.app.middleware import attack_detection as ad
from src.app.middleware.attack_detection import (
    COMBINED_ATTACK_RE,
    AttackDetectionMiddleware,
)

# One true positive per named group (must each match the production regex).
TRUE_POSITIVES: list[tuple[str, bytes]] = [
    ("sql_union", b"1 UNION SELECT password FROM users"),
    ("sql_or", b"admin' OR 1=1"),
    ("sql_drop", b"; DROP TABLE users"),
    ("sql_delete", b"; DELETE FROM users"),
    ("sql_comment", b"admin'--"),
    ("sql_semicomment", b"x;\n-- cleanup"),
    ("sql_exec", b"EXEC xp_cmdshell"),
    ("xss_script", b"<script>alert(1)</script>"),
    ("xss_jsuri", b"javascript:alert(1)"),
    ("xss_event", b'<img src=x onerror=alert(1)>'),
    ("xss_iframe_tag", b"<iframe src=evil>"),
    ("xss_object_tag", b"<object data=evil>"),
    ("path_dotdot", b"../../etc/passwd"),
    ("path_enc", b"%2e%2e%2fetc%2fpasswd"),
    ("path_penc", b"%2e%2e/etc/passwd"),
    ("cmd_inj", b"; cat /etc/passwd"),
    ("ssrf_cloud", b"http://169.254.169.254/latest/meta-data/"),
]

# Prefilter hits but regex must NOT match (sound over-approximation: literals
# like "or"/"on" are substrings of innocent words).
FALSE_POSITIVES: list[bytes] = [
    b'{"color": "orange", "session": "none"}',
    b"action=update; note=copper pipes",
]

CLEAN: list[bytes] = [
    b'{"username": "jane", "page": 3}',
    b"/api/v1/users/me?verbose=true",
    b"Mozilla/5.0 AppleWebKit/537.36",
    b"",
]


@pytest.fixture()
def middleware() -> AttackDetectionMiddleware:
    return AttackDetectionMiddleware(app=MagicMock())


def _verdicts(mw: AttackDetectionMiddleware, payload: bytes) -> list[tuple]:
    attacks: list[dict] = []
    mw._check_for_patterns(payload, "request_body", attacks)
    return sorted((a["attack_type"], a["pattern_name"], a["location"]) for a in attacks)


def test_corpus_covers_all_regex_groups() -> None:
    covered = {m.lastgroup for _, p in TRUE_POSITIVES for m in COMBINED_ATTACK_RE.finditer(p)}
    assert covered == set(COMBINED_ATTACK_RE.groupindex.keys()), (
        f"uncovered groups (add literal + corpus entry): {set(COMBINED_ATTACK_RE.groupindex.keys()) - covered}"
    )


@pytest.mark.parametrize("group,payload", TRUE_POSITIVES)
def test_true_positive_detected(group: str, payload: bytes, middleware: AttackDetectionMiddleware) -> None:
    assert ad._attack_prefilter_hit(payload) is True
    assert _verdicts(middleware, payload) != []


@pytest.mark.parametrize("payload", FALSE_POSITIVES + CLEAN)
def test_prefilter_miss_implies_regex_miss(payload: bytes) -> None:
    # The soundness invariant in the negative direction: whenever the
    # prefilter says "no hit", the raw regex must find nothing.
    raw = list(COMBINED_ATTACK_RE.finditer(payload))
    if not ad._attack_prefilter_hit(payload):
        assert raw == []
    else:
        # Over-approximation allowed only for the known FP corpus.
        assert payload in FALSE_POSITIVES
        assert raw == []


def test_automaton_and_fallback_paths_agree(
    middleware: AttackDetectionMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = [p for _, p in TRUE_POSITIVES] + FALSE_POSITIVES + CLEAN
    with_automaton = {bytes(p): _verdicts(middleware, p) for p in corpus}
    monkeypatch.setattr(ad, "_PREFILTER_AUTOMATON", None)
    for payload in corpus:
        assert _verdicts(middleware, payload) == with_automaton[bytes(payload)]
        # The fallback must also respect the no-miss invariant.
        if not ad._attack_prefilter_hit(payload):
            assert list(COMBINED_ATTACK_RE.finditer(payload)) == []


# ---------------------------------------------------------------------------
# C-2: the cmd_inj prefilter gate is the CONJUNCTION, not the bare separator.
#
# (FastAPI_saas-niix.)  The cmd_inj group matches a separator FOLLOWED BY a
# command word: ``(?:;|\|\||&&)\s*(?:cat|ls|id|whoami|ping|curl|wget)\b``.
# A separator on its own is NECESSARY for the group and far from SUFFICIENT,
# and a browser user-agent carries ";" on nearly every request (the
# "Linux; Android 14" platform token).  A prefilter that admits on the bare
# separator therefore ran the full 17-group regex over ordinary traffic.
#
# The two shapes the change has to separate:
#   "action=update; note=copper pipes"     -> a separator with NO command word
#   "Mozilla/5.0 (Linux; Android 14; ...)"  -> a browser user-agent
# Both used to hit the prefilter on the bare ";".  Both must now MISS, and
# the soundness invariant must still hold for everything that does miss.
# ---------------------------------------------------------------------------

_CMD_INJ_SEPARATORS = (b";", b"||", b"&&")
_CMD_INJ_COMMANDS = (b"cat", b"ls", b"id", b"whoami", b"ping", b"curl", b"wget")

PIXEL_USER_AGENT = (
    b"Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    b"(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"
)
TRIPPING_USER_AGENT = b"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ; id"

# Separator without a command word.  None of these may contain another
# group's necessary literal, or the prefilter would hit for a different
# reason and the test would prove nothing.
SEPARATOR_ONLY: list[bytes] = [
    b"action=update; note=copper pipes",
    b"foo; bar",
    b"x;",
    b"a || b",
    b"a && b",
    b";;;",
    b"||",
    b"&&",
    b"nul; null",
    b"one; two; three",
]

# Separator WITH a command word in command position: still a hit.
SEPARATOR_AND_COMMAND: list[bytes] = [
    b"; cat /etc/passwd",
    b"; id",
    b"|| whoami",
    b"&& ls",
    b";ping 10.0.0.1",
    b"action=update; curl http://evil",
]


def _occurrences(haystack: bytes, needle: bytes) -> list[int]:
    """Every index at which `needle` starts in `haystack`."""
    found: list[int] = []
    start = haystack.find(needle)
    while start >= 0:
        found.append(start)
        start = haystack.find(needle, start + 1)
    return found


def _is_word_byte(char: int) -> bool:
    """Is the byte a regex word byte (letters, digits, underscore)?"""
    return chr(char).isalnum() or char == 0x5F


def _cmd_inj_corpus() -> list[bytes]:
    """Separator + command in every shape the group's regex distinguishes.

    The prefixes include a lone "|", so the corpus contains "|||cat": the
    combined regex finds the "||" that starts at index 1, while a
    non-overlapping search for "||" would not.  A hand-written scan would
    miss that verdict; the extracted gate cannot.
    """
    corpus: list[bytes] = []
    for separator in _CMD_INJ_SEPARATORS:
        for command in _CMD_INJ_COMMANDS:
            for gap in (b"", b" ", b"\t", b"\n", b"  ", b"\r\n", b"\x0b"):
                for prefix in (b"", b"x", b"(", b"|"):
                    for suffix in (b"", b" ", b"x", b"/etc/passwd", b";"):
                        corpus.append(prefix + separator + gap + command + suffix)
    return corpus


def test_the_global_literal_set_is_unchanged() -> None:
    # The C-2 change is scoped to the cmd_inj group's OWN gate.  The global
    # literal set stays exactly as it was, with the three separators in it,
    # because they remain NECESSARY for cmd_inj.
    assert len(ad._PREFILTER_LITERALS_BYTES) == 15
    assert set(_CMD_INJ_SEPARATORS) <= ad._PREFILTER_LITERALS_BYTES


def test_the_effective_single_literal_set_is_the_other_twelve() -> None:
    effective = getattr(ad, "_PREFILTER_SINGLE_LITERALS", None)
    assert effective is not None, "C-2 has not landed: there is no effective literal set"
    assert effective == ad._PREFILTER_LITERALS_BYTES - set(_CMD_INJ_SEPARATORS)
    assert len(effective) == 12


def test_a_separator_without_a_command_word_misses_the_prefilter() -> None:
    others = ad._PREFILTER_LITERALS_BYTES - set(_CMD_INJ_SEPARATORS)
    for payload in SEPARATOR_ONLY:
        assert not any(literal in payload for literal in others), payload
        assert ad._attack_prefilter_hit(payload) is False, payload
        assert list(COMBINED_ATTACK_RE.finditer(payload)) == [], payload


def test_a_separator_with_a_command_word_hits_the_prefilter() -> None:
    for payload in SEPARATOR_AND_COMMAND:
        assert ad._attack_prefilter_hit(payload) is True, payload
        assert list(COMBINED_ATTACK_RE.finditer(payload)) != [], payload


def test_the_cmd_inj_gate_agrees_with_the_regex_over_a_generated_corpus() -> None:
    others = ad._PREFILTER_LITERALS_BYTES - set(_CMD_INJ_SEPARATORS)
    for payload in _cmd_inj_corpus():
        # The corpus may not carry another group's literal, so the combined
        # regex can only ever produce cmd_inj's verdict on it.
        assert not any(literal in payload for literal in others), payload
        match = COMBINED_ATTACK_RE.search(payload)
        regex_says_cmd_inj = match is not None and match.group("cmd_inj") is not None
        assert regex_says_cmd_inj == (match is not None), payload
        assert ad._attack_prefilter_hit(payload) is regex_says_cmd_inj, payload
        if not ad._attack_prefilter_hit(payload):
            assert list(COMBINED_ATTACK_RE.finditer(payload)) == [], payload


def test_the_prefilter_miss_still_implies_no_verdict_for_the_generated_corpus() -> None:
    for payload in _cmd_inj_corpus():
        if not ad._attack_prefilter_hit(payload):
            assert COMBINED_ATTACK_RE.search(payload) is None, payload


def test_the_pixel_user_agent_misses_the_prefilter_and_the_regex_clears_it() -> None:
    assert COMBINED_ATTACK_RE.search(PIXEL_USER_AGENT) is None
    assert ad._attack_prefilter_hit(PIXEL_USER_AGENT) is False


def test_the_pixel_user_agent_is_benign_because_of_position_not_absence() -> None:
    # The recorded correction: "Pixel" contains no "id"; "Android" does.  The
    # reason the user-agent is benign is that its "id" is mid-word and no
    # command word follows any ";" -- NOT that the command word is missing.
    assert b"id" in PIXEL_USER_AGENT
    assert b"id" not in b"Pixel"
    for index in _occurrences(PIXEL_USER_AGENT, b"id"):
        assert index > 0
        assert _is_word_byte(PIXEL_USER_AGENT[index - 1]), PIXEL_USER_AGENT
    for separator in _occurrences(PIXEL_USER_AGENT, b";"):
        rest = PIXEL_USER_AGENT[separator + 1 :].lstrip()
        assert not any(rest.startswith(command) for command in _CMD_INJ_COMMANDS), PIXEL_USER_AGENT


def test_a_user_agent_with_a_command_in_command_position_still_trips() -> None:
    match = COMBINED_ATTACK_RE.search(TRIPPING_USER_AGENT)
    assert match is not None
    assert match.group("cmd_inj") is not None
    assert ad._attack_prefilter_hit(TRIPPING_USER_AGENT) is True


def test_the_cmd_inj_group_is_still_covered_by_the_true_positive_corpus() -> None:
    # C-2 narrows the GATE, not the group: cmd_inj must still be detected.
    command_injections = [payload for group, payload in TRUE_POSITIVES if group == "cmd_inj"]
    assert command_injections
    for payload in command_injections:
        assert ad._attack_prefilter_hit(payload) is True, payload
        match = COMBINED_ATTACK_RE.search(payload)
        assert match is not None
        assert match.group("cmd_inj") is not None, payload
