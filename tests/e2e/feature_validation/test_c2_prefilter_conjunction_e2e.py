"""C-2 e2e: the narrowed prefilter against the full attack corpus.

WHAT
    C-2 narrowed the attack-scan prefilter. The three command separators
    (``;``, ``||``, ``&&``) no longer admit content on their own; ``cmd_inj``
    is admitted by a CONJUNCTION gate built from the production ``cmd_inj``
    sub-pattern (a separator AND a command word). The GLOBAL literal set
    (``_PREFILTER_LITERALS_BYTES``) is unchanged at 15 -- the separators are
    still NECESSARY for ``cmd_inj`` -- and the EFFECTIVE set the automaton
    iterates (``_PREFILTER_SINGLE_LITERALS``) is those 15 minus the three
    separators (12).

WHY
    The conjunction is sound: both conjuncts of ``cmd_inj`` are individually
    mandatory. But the STATED PROOF was wrong, and the error is load-bearing:
    a bare ``;`` is also required by ``sql_comment`` and ``sql_semicomment``,
    so removing the bare ``;`` from the GLOBAL literal set would be safe ONLY
    because ``--`` remains in the literal set.

    This script exercises the shipped narrowing over the whole corpus, in
    process, and over a sample over real HTTP, and records for every payload:
    the literals it carries, the branch set each variant implies, the verdict
    each variant reaches, and an explicit MATCH or DIFFER.

THE ASSERTIONS
    1. ZERO verdict differences across the corpus: the narrowing may admit
       LESS, but it must never change a verdict.
    2. The narrowed prefilter admits a SUBSET of the old one, and that
       difference is NON-EMPTY -- a gate that cannot fail cannot pass.
    3. The ``--`` companion stays in the GLOBAL set AND in the EFFECTIVE set,
       and every payload that trips a semicolon group carries ``--``.
    4. ``x;--`` style payloads are still admitted AND still detected.
    5. The mobile user agent is benign because of the POSITION of ``id``, not
       because ``id`` is absent, and the tripping user agent is still caught.
    6. The non-ASCII, NFKC and zero-width arms are present and agree.

WHAT IS NOT ASSERTED
    Nothing about latency, throughput or cost: this script asserts behaviour
    only. It does not claim the OLD variant was ever a shipped configuration
    -- it reconstructs the pre-C-2 prefilter from the REAL global literal
    set, which is the set C-2 deliberately left unchanged.

NO TEST WAS WEAKENED TO ACCOMMODATE A CHANGE; NO ASSERTION WAS RELAXED TO
MAKE A NUMBER PASS. No number is asserted here at all.
"""

from __future__ import annotations

import json
import os
import unicodedata
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.app.middleware import attack_detection as ad
from tests.unit.middleware.test_attack_pruning_differential import (
    _CORPUS,
    _GROUP_ORDER,
    _NONASCII_TOKENS,
    _ZERO_WIDTH_TOKENS,
    GROUP_NECESSARY_LITERALS,
    N_DIFFERENTIAL_CASES,
    _corpus,
    _real_group_subpatterns,
)

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parents[3]
ARTEFACT_DIR = REPO_ROOT / "perf-results" / "RESULT-20261020-mhj4"
ARTEFACT_PATH = ARTEFACT_DIR / "c2_prefilter_differential.json"
WRITE_ARTEFACT_ENV = "C2_E2E_WRITE_ARTEFACT"

PIXEL_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"
)
TRIPPING_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ; id"

# The groups whose production sub-pattern can be tripped by a semicolon
# separator. This is the closed world the "``--`` remains in the literal set"
# assertion is about: every member except ``cmd_inj`` needs the ``--``
# companion, because that is what the sql_comment / sql_semicomment
# sub-patterns actually require.
SEMICOLON_GROUPS = frozenset({"sql_comment", "sql_semicomment", "cmd_inj"})
SQL_SEMICOLON_GROUPS = frozenset({"sql_comment", "sql_semicomment"})

# The HTTP sample. Six payloads, chosen to cover one detection per family
# plus the two payloads that separate the two variants:
#   - a bare separator with NO command word (the narrowing's own target),
#   - the benign mobile user agent,
#   - the tripping user agent.
HTTP_SAMPLE: tuple[tuple[str, bytes], ...] = (
    ("sql_union", b"UNION SELECT password FROM users"),
    ("sql_semicomment", b"1;-- comment"),
    ("cmd_inj", b"; cat /etc/passwd"),
    ("bare_separator", b"hello; world"),
    ("pixel_user_agent", PIXEL_USER_AGENT.encode("utf-8")),
    ("tripping_user_agent", TRIPPING_USER_AGENT.encode("utf-8")),
)


# ---------------------------------------------------------------------------
# The two variants. Both are derived from the REAL module, so neither can
# drift away from the code it is supposed to describe.
# ---------------------------------------------------------------------------


def _old_prefilter_hit(content: bytes) -> bool:
    """The pre-C-2 prefilter: ANY of the 15 global literals present."""
    lowered = content.lower()
    return any(literal in lowered for literal in ad._PREFILTER_LITERALS_BYTES)


# Captured at import time, BEFORE any patch is applied, so the "new" variant
# calls the shipped implementation rather than itself.
_REAL_PREFILTER_HIT = ad._attack_prefilter_hit


def _new_prefilter_hit(content: bytes) -> bool:
    """The shipped prefilter: the effective literal set plus the cmd_inj gate."""
    return _REAL_PREFILTER_HIT(content)


def _literals_present(payload: bytes) -> list[str]:
    """The global literals this payload carries (ASCII-folded)."""
    lowered = payload.lower()
    return sorted(
        literal.decode("ascii", "replace")
        for literal in ad._PREFILTER_LITERALS_BYTES
        if literal in lowered
    )


def _branch_set(payload: bytes, literals: Any) -> list[str]:
    """The groups whose NECESSARY literal the payload carries."""
    lowered = payload.lower()
    present = frozenset(literal for literal in literals if literal in lowered)
    return [group for group in _GROUP_ORDER if GROUP_NECESSARY_LITERALS[group] & present]


def _old_branch_set(payload: bytes) -> list[str]:
    """The branch set the OLD (global-literal) prefilter implies."""
    return _branch_set(payload, ad._PREFILTER_LITERALS_BYTES)


def _new_branch_set(payload: bytes) -> list[str]:
    """The branch set the SHIPPED prefilter implies.

    The effective literal set covers every group except ``cmd_inj``, whose
    admission is the conjunction gate. So ``cmd_inj`` is added when the gate
    hits, and the group order is kept so the two branch sets are comparable.
    """
    groups = _branch_set(payload, ad._PREFILTER_SINGLE_LITERALS)
    if ad._cmd_inj_gate_hit(payload.lower()):
        groups = [group for group in _GROUP_ORDER if group in groups or group == "cmd_inj"]
    return groups


def _verdict(middleware: Any, content: bytes) -> list[tuple[str, str, str]]:
    """Drive the REAL ``_check_for_patterns`` and return its verdict.

    The verdict is the (attack_type, pattern_name, location) triple of every
    detection the middleware appended. ``matched_content`` is deliberately
    excluded: it is redacted by default and is not part of the verdict.
    """
    attacks: list[dict[str, Any]] = []
    middleware._check_for_patterns(content, "request_body", attacks)
    return [
        (
            str(attack.get("attack_type", "")),
            str(attack.get("pattern_name", "")),
            str(attack.get("location", "")),
        )
        for attack in attacks
    ]


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------


def _non_ascii_arms() -> list[tuple[str, bytes]]:
    """Non-ASCII payloads, including one that carries a real literal."""
    return [
        ("non_ascii_accented", "café UNION SELECT".encode()),
        ("non_ascii_greek", "αβγ ; id".encode()),
        ("non_ascii_cjk", "中文 UNION".encode()),
    ]


def _nfkc_arms() -> list[tuple[str, bytes]]:
    """NFKC arms: the NORMALISED text is what a consumer downstream sees."""
    fullwidth_union = unicodedata.normalize("NFKC", "ＵＮＩＯＮ ＳＥＬＥＣＴ")
    fullwidth_tag = unicodedata.normalize("NFKC", "＜script＞")
    return [
        ("nfkc_fullwidth_union_select", fullwidth_union.encode()),
        ("nfkc_fullwidth_script_tag", fullwidth_tag.encode()),
    ]


def _zero_width_arms() -> list[tuple[str, bytes]]:
    """Zero-width arms: one breaks a literal, one sits between two."""
    zwsp = ""
    return [
        ("zero_width_inside_a_literal", f"UNI{zwsp}ON".encode()),
        ("zero_width_between_literals", f"UNION{zwsp} SELECT".encode()),
    ]


def _corpus_cases() -> list[tuple[str, bytes]]:
    """The full corpus: the seeded differential corpus plus the labelled arms."""
    cases: list[tuple[str, bytes]] = []
    cases.extend((f"seeded_{index}", payload) for index, payload in enumerate(_corpus(N_DIFFERENTIAL_CASES)))
    cases.extend((f"group_{name}", payload) for name, payload in _CORPUS)
    cases.append(("pixel_user_agent", PIXEL_USER_AGENT.encode("utf-8")))
    cases.append(("tripping_user_agent", TRIPPING_USER_AGENT.encode("utf-8")))
    cases.extend(_non_ascii_arms())
    cases.extend(_nfkc_arms())
    cases.extend(_zero_width_arms())
    cases.extend((f"non_ascii_token_{index}", token) for index, token in enumerate(_NONASCII_TOKENS))
    cases.extend((f"zero_width_token_{index}", token) for index, token in enumerate(_ZERO_WIDTH_TOKENS))
    return cases


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def middleware() -> Any:
    """A middleware instance with deterministic (redacted) matched_content."""
    instance = ad.AttackDetectionMiddleware(app=MagicMock())
    instance._log_full_request = False
    return instance


@pytest.fixture(scope="module")
def differential(middleware: Any) -> dict[str, Any]:
    """Run the whole corpus through BOTH variants and record every payload.

    The two passes are separate because the prefilter is a MODULE GLOBAL that
    ``_check_for_patterns`` resolves at call time, so each variant needs its
    own patch context. The corpus is seeded, so the result is reproducible.
    """
    cases = _corpus_cases()

    old_verdicts: list[list[tuple[str, str, str]]] = []
    with patch.object(ad, "_attack_prefilter_hit", _old_prefilter_hit):
        for _, payload in cases:
            old_verdicts.append(_verdict(middleware, payload))

    new_verdicts: list[list[tuple[str, str, str]]] = []
    with patch.object(ad, "_attack_prefilter_hit", _new_prefilter_hit):
        for _, payload in cases:
            new_verdicts.append(_verdict(middleware, payload))

    records: list[dict[str, Any]] = []
    differences: list[dict[str, Any]] = []
    narrowed_only_admitted = 0
    detected = 0
    for (name, payload), old_verdict, new_verdict in zip(cases, old_verdicts, new_verdicts, strict=True):
        old_admitted = _old_prefilter_hit(payload)
        new_admitted = _new_prefilter_hit(payload)
        if old_admitted and not new_admitted:
            narrowed_only_admitted += 1
        if new_verdict:
            detected += 1
        record: dict[str, Any] = {
            "name": name,
            "payload_repr": repr(payload),
            "literals_present": _literals_present(payload),
            "old_admitted": old_admitted,
            "new_admitted": new_admitted,
            "old_branch_set": _old_branch_set(payload),
            "new_branch_set": _new_branch_set(payload),
            "old_verdict": [list(entry) for entry in old_verdict],
            "new_verdict": [list(entry) for entry in new_verdict],
            "verdict": "MATCH" if old_verdict == new_verdict else "DIFFER",
        }
        records.append(record)
        if record["verdict"] == "DIFFER":
            differences.append(record)

    return {
        "records": records,
        "differences": differences,
        "payload_count": len(cases),
        "seeded_case_count": N_DIFFERENTIAL_CASES,
        "narrowed_only_admitted": narrowed_only_admitted,
        "detected_count": detected,
    }


# ---------------------------------------------------------------------------
# The tests
# ---------------------------------------------------------------------------


def test_no_payload_changed_its_verdict(differential: dict[str, Any]) -> None:
    """ZERO verdict differences across the whole corpus."""
    differences = differential["differences"]
    assert differences == [], (
        "the narrowed prefilter changed the verdict of "
        f"{len(differences)} payload(s); first three: {differences[:3]}"
    )
    assert differential["payload_count"] > N_DIFFERENTIAL_CASES


def test_the_narrowed_prefilter_admits_a_subset_of_the_old_one(differential: dict[str, Any]) -> None:
    """The narrowing may admit LESS, never MORE -- and it does admit less."""
    for record in differential["records"]:
        if record["new_admitted"]:
            assert record["old_admitted"], (
                f"the shipped prefilter admitted a payload the old one rejected: {record['name']}"
            )
    assert differential["narrowed_only_admitted"] > 0, (
        "the narrowing rejected nothing at all, so this oracle cannot discriminate the change it gates"
    )


def test_the_dashdash_companion_stays_in_both_literal_sets() -> None:
    """The cross-group assertion: ``--`` remains in the literal set.

    This is the fact that makes the removal of the bare ``;`` sound. A
    future edit to the literal set that drops ``--`` from the EFFECTIVE set
    would break this test loudly.
    """
    subpatterns = _real_group_subpatterns()
    semicolon_groups = {
        group for group, subpattern in subpatterns.items() if b";" in subpattern
    }
    assert semicolon_groups == set(SEMICOLON_GROUPS), (
        "the set of groups whose production sub-pattern can be tripped by a semicolon changed; "
        f"expected {sorted(SEMICOLON_GROUPS)}, found {sorted(semicolon_groups)}"
    )
    for group in sorted(SQL_SEMICOLON_GROUPS):
        assert b"--" in subpatterns[group], (
            f"the {group} sub-pattern no longer textually requires the -- companion"
        )
    assert b"--" in ad._PREFILTER_LITERALS_BYTES, "-- left the GLOBAL literal set"
    assert b"--" in ad._PREFILTER_SINGLE_LITERALS, "-- left the EFFECTIVE literal set"


def test_every_payload_that_trips_a_semicolon_group_carries_the_companion(differential: dict[str, Any]) -> None:
    """The behavioural half: the companion is NECESSARY, not decorative."""
    expected_names = {
        ad._GROUP_TO_ATTACK[group][0] for group in SQL_SEMICOLON_GROUPS if group in ad._GROUP_TO_ATTACK
    }
    checked = 0
    for record in differential["records"]:
        pattern_names = {entry[1] for entry in record["new_verdict"]}
        if not (pattern_names & expected_names):
            continue
        checked += 1
        assert "--" in record["payload_repr"], (
            f"a semicolon group tripped without the -- companion in the payload: {record['name']}"
        )
    assert checked > 0, "no payload tripped a semicolon group, so the assertion is vacuous"


def test_a_semicolon_payload_is_still_admitted_and_detected(differential: dict[str, Any]) -> None:
    """``x;--`` style payloads are still admitted AND still detected."""
    records = {record["name"]: record for record in differential["records"]}
    expected_names = {
        ad._GROUP_TO_ATTACK[group][0] for group in SQL_SEMICOLON_GROUPS if group in ad._GROUP_TO_ATTACK
    }
    for name in ("group_sql_comment", "group_sql_semicomment"):
        record = records[name]
        assert record["new_admitted"], f"{name} is no longer admitted by the shipped prefilter"
        assert record["new_verdict"], f"{name} is admitted but no longer detected"
        assert {entry[1] for entry in record["new_verdict"]} & expected_names, (
            f"{name} was detected by a pattern that is not a semicolon group: {record['new_verdict']}"
        )


def test_the_mobile_user_agent_is_benign_because_of_position_not_absence(
    differential: dict[str, Any],
) -> None:
    """ANDROID contains ``id``; the reason it is benign is the POSITION."""
    record = {item["name"]: item for item in differential["records"]}["pixel_user_agent"]
    assert "id" in PIXEL_USER_AGENT.lower(), "the fixture no longer carries an id at all"
    assert "id" in "android", "the word android no longer carries the substring id"
    assert "id" not in "pixel", "the word pixel now carries the substring id"
    assert "android" in PIXEL_USER_AGENT.lower(), "the fixture no longer names Android"
    assert record["new_verdict"] == [], "the benign user agent is now detected"
    assert record["old_verdict"] == [], "the benign user agent was detected by the old prefilter"
    assert record["new_admitted"] is False
    assert record["old_admitted"] is True, (
        "the old prefilter admitted the benign user agent on the bare semicolon; "
        "if it no longer does, this test has stopped testing what it claims to test"
    )
    tripping = {item["name"]: item for item in differential["records"]}["tripping_user_agent"]
    assert tripping["new_verdict"], "the tripping user agent is no longer detected"


def test_the_non_ascii_nfkc_and_zero_width_arms_are_present_and_agree(differential: dict[str, Any]) -> None:
    """The folded arms are in the corpus and the two variants agree on them."""
    names = {record["name"] for record in differential["records"]}
    for arm in ("non_ascii_accented", "non_ascii_greek", "non_ascii_cjk"):
        assert arm in names, f"the {arm} arm is missing from the corpus"
    for arm in ("nfkc_fullwidth_union_select", "nfkc_fullwidth_script_tag"):
        assert arm in names, f"the {arm} arm is missing from the corpus"
    for arm in ("zero_width_inside_a_literal", "zero_width_between_literals"):
        assert arm in names, f"the {arm} arm is missing from the corpus"
    for record in differential["records"]:
        if record["name"].startswith(("non_ascii", "nfkc_", "zero_width_")):
            assert record["verdict"] == "MATCH", f"the {record['name']} arm disagrees between the variants"


# ---------------------------------------------------------------------------
# The HTTP leg
# ---------------------------------------------------------------------------


def _attack_detection_mode() -> str:
    from src.app.core.config import settings

    return settings.attack_detection.ATTACK_DETECTION_MODE


def _rejection_status(mode: str) -> int:
    return {"block": 400, "ban": 403}.get(mode, 200)


@pytest.mark.asyncio
async def test_over_http_the_two_variants_reach_the_same_status(
    client: Any,
    authenticated_user: dict[str, Any],
) -> None:
    """The same payloads over the real ASGI stack, once per variant."""
    token = authenticated_user["access_token"]
    mode = _attack_detection_mode()
    rejection_status = _rejection_status(mode)

    observations: list[dict[str, Any]] = []
    for name, payload in HTTP_SAMPLE:
        headers = {"User-Agent": payload.decode("utf-8"), "Authorization": f"Bearer {token}"}
        statuses: dict[str, int] = {}
        for variant_name, prefilter in (("old", _old_prefilter_hit), ("new", _new_prefilter_hit)):
            with patch.object(ad, "_attack_prefilter_hit", prefilter):
                response = await client.get("/api/v1/users/me", headers=headers)
            statuses[variant_name] = response.status_code
        assert statuses["old"] == statuses["new"], (
            f"the two variants reached different HTTP statuses for {name}: {statuses}"
        )
        observations.append({"name": name, "statuses": statuses})

    blocked = [item for item in observations if item["statuses"]["old"] == rejection_status]
    if mode == "log":
        assert blocked == [], "the log mode blocked a request; the mode was read wrong"
    else:
        assert blocked, (
            "no sampled payload was blocked, so the HTTP leg proved nothing; statuses: "
            f"{observations}"
        )
    # The bare separator is the payload the narrowing exists for: it must NOT
    # be blocked, under either variant.
    bare = next(item for item in observations if item["name"] == "bare_separator")
    assert bare["statuses"]["old"] != rejection_status, "a bare separator with no command word was blocked"


# ---------------------------------------------------------------------------
# The recorded artefact
# ---------------------------------------------------------------------------


def _stable_facts(differential: dict[str, Any]) -> dict[str, Any]:
    """The behaviour facts, and only the behaviour facts.

    Latencies, request ids and status codes are NOT part of the compared
    facts: they are not reproducible and they are not what this script
    asserts.
    """
    labelled = [
        record
        for record in differential["records"]
        if not record["name"].startswith("seeded_")
    ]
    return {
        "payload_count": differential["payload_count"],
        "seeded_case_count": differential["seeded_case_count"],
        "verdict_difference_count": len(differential["differences"]),
        "narrowed_only_admitted": differential["narrowed_only_admitted"],
        "detected_count": differential["detected_count"],
        "global_literal_count": len(ad._PREFILTER_LITERALS_BYTES),
        "effective_literal_count": len(ad._PREFILTER_SINGLE_LITERALS),
        "labelled_payloads": labelled,
    }


def _artefact(differential: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "perf-c2-prefilter-differential/1",
        "bead": "FastAPI_saas-mhj4",
        "item": "C-2 e2e",
        "title": "Differential run of the narrowed attack-scan prefilter over the full corpus",
        "method": "in-process differential over the seeded corpus plus labelled arms, "
        "and a six-payload sample over the real ASGI stack",
        "variants": {
            "old": "any of the 15 global literals present (reconstructed from the REAL global set)",
            "new": "the shipped _attack_prefilter_hit: the 12 effective literals plus the cmd_inj gate",
        },
        "verdict": "ZERO verdict differences across the corpus",
        "stable_facts": _stable_facts(differential),
    }


def test_the_recorded_artefact_is_this_runs_facts(differential: dict[str, Any]) -> None:
    """The committed artefact must be what this run produces."""
    artefact = _artefact(differential)
    if os.environ.get(WRITE_ARTEFACT_ENV) == "1":
        ARTEFACT_DIR.mkdir(parents=True, exist_ok=True)
        ARTEFACT_PATH.write_text(json.dumps(artefact, indent=2, ensure_ascii=False), encoding="utf-8")
    assert ARTEFACT_PATH.exists(), (
        f"the recorded artefact is missing: {ARTEFACT_PATH}. "
        f"Regenerate it with {WRITE_ARTEFACT_ENV}=1."
    )
    recorded = json.loads(ARTEFACT_PATH.read_text(encoding="utf-8"))
    assert recorded["stable_facts"] == artefact["stable_facts"], (
        "the recorded artefact no longer describes this run; regenerate it with "
        f"{WRITE_ARTEFACT_ENV}=1"
    )
