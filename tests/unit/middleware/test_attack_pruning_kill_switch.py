"""Tests for the two A-1 mitigations: the kill switch and the prune counter.

ADR-0009 records the owner's decision on A-1 (necessary-literal branch
pruning) as *accepted with mandatory mitigations*. This file pins the two
that are mechanical controls:

(b) ``ATTACK_DETECTION_BRANCH_PRUNE`` -- a flag on ``AttackDetectionSettings``
    that, when False, disables the prune and runs the FULL
    ``COMBINED_ATTACK_RE`` on every scan.
(c) ``record_attack_subset_compiled()`` -- a counter incremented on every
    prune, because a prune is a detection-soundness judgement and nothing
    else in the system observes it.

A NOTE ON WHAT THE FLAG CAN AND CANNOT DO, because it shapes every
assertion below. The obvious test -- "with the flag off, a payload the
prefilter pruned is now detected" -- CANNOT be written, and the reason is a
property of the production regex rather than of the flag: every byte string
``COMBINED_ATTACK_RE`` can match necessarily contains at least one of the
literals in ``_PREFILTER_LITERALS_BYTES``. That is the entire premise of the
prefilter (see the comment above the literal set), and it was confirmed
empirically over one witness per branch while writing this file: all 17
branches, no exception. So there is no payload for which the prefilter and
the regex disagree, and turning the flag off cannot change any verdict. It
changes only COST. The flag is therefore a performance knob with a security
rationale, and these tests hold it to that truth rather than to the more
dramatic claim it cannot deliver.

What IS worth testing is the thing that can silently rot. If a future change
adds an 18th regex branch and forgets to register its necessary literal, that
branch's payloads are pruned and the detection is LOST -- silently, with no
failing test, no log line, and no performance regression to notice it by,
because skipping the regex is the fast path. ``TestThePrefilterCannotHide...``
is the ratchet for that. It fails loudly when the branch count and the
witness table diverge, which forces whoever adds the branch to confront the
literal question rather than inherit it.

The differential oracle named as P1 in ADR-0009 lives in
``test_attack_pruning_differential.py`` and is the over-strengthened-literal
catcher. It is not duplicated here: this file covers the controls, that file
covers the literal set against the real matcher.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from src.app.core.monitoring.r1_unified_metrics import get_r1_metrics
from src.app.middleware import attack_detection as ad
from src.app.middleware.attack_detection import (
    COMBINED_ATTACK_RE,
    AttackDetectionMiddleware,
)

COUNTER = "r1_attack_subset_compiled_total"

#: Witnesses per regex branch: byte strings that branch actually matches.
#:
#: A BRANCH rather than a single witness, because a branch can have
#: alternatives with DIFFERENT necessary literals. ``cmd_inj`` is the case
#: that forces this: its separator is ``(;|\\|\\||&&)``, so ``||cat`` needs
#: the literal ``||`` while ``;cat`` needs ``;``. With one witness per branch
#: the literals ``||`` and ``&&`` look orphaned -- and they are not; the
#: witness table was simply not covering the branch. That was a wrong
#: expectation in an earlier draft of this file, not a production defect.
#:
#: Exhaustiveness is asserted by
#: ``test_the_witness_table_covers_every_regex_branch``, so adding a branch
#: to ``COMBINED_ATTACK_RE`` without adding a witness here fails the suite.
WITNESSES: dict[str, tuple[bytes, ...]] = {
    "sql_union": (b"union select",),
    "sql_or": (b"or 1=1",),
    "sql_drop": (b"drop table",),
    "sql_delete": (b"delete from",),
    "sql_comment": (b"'--",),
    "sql_semicomment": (b";--",),
    "sql_exec": (b"exec xp_",),
    "xss_script": (b"<script>",),
    "xss_jsuri": (b"javascript:",),
    "xss_event": (b"<img onerror=x>",),
    "xss_iframe_tag": (b"<iframe>",),
    "xss_object_tag": (b"<object>",),
    "path_dotdot": (b"../",),
    "path_enc": (b"%2e%2e%2f",),
    "path_penc": (b"%2e%2e/",),
    "cmd_inj": (b";cat", b"||cat", b"&&cat"),
    "ssrf_cloud": (b"169.254.169.254",),
}

#: A payload carrying none of the literals, so the prefilter prunes it. Used to
#: observe a prune and a non-prune from the outside.
BENIGN_WITHOUT_LITERALS = b"field=value&other=thing"


async def _noop_app(scope: Any, receive: Any, send: Any) -> None:  # noqa: ARG001
    """An ASGI app that does nothing, so the middleware can be constructed."""


class _StubIpRules:
    """Stands in for the Redis-backed rules object.

    ``_check_for_patterns`` never consults the IP rules -- only ``__call__``
    does -- but ``__init__`` constructs the real ``RedisIpRules`` when none is
    passed, and these tests must not reach for Redis to observe a regex.
    """

    async def check_ip_status(self, ip: str) -> bool:  # noqa: ARG002
        return True

    async def ban_ip(self, ip: str, reason: str) -> None:  # noqa: ARG002
        return None


def _middleware() -> AttackDetectionMiddleware:
    return AttackDetectionMiddleware(app=_noop_app, ip_rules=_StubIpRules())  # type: ignore[arg-type]


def _counter() -> float:
    return get_r1_metrics().get_counter(COUNTER)


def _scan(middleware: AttackDetectionMiddleware, content: bytes) -> list[dict[str, Any]]:
    attacks: list[dict[str, Any]] = []
    middleware._check_for_patterns(content, "request_body", attacks)
    return attacks


def _with_flag(monkeypatch: pytest.MonkeyPatch, value: bool) -> None:
    monkeypatch.setattr(ad.settings.attack_detection, "ATTACK_DETECTION_BRANCH_PRUNE", value)


def _source_of(func: Any) -> str:
    """The source of ``func``, for structural assertions on its body.

    Used instead of reading the module file so the assertion stays attached to
    the function it constrains.
    """
    return inspect.getsource(func)


class TestTheKillSwitchDefaultsToPruning:
    """The flag is default-on: pruning is the shipped behaviour."""

    def test_the_setting_defaults_to_true(self) -> None:
        assert ad.settings.attack_detection.ATTACK_DETECTION_BRANCH_PRUNE is True

    def test_a_fresh_middleware_reads_the_flag(self) -> None:
        assert _middleware()._branch_prune is True


class TestTurningTheFlagOffRunsTheFullRegex:
    """Flag off means: no skip, every scan pays for the regex.

    The observable difference from flag on is the COUNTER, not the verdict --
    see the module docstring for why the verdict cannot differ.
    """

    def test_flag_off_does_not_prune_a_literal_free_payload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The regex runs on benign traffic, so nothing is recorded as pruned.

        The flag's whole purpose is to make every request match the regex so a
        suspected-too-narrow literal set cannot hide a detection. A prune
        recorded here would mean the flag is not actually consulted.
        """
        middleware = _middleware()
        _with_flag(monkeypatch, False)
        middleware._branch_prune = False

        before = _counter()
        attacks = _scan(middleware, BENIGN_WITHOUT_LITERALS)

        assert attacks == []
        assert _counter() == before, "the flag was off, so nothing may be counted as pruned"

    def test_flag_on_prunes_the_same_payload(self) -> None:
        """The mirror image, so the test above is measuring the flag and not
        merely observing a payload that is always pruned."""
        middleware = _middleware()
        middleware._branch_prune = True

        before = _counter()
        attacks = _scan(middleware, BENIGN_WITHOUT_LITERALS)

        assert attacks == []
        assert _counter() == before + 1

    def test_flag_off_still_detects_a_literal_bearing_payload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The security-critical direction: disabling the flag must not weaken
        detection. It is a performance control, so it is not allowed to become
        a detection control in either state."""
        middleware = _middleware()
        _with_flag(monkeypatch, False)
        middleware._branch_prune = False

        attacks = _scan(middleware, WITNESSES["sql_union"][0])

        assert [a["pattern_name"] for a in attacks], "flag off must still detect"
        assert any(a["attack_type"] == "sql_injection" for a in attacks)


class TestTheCounterCountsPrunesAndOnlyPrunes:
    """Mitigation (c): the prune is counted because nothing else observes it."""

    def test_a_prune_increments_the_counter(self) -> None:
        middleware = _middleware()
        middleware._branch_prune = True

        before = _counter()
        _scan(middleware, BENIGN_WITHOUT_LITERALS)

        assert _counter() == before + 1

    def test_a_literal_bearing_payload_does_not_increment(self) -> None:
        """A scan that ran the regex is not a prune and must not be counted as
        one -- otherwise the counter measures scan volume rather than pruning
        and the alert threshold would be meaningless."""
        middleware = _middleware()
        middleware._branch_prune = True

        before = _counter()
        _scan(middleware, WITNESSES["cmd_inj"][0])

        assert _counter() == before

    def test_flag_off_never_increments(self, monkeypatch: pytest.MonkeyPatch) -> None:
        middleware = _middleware()
        _with_flag(monkeypatch, False)
        middleware._branch_prune = False

        before = _counter()
        _scan(middleware, BENIGN_WITHOUT_LITERALS)

        assert _counter() == before

    def test_the_counter_name_is_registered(self) -> None:
        """Guards the silent no-op in ``inc_counter``.

        ``inc_counter`` only touches a name present in the counter dict and
        otherwise logs ``unknown_counter`` and returns without incrementing. A
        ``record_*`` function whose name was never registered therefore looks
        correct in review, imports cleanly, and never counts anything -- which
        would make mitigation (c) a comment rather than a control. Behavioural
        rather than reflective: the assertion is that the number moves, not
        that a key exists in a private dict.
        """
        before = _counter()
        get_r1_metrics().inc_counter(COUNTER)

        assert _counter() == before + 1, (
            f"{COUNTER!r} is not a registered counter; inc_counter() discards "
            "unknown names with only a warning, so record_attack_subset_compiled() "
            "would never record anything"
        )


class TestTheFlagIsRestartFree:
    """A security control you need a deploy to disable is not a control you
    can use in an incident, so the flag is refreshed per request like the
    middleware's other three runtime flags."""

    def test_flipping_the_setting_takes_effect_without_a_new_middleware(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        middleware = _middleware()
        middleware._branch_prune = True

        _with_flag(monkeypatch, False)
        # This is the line __call__ runs on every request; invoking it directly
        # is the honest way to test the refresh without standing up a request.
        middleware._branch_prune = ad.settings.attack_detection.ATTACK_DETECTION_BRANCH_PRUNE

        assert middleware._branch_prune is False

    def test_the_refresh_line_is_in_call_not_only_in_init(self) -> None:
        """Structural: the refresh must be in ``__call__``, not only in
        ``__init__``. Without it, flipping the flag at runtime would leave
        every live middleware on its construction-time value -- the exact
        incident-response failure the flag exists to avoid."""
        assert "ATTACK_DETECTION_BRANCH_PRUNE" in _source_of(AttackDetectionMiddleware.__call__)


class TestThePrefilterCannotHideAMatchTheRegexWouldFind:
    """The ratchet for the defect the flag exists to mitigate."""

    def test_the_witness_table_covers_every_regex_branch(self) -> None:
        assert set(WITNESSES) == set(COMBINED_ATTACK_RE.groupindex), (
            "COMBINED_ATTACK_RE gained or lost a branch without the witness "
            f"table following it. New: "
            f"{sorted(set(COMBINED_ATTACK_RE.groupindex) - set(WITNESSES))}; "
            f"stale: {sorted(set(WITNESSES) - set(COMBINED_ATTACK_RE.groupindex))}. "
            "A branch added without a registered necessary literal is silently "
            "pruned, and nothing else in the system would notice."
        )

    @pytest.mark.parametrize("branch", sorted(WITNESSES))
    def test_every_witness_really_matches_the_regex(self, branch: str) -> None:
        """A witness that stopped matching its branch would make the soundness
        assertion below pass vacuously."""
        for witness in WITNESSES[branch]:
            assert COMBINED_ATTACK_RE.search(witness), (
                f"the witness {witness!r} for {branch!r} no longer matches the "
                "regex, so it proves nothing about the prefilter"
            )

    @pytest.mark.parametrize("branch", sorted(WITNESSES))
    def test_every_witness_carries_a_registered_literal(self, branch: str) -> None:
        for witness in WITNESSES[branch]:
            assert ad._attack_prefilter_hit(witness), (
                f"the {branch!r} branch can match {witness!r}, which carries none "
                "of the registered literals, so the prefilter prunes a real "
                "detection. Register a necessary literal for it, or remove the branch."
            )

    def test_every_registered_literal_is_needed_by_some_witness(self) -> None:
        """No dead literal. A literal no branch needs costs a frozenset entry and
        an automaton edge on every scan and buys no soundness, which makes it
        indistinguishable from a typo that was meant to register something."""
        combined = b"".join(w for witnesses in WITNESSES.values() for w in witnesses)
        orphans = sorted(
            literal.decode("latin-1")
            for literal in ad._PREFILTER_LITERALS_BYTES
            if literal not in combined
        )
        assert not orphans, f"literal(s) no branch witness needs: {orphans}"

    def test_the_automaton_sees_every_registered_literal(self) -> None:
        """The prefilter consults ``_PREFILTER_AUTOMATON`` FIRST and only falls
        back to iterating the literal set when that automaton is None. The
        automaton is built ONCE at import, so editing the literal set without
        rebuilding it leaves the prefilter matching the OLD literals: the new
        entry looks live in review and is consulted by nothing. (This was found
        the hard way -- an earlier draft of the fail-direction test below
        patched only the literal set and the patch had no effect at all.)

        Behavioural, because the automaton is an opaque object: every literal
        must be found by the real code path, not merely present in the set.

        Since C-2 (FastAPI_saas-niix) the set the automaton is built from is
        ``_PREFILTER_SINGLE_LITERALS`` -- the global set minus the three
        command-injection separators, which moved into their own conjunction
        gate. The intent of this test is unchanged ("the automaton is in step
        with the set the code consults"); the set it names changed, so it
        names the consulted one.
        """
        for literal in sorted(ad._PREFILTER_SINGLE_LITERALS):
            assert ad._attack_prefilter_hit(literal.lower()), (
                f"{literal!r} is registered but the prefilter does not find it; "
                "the automaton is built at import time, so it is out of step with "
                "the literal set"
            )

    def test_the_separators_are_not_in_the_automaton_but_the_gate_covers_them(self) -> None:
        """C-2: the three separators are NECESSARY for the cmd_inj branch but
        not SUFFICIENT, so they must not admit content on their own.

        This is the shape the change was made for: a browser user-agent
        carries ";" on nearly every request, and a bare separator made the
        full 17-group regex run on all of them. The separator alone must now
        MISS the prefilter, and the separator WITH a command word must still
        HIT -- otherwise the change would have cost a detection.
        """
        for separator in sorted(ad._CMD_INJ_SEPARATORS):
            assert separator not in ad._PREFILTER_SINGLE_LITERALS, (
                f"{separator!r} is in the automaton's set, so it admits content on "
                "its own -- that is the behaviour C-2 removed"
            )
            assert not ad._attack_prefilter_hit(separator), (
                f"a bare {separator!r} must not admit content: it is necessary for "
                "the cmd_inj branch but not sufficient"
            )
            assert ad._attack_prefilter_hit(separator + b" cat"), (
                f"{separator!r} followed by a command word must still be admitted, "
                "or the cmd_inj branch has lost its detection"
            )

    def test_the_effective_set_is_the_global_set_minus_the_separators(self) -> None:
        """The effective set is DERIVED from the global set at import. Nothing
        re-derives it later, so a future edit that adds a literal to the global
        set without the effective set following would leave that literal
        registered but not consulted -- the same "looks live in review and is
        consulted by nothing" failure the automaton test above exists to
        catch, one level up.

        The degraded form is the fail-safe: if the conjunction gate could not
        be built, the effective set is the WHOLE global set, because a gate
        that cannot be built must not be allowed to prune anything.
        """
        if ad._CMD_INJ_GATE is None:
            assert ad._PREFILTER_SINGLE_LITERALS == ad._PREFILTER_LITERALS_BYTES
        else:
            assert ad._PREFILTER_SINGLE_LITERALS == (
                ad._PREFILTER_LITERALS_BYTES - ad._CMD_INJ_SEPARATORS
            )

    @pytest.mark.parametrize(
        ("kept_literal", "lost_witness"),
        [
            (b"<", b"javascript:"),
            (b";", b"<script>"),
            (b"union", b"exec xp_cmdshell"),
        ],
    )
    def test_dropping_a_literal_loses_a_real_detection(
        self,
        monkeypatch: pytest.MonkeyPatch,
        kept_literal: bytes,
        lost_witness: bytes,
    ) -> None:
        """The fail direction, demonstrated rather than assumed.

        Reduce the literal set to one entry and a payload the real regex matches
        becomes invisible to the prefilter -- a LOST detection, with no error and
        no slowdown. This is the mechanism the invariant above exists to
        prevent, so if this test ever stops failing, the invariant has stopped
        meaning anything.

        Both sets AND the automaton are patched. The automaton is the primary
        code path, so patching only the sets would leave the original literals
        in force and this test would prove nothing. Since C-2 the code consults
        TWO sets -- the global one and the effective one the automaton is built
        from -- so both are patched, or the reduction would not reach the code.

        The third case is the sql_exec branch, not cmd_inj: after C-2 the
        cmd_inj branch is protected by its own conjunction gate rather than by
        a literal, so dropping a literal does NOT lose it. That branch carries
        its own ratchet below.
        """
        monkeypatch.setattr(ad, "_PREFILTER_LITERALS_BYTES", frozenset({kept_literal}))
        monkeypatch.setattr(ad, "_PREFILTER_SINGLE_LITERALS", frozenset({kept_literal}))
        monkeypatch.setattr(ad, "_PREFILTER_AUTOMATON", None)

        assert COMBINED_ATTACK_RE.search(lost_witness), "the regex still detects this payload"
        assert not ad._attack_prefilter_hit(lost_witness), (
            "the prefilter still sees this payload, so dropping a literal did not "
            "demonstrate the failure mode this test exists to show"
        )

    def test_the_cmd_inj_branch_is_protected_by_its_gate_not_by_a_literal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """C-2 moved the cmd_inj gate out of the literal set and into a
        conjunction, so this branch is no longer protected by a literal. It has
        its own protection, and this is the ratchet for IT: reduce the literal
        set to nothing at all and the cmd_inj detection survives.

        The previous ratchet case claimed that dropping a literal loses the
        cmd_inj detection. After C-2 that is FALSE -- leaving it in place
        would have been a test asserting the wrong thing -- so the branch that
        stopped being literal-protected got its own ratchet instead, and the
        case that was removed was replaced with one that is still true.
        """
        monkeypatch.setattr(ad, "_PREFILTER_LITERALS_BYTES", frozenset())
        monkeypatch.setattr(ad, "_PREFILTER_SINGLE_LITERALS", frozenset())
        monkeypatch.setattr(ad, "_PREFILTER_AUTOMATON", None)

        assert COMBINED_ATTACK_RE.search(b";cat"), "the regex still detects this payload"
        assert ad._attack_prefilter_hit(b";cat"), (
            "the cmd_inj branch is protected by the conjunction gate, not by a "
            "literal, so emptying the literal set must not lose it"
        )
