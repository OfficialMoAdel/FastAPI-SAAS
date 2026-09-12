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
