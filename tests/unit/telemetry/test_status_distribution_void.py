"""P-6: the non-2xx => VOID assertion must FIRE, and the two counters must agree.

Named by the bead, so the path is the bead's. Every test here is a NEGATIVE test
first: the detector has to be shown failing a phase, not merely to exist. A gate
nobody has watched reject anything is a gate that might not be a gate, and this
project has shipped two of those already - an import-linter that sat inert, and a
worker that ran on an import side effect.

The direction of the bias is the reason the rule exists and it is asserted rather
than described: a rejection is fast, so admitting a phase that contains one drags
every percentile DOWN and every throughput figure UP - an error that favours the
false conclusion.
"""

from __future__ import annotations

import pytest

from src.benchmarks.status_gate import (
    PhaseVoided,
    admit_phase,
    admit_phases,
    admitted_phases,
    assert_counters_agree,
    assert_gate_a_success,
    classify_status,
    percentiles_over_admitted,
    void_phases,
)

PHASES = ("cold", "hold", "warm")


def _clean(*phases: str) -> dict[str, dict[str, int]]:
    return {phase: {"2xx": 100, "3xx": 5} for phase in (phases or PHASES)}


class TestPhaseVoidedFires:
    def test_a_phase_with_a_429_is_void(self) -> None:
        """Unified plan 4.5 names 429 verbatim."""
        with pytest.raises(PhaseVoided, match="VOID"):
            admit_phase("warm", {"2xx": 500, "4xx": 3}, codes={"429": 3})

    def test_a_phase_with_a_401_is_void(self) -> None:
        with pytest.raises(PhaseVoided, match="VOID"):
            admit_phase("warm", {"2xx": 500, "4xx": 3}, codes={"401": 3})

    def test_a_phase_with_a_5xx_is_void(self) -> None:
        """A server error is not a fast rejection and would flatter the same way."""
        with pytest.raises(PhaseVoided, match="VOID"):
            admit_phase("warm", {"2xx": 500, "5xx": 2})

    def test_a_429_injected_mid_phase_voids_that_phase_only(self) -> None:
        """The pack's exact failure: auto-ban voided 3 of 6 phases on one host."""
        verdicts = admit_phases(
            {"cold": {"2xx": 300}, "hold": {"2xx": 300, "4xx": 1}, "warm": {"2xx": 300}},
            codes={"hold": {"429": 1}},
        )
        assert void_phases(verdicts) == ["hold"]
        assert admitted_phases(verdicts) == ["cold", "warm"]

    def test_one_429_is_enough(self) -> None:
        """Not a threshold. One rejection means the phase is not a served phase."""
        with pytest.raises(PhaseVoided):
            admit_phase("warm", {"2xx": 10_000, "4xx": 1})

    def test_a_zero_count_class_does_not_void(self) -> None:
        """A class present with count 0 is a key, not an event."""
        assert admit_phase("warm", {"2xx": 100, "4xx": 0, "5xx": 0}).admitted is True


class TestTheBiasIsSignedAndStated:
    def test_a_void_phase_carries_no_percentiles_at_all(self) -> None:
        """Not "percentiles marked invalid" - nothing there to quote."""
        verdicts = admit_phases(
            {"cold": {"2xx": 300}, "warm": {"2xx": 300, "4xx": 2}},
            percentiles={"cold": {"p50": 4.1}, "warm": {"p50": 3.9}},
            codes={"warm": {"429": 2}},
        )
        voided = next(v for v in verdicts if not v.admitted)
        admitted = next(v for v in verdicts if v.admitted)
        assert voided.percentiles is None
        assert admitted.percentiles == {"p50": 4.1}

    def test_aggregating_over_a_void_set_is_refused_not_filtered(self) -> None:
        """A quietly-filtered number would turn a mistake into a plausible figure."""
        verdicts = admit_phases(
            {"cold": {"2xx": 300}, "warm": {"2xx": 300, "4xx": 2}},
            percentiles={"cold": {"p50": 4.1}, "warm": {"p50": 3.9}},
            codes={"warm": {"429": 2}},
        )
        with pytest.raises(PhaseVoided, match="cannot aggregate"):
            percentiles_over_admitted(verdicts)

    def test_aggregating_over_only_admitted_phases_succeeds(self) -> None:
        verdicts = admit_phases(
            {"cold": {"2xx": 300}, "warm": {"2xx": 300, "4xx": 2}},
            percentiles={"cold": {"p50": 4.1}, "warm": {"p50": 3.9}},
            codes={"warm": {"429": 2}},
        )
        merged = percentiles_over_admitted([v for v in verdicts if v.admitted])
        assert merged == {"p50": 4.1}

    def test_3xx_is_served_not_void(self) -> None:
        """A redirect is a served response; excluding it would void healthy phases."""
        assert admit_phase("warm", {"2xx": 100, "3xx": 40}).admitted is True


class TestArtefactRecordsCounts:
    def test_the_export_carries_counts_not_a_boolean(self) -> None:
        """Acceptance criterion (a). A boolean cannot be examined after the fact."""
        verdicts = admit_phases(
            {"warm": {"2xx": 400, "4xx": 12}},
            codes={"warm": {"429": 8, "401": 4}},
        )
        export = verdicts[0].export()
        assert export["admitted"] is False
        assert export["distribution"] == {"2xx": 400, "4xx": 12}

    def test_the_reasons_separate_429_from_401(self) -> None:
        """Throttled and unauthenticated are different diagnoses, same class."""
        verdicts = admit_phases(
            {"warm": {"2xx": 400, "4xx": 12}},
            codes={"warm": {"429": 8, "401": 4}},
        )
        reason = verdicts[0].reasons[0]
        assert "429" in reason and "401" in reason

    def test_every_offending_class_is_named_not_just_the_first(self) -> None:
        """A run that voided on 4xx and also carried 5xx has two problems."""
        verdicts = admit_phases({"warm": {"2xx": 400, "4xx": 12, "5xx": 3}})
        assert len(verdicts[0].reasons) == 2


class TestGateA:
    def test_a_clean_run_passes(self) -> None:
        assert_gate_a_success(admit_phases(_clean()), expected_phases=PHASES)

    def test_any_void_phase_fails_gate_a(self) -> None:
        verdicts = admit_phases(
            {"cold": {"2xx": 300}, "hold": {"2xx": 300}, "warm": {"2xx": 300, "4xx": 1}},
            codes={"warm": {"429": 1}},
        )
        with pytest.raises(PhaseVoided, match="voided"):
            assert_gate_a_success(verdicts, expected_phases=PHASES)

    def test_a_missing_phase_fails_gate_a(self) -> None:
        """A phase that was not measured is not a phase that passed."""
        with pytest.raises(PhaseVoided, match="absent"):
            assert_gate_a_success(admit_phases({"cold": {"2xx": 9}}), expected_phases=PHASES)


class TestCounterAgreement:
    def test_the_two_counters_agree_within_tolerance(self) -> None:
        result = assert_counters_agree({"2xx": 500}, {"2xx": 498})
        assert result["agree"] is True

    def test_a_large_disagreement_voids_the_run(self) -> None:
        """H-7's missing catch_response is the precedent this guards."""
        with pytest.raises(PhaseVoided, match="disagree"):
            assert_counters_agree({"2xx": 500}, {"2xx": 100})

    def test_two_empty_counters_agree_vacuously(self) -> None:
        """A run that measured nothing has not disagreed about anything."""
        assert assert_counters_agree({}, {})["agree"] is True

    def test_a_relative_tolerance_is_used_not_an_absolute_one(self) -> None:
        """The two count over different populations, so an absolute bar is wrong."""
        small = assert_counters_agree({"2xx": 100}, {"2xx": 99})
        assert small["agree"] is True


class TestClassification:
    def test_status_codes_bucket_into_the_classes_the_rule_names(self) -> None:
        assert classify_status(200) == "2xx"
        assert classify_status(204) == "2xx"
        assert classify_status(301) == "3xx"
        assert classify_status(401) == "4xx"
        assert classify_status(429) == "4xx"
        assert classify_status(503) == "5xx"

    def test_a_1xx_is_not_mistaken_for_a_success(self) -> None:
        """An informational response is not a served one.

        ``admit_phase`` raises rather than returning a negative verdict, so the
        property is the raise - a 1xx must not slip through as success just
        because it is not 4xx or 5xx.
        """
        assert classify_status(100) == "1xx"
        with pytest.raises(PhaseVoided, match="1xx"):
            admit_phase("warm", {"1xx": 1})
