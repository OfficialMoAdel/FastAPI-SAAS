"""Request-level attribution of the EmailStr response-validation cost.

Bead: FastAPI_saas-2h3n (M-4). The dispute is whether ``EmailStr`` validation
on the ``/users/me`` RESPONSE model costs 974 us/req (bunny_pi, cProfile) or
75-150 us (bunny_op, a microbenchmark), 6-13x apart, with both denominators
unsound.

P-17 (``yi4n``) already answered something -- ``perf-results/RESULT-20261013-
emailstr/emailstr_cost.json`` says "RESOLVED: NEITHER, 34.3 us" -- but it did
not answer THIS question, and the way it did not is the point of this file:

* the share was ``marginal / req_p50_us``: a MICROBENCHMARK number divided by a
  REQUEST latency taken from a different measurement. Nothing ever measured the
  cost inside the request that paid it, so the denominator was not that
  request's;
* the harness's own docstring claimed the endpoint "is measured with and
  without validation, and the difference is the cost as it lands on the hot
  path". There was no without-validation arm. The difference was between two
  microbenchmark layers;
* the idna figure was ``idna.encode("subdomain.example.co.uk")`` -- a function
  the installed ``email_validator`` never calls (it calls ``idna.uts46_remap``
  on the domain and ``idna.alabel`` PER LABEL), on a domain the application
  never validates, and it was never subtracted from the marginal figure, so
  "email_validator" and "idna" were never isolated;
* the harness computed three percentiles inline with
  ``sorted(lat)[int(0.95 * (len(lat) - 1))]``, the exact pattern
  ``src/AGENTS.md`` bans, while ``src/benchmarks/percentiles.py`` is the ONE
  percentile estimator and ``src/benchmarks/protocol.py`` the ONE micro
  estimator;
* nothing priced the instrument's own cost, so a measurement taken THROUGH the
  instrument could not say what the instrument added to it (the defect
  FastAPI_saas-7zwp found in L-8).

What this file pins, therefore, is the METHOD, not a number:

* the instrument reaches the real pydantic path -- it wraps the three functions
  that are resolved at CALL TIME, so the patch actually bites;
* the idna portion is separated from email_validator's own, and the separation
  is arithmetic that cannot silently change;
* the share is computed against the SAME request's latency;
* the instrument's own overhead is measured as its own arm and subtracted
  explicitly rather than left in the number;
* the ONE percentile estimator and the ONE micro estimator are used, and the
  inline percentile arithmetic is gone;
* the dispute is bounded in a form a reader can check: the measured value, the
  share of the request that paid it, and what the 974 us claim would have
  implied for that same request.

The script is loaded by path rather than imported as a package: it is a
``scripts/`` entry point, not library code.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HARNESS_PATH = REPO_ROOT / "scripts" / "emailstr_cost.py"

#: The two published claims, and the band between them.
LOW_CLAIM_US = 75.0
HIGH_CLAIM_US = 974.0
BAND_TOP_US = 150.0


def load_harness() -> ModuleType:
    """Execute ``scripts/emailstr_cost.py`` as a module.

    ``sys.modules[name] = mod`` before ``exec_module`` so the module's own
    dataclasses (if it grows any) can resolve their defining module.
    """
    name = "m4_emailstr_cost_probe"
    spec = importlib.util.spec_from_file_location(name, HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestTheInstrumentReachesTheRealValidationPath:
    """The instrument must bite, or it measures nothing."""

    def test_validating_an_email_is_recorded(self) -> None:
        from pydantic import EmailStr, TypeAdapter

        mod = load_harness()
        restore = mod.install_instrument()
        try:
            with mod.attributing() as attribution:
                TypeAdapter(EmailStr).validate_python("qa0@e.io")
        finally:
            restore()

        assert attribution.email_calls == 1
        assert attribution.email_us > 0.0

    def test_the_idna_portion_is_separated_from_email_validators_own(self) -> None:
        from pydantic import EmailStr, TypeAdapter

        mod = load_harness()
        restore = mod.install_instrument()
        try:
            with mod.attributing() as attribution:
                TypeAdapter(EmailStr).validate_python("qa0@e.io")
        finally:
            restore()

        # idna is INSIDE email_validator's call, so it is part of the cost and
        # not all of it: a domain of "e.io" has two labels.
        assert attribution.idna_calls >= 1
        assert 0.0 < attribution.idna_us < attribution.email_us

    def test_the_wrapped_functions_are_the_ones_resolved_at_call_time(self) -> None:
        import email_validator
        import idna

        mod = load_harness()
        restore = mod.install_instrument()
        try:
            assert getattr(email_validator.validate_email, "is_emailstr_instrument", False)
            assert getattr(idna.uts46_remap, "is_emailstr_instrument", False)
            assert getattr(idna.alabel, "is_emailstr_instrument", False)
        finally:
            restore()

    def test_removing_the_instrument_restores_the_originals(self) -> None:
        import email_validator
        from pydantic import EmailStr, TypeAdapter

        mod = load_harness()
        original = email_validator.validate_email
        restore = mod.install_instrument()
        restore()

        assert email_validator.validate_email is original
        with mod.attributing() as attribution:
            TypeAdapter(EmailStr).validate_python("qa0@e.io")
        assert attribution.email_calls == 0

    def test_an_unbound_call_is_not_recorded(self) -> None:
        from pydantic import EmailStr, TypeAdapter

        mod = load_harness()
        restore = mod.install_instrument()
        try:
            TypeAdapter(EmailStr).validate_python("qa0@e.io")
        finally:
            restore()

        assert mod.current_attribution() is None


class TestTheShareUsesTheSameRequestsLatency:
    """A share is only sound if the denominator is the request that paid it."""

    def test_the_share_is_the_requests_own_cost_over_its_own_latency(self) -> None:
        mod = load_harness()
        row = mod.attribute_request(
            email_us=30.0,
            idna_us=10.0,
            email_calls=1,
            idna_calls=2,
            request_us=4000.0,
            overhead_us=0.0,
        )
        assert row["email_validator_own_us"] == 20.0
        assert row["share_of_request"] == 30.0 / 4000.0

    def test_two_requests_with_different_latencies_get_different_shares(self) -> None:
        mod = load_harness()
        fast = mod.attribute_request(
            email_us=30.0,
            idna_us=10.0,
            email_calls=1,
            idna_calls=1,
            request_us=2000.0,
            overhead_us=0.0,
        )
        slow = mod.attribute_request(
            email_us=30.0,
            idna_us=10.0,
            email_calls=1,
            idna_calls=1,
            request_us=8000.0,
            overhead_us=0.0,
        )
        assert fast["share_of_request"] == 4 * slow["share_of_request"]

    def test_the_instruments_own_overhead_is_subtracted_explicitly(self) -> None:
        mod = load_harness()
        row = mod.attribute_request(
            email_us=30.0,
            idna_us=10.0,
            email_calls=1,
            idna_calls=2,
            request_us=4000.0,
            overhead_us=0.3,
        )
        assert row["email_us_gross"] == 30.0
        assert row["email_us_net"] == 29.7
        assert row["share_of_request"] == 29.7 / 4000.0


class TestTheInstrumentOverheadIsPriced:
    """A measurement taken through an instrument must say what it added."""

    def test_each_wrappers_overhead_is_measured(self) -> None:
        mod = load_harness()
        overhead = mod.measure_instrument_overhead()
        assert overhead["email_wrapper_us"] > 0.0
        assert overhead["idna_wrapper_us"] > 0.0

    def test_the_overhead_is_reported_per_wrapper_and_not_folded_in(self) -> None:
        mod = load_harness()
        overhead = mod.measure_instrument_overhead()
        assert set(overhead) == {"email_wrapper_us", "idna_wrapper_us"}


class TestTheDisputeIsBounded:
    """The bead asks for a resolution OR an explicit bound. Both are checkable."""

    def test_a_measurement_below_the_band_says_neither(self) -> None:
        mod = load_harness()
        bound = mod.bound_the_dispute(measured_us=34.3, request_us=4553.3)
        assert bound["status"] == "NEITHER"
        assert bound["measured_us"] == 34.3

    def test_a_measurement_inside_the_band_says_the_band(self) -> None:
        mod = load_harness()
        bound = mod.bound_the_dispute(measured_us=100.0, request_us=4553.3)
        assert bound["status"] == "IN_BAND"
        assert bound["band_us"] == [LOW_CLAIM_US, BAND_TOP_US]

    def test_the_high_claim_is_expressed_against_the_same_request(self) -> None:
        mod = load_harness()
        bound = mod.bound_the_dispute(measured_us=34.3, request_us=4553.3)
        # What 974 us would have meant for THIS request, and how far below it
        # the measurement sits. A reader can check both.
        assert bound["high_claim_share_if_true"] == HIGH_CLAIM_US / 4553.3
        assert bound["factor_below_the_high_claim"] == HIGH_CLAIM_US / 34.3
        assert bound["share_of_request"] == 34.3 / 4553.3

    def test_the_bound_never_claims_a_share_the_measurement_did_not_take(self) -> None:
        mod = load_harness()
        bound = mod.bound_the_dispute(measured_us=0.0, request_us=1000.0)
        assert bound["share_of_request"] == 0.0


class TestTheOneEstimatorsAreUsed:
    """The repo has ONE percentile estimator and ONE micro estimator."""

    def test_the_harness_has_no_inline_percentile_arithmetic(self) -> None:
        source = HARNESS_PATH.read_text(encoding="utf-8")
        assert "sorted(" not in source

    def test_the_harness_delegates_to_the_one_percentile_estimator(self) -> None:
        source = HARNESS_PATH.read_text(encoding="utf-8")
        assert "from src.benchmarks.percentiles import" in source
        assert "nearest_rank" in source

    def test_the_harness_uses_the_shared_microbenchmark_protocol(self) -> None:
        source = HARNESS_PATH.read_text(encoding="utf-8")
        assert "from src.benchmarks.protocol import" in source

    def test_the_harness_does_not_time_idna_encode_as_the_apps_idna_cost(self) -> None:
        source = HARNESS_PATH.read_text(encoding="utf-8")
        # email_validator calls uts46_remap on the domain and alabel per label.
        # It never calls idna.encode, so timing it measures a call the
        # application does not make.
        assert "idna.encode(" not in source
        assert "uts46_remap" in source
        assert "alabel" in source


class TestTheShareIsNotComputedFromASeparateRequestDistribution:
    """The old method divided a microbenchmark by a request percentile."""

    def test_the_unsound_denominator_is_gone(self) -> None:
        source = HARNESS_PATH.read_text(encoding="utf-8")
        assert "req_p50_us" not in source

    def test_the_request_level_attribution_is_wired_into_the_harness(self) -> None:
        source = HARNESS_PATH.read_text(encoding="utf-8")
        assert "attribute_request(" in source
        assert "attributing(" in source
        assert "measure_instrument_overhead(" in source


class TestTheFirstRunsContamination:
    """The first Linux run measured its own refusals.

    1250 requests against a tier budget of 1000 per hour spent the budget
    inside the run, the refusals then earned the address an auto-ban, and the
    last repetition measured nothing at all. Its artefact carried a NEGATIVE
    net cost, because the instrument's overhead was subtracted from requests
    that never validated.
    """

    def test_each_call_is_recorded_with_its_own_duration(self) -> None:
        from pydantic import EmailStr, TypeAdapter

        harness = load_harness()
        restore = harness.install_instrument()
        try:
            with harness.attributing() as attribution:
                TypeAdapter(EmailStr).validate_python("qa0@e.io")
        finally:
            restore()
        assert len(attribution.email_call_us) == 1
        assert attribution.email_call_us[0] > 0.0
        assert attribution.email_values == ["qa0@e.io"]

    def test_the_instruments_overhead_is_charged_per_call(self) -> None:
        harness = load_harness()
        overhead = {"email_wrapper_us": 0.4, "idna_wrapper_us": 0.5}
        assert harness.request_overhead_us(email_calls=1, idna_calls=1, overhead=overhead) == pytest.approx(0.9)
        # A request that validated TWICE paid the email wrapper's cost twice.
        assert harness.request_overhead_us(email_calls=2, idna_calls=1, overhead=overhead) == pytest.approx(1.3)

    def test_a_request_that_did_not_validate_is_not_a_sample(self) -> None:
        harness = load_harness()
        rows = [
            {"email_calls": 0, "email_us_net": -0.9},
            {"email_calls": 2, "email_us_net": 177.0},
            {"email_calls": 1, "email_us_net": 88.0},
        ]
        measured = harness.select_measured_rows(rows)
        assert len(measured) == 2
        assert all(row["email_us_net"] > 0 for row in measured)

    def test_the_rate_limit_state_is_cleared_before_every_repetition(self) -> None:
        source = HARNESS_PATH.read_text(encoding="utf-8")
        # The state clear has to be inside the repetition loop, or the budget
        # is spent once for the whole run and the tail of the run is refused.
        assert "for rep in range(args.repetitions):" in source
        assert "state_clears.append" in source
        assert "await _clear_state()" in source

    def test_a_refused_request_is_reported_and_fails_the_run(self) -> None:
        source = HARNESS_PATH.read_text(encoding="utf-8")
        assert "requests_refused" in source
        assert "every_measured_request_was_served" in source
        # The run must not report success while requests were refused.
        assert "return 2" in source


class TestTheArtefactSaysWhichBuildProducedIt:
    """A measurement artefact that cannot name its build is a number with no referent.

    The previous artefact carried no timestamp, no host and no commit, so the
    house summary had to take them from the session that ran it. That is an
    inference about a file, and this bead exists because an inference about a
    file is what the previous dispute was made of.
    """

    def test_the_provenance_block_names_the_build_the_machine_and_the_moment(self) -> None:
        from datetime import datetime

        harness = load_harness()
        provenance = harness._provenance()
        assert set(provenance) == {"measured_at", "commit", "host", "platform"}
        assert datetime.fromisoformat(provenance["measured_at"]).tzinfo is not None
        assert provenance["host"]
        assert "python" in provenance["platform"]
        # A commit is either the sha of the build or the explicit word
        # "unknown" - never an empty string, which would read as a claim.
        assert provenance["commit"] == "unknown" or len(provenance["commit"]) == 40

    def test_the_artefact_carries_the_provenance_block(self) -> None:
        source = HARNESS_PATH.read_text(encoding="utf-8")
        assert '"provenance": provenance' in source
        assert "def _provenance()" in source
