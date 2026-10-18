"""M-5: L-8's phase-1 per-call costs must not be measured through L-8's own meter.

L-8 (`src/benchmarks/loop_occupancy.py`) answers "what does the event loop spend
its time on?" with two phases. Phase 2 installs `install_instruments()`, which
monkeypatches three seams so each one charges `Meter.charge()` -- two
`perf_counter` reads, two `process_time` reads, three dict/attribute writes --
while it runs. Phase 1 then measures the per-call cost of those same three
sites. ``amain`` installed the instruments on the line ABOVE phase 1, so
phase 1's `header_scan` and `otel_span` figures were taken through the patched
seams and included the meter's own cost. `exc_info` was not, because
`measure_exc_info` builds its own `structlog.get_logger("l8.probe")` rather
than using the patched `database.logger`.

That is not a rounding error, and it is not hypothetical: `SPAN_CEILING_US`
carries the sentence "Measured 19.486 us on the target, i.e. 0.39 of this",
while P-19's independent artefact (`perf-results/RESULT-20261013-span/
span_cost.json`) records 3.123 us for the same call from a harness with no
meter. The ceiling text attributes the 6x gap to the host. The two figures were
never the same quantity, so neither can check the other.

M-5 also asks for a figure for the DEAD span -- the `ProxyTracer` path that S-3
(`FastAPI_saas-ztwf`) already removed behind a guard. `tracing.py:78-80`
returns `trace.INVALID_SPAN` when `_SDK_INSTALLED` is false, and that flag is
resolved once at import, so today's cost and the cost S-3 removed are two
different paths that must be measured as two arms. Measuring only the live one
prices the optimisation that already landed and leaves the bead's actual
question unanswered.

These tests pin four things and deliberately assert nothing about timing:

1. A site whose meter moved during its own measurement REFUSES to report a
   figure, instead of reporting one that includes the instrument.
2. The guard flag can be forced either way and is always restored.
3. The two arms really do take different code paths -- otherwise the A/B would
   be two names for one measurement.
4. The separation rule is arithmetic on data the protocol already produces, and
   it reports "not resolvable" rather than a flattering delta.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest
from opentelemetry import trace as otel_trace

from src.benchmarks import loop_occupancy as l8
from src.benchmarks import protocol
from src.benchmarks.protocol import NOISE_FLOOR_RATIO, Ceiling, MicroResult, ProtocolViolation

GUARDED = "guarded"
PROXY_TRACER = "proxy_tracer"


@pytest.fixture
def _meter_untouched() -> Iterator[None]:
    """Arm restoration for the three seams, then zero the meter.

    `install_instruments()` has no uninstall counterpart, so a test that calls it
    would leave the class patched for the rest of the session and tax every
    later test. Re-assigning each seam to its own current value is a no-op that
    arms monkeypatch's restore, which is cheaper and more honest than adding an
    uninstall path that only tests need.
    """
    from src.app.core.db import database
    from src.app.core.observability import tracing
    from src.app.middleware.attack_detection import AttackDetectionMiddleware

    saved: tuple[tuple[Any, str, Any], ...] = (
        (AttackDetectionMiddleware, "_check_for_patterns", AttackDetectionMiddleware._check_for_patterns),
        (tracing, "trace_span", tracing.trace_span),
        (database, "trace_span", database.trace_span),
        (database, "logger", database.logger),
    )
    for source in l8.METER.sources.values():
        source.wall_s = 0.0
        source.cpu_s = 0.0
        source.count = 0
    try:
        yield
    finally:
        for obj, name, value in saved:
            setattr(obj, name, value)
        for source in l8.METER.sources.values():
            source.wall_s = 0.0
            source.cpu_s = 0.0
            source.count = 0


# --------------------------------------------------------------------------
# (1) a measured site must not be charging its own meter
# --------------------------------------------------------------------------


class TestAMeteredSiteRefusesToReportAPerCallCost:
    def test_the_span_figure_refuses_a_metered_seam(self, _meter_untouched: None) -> None:
        l8.install_instruments()

        with pytest.raises(ProtocolViolation) as excinfo:
            l8.measure_otel_span()

        assert "otel_span" in str(excinfo.value)

    def test_the_header_scan_figure_refuses_a_metered_seam(self, _meter_untouched: None) -> None:
        l8.install_instruments()

        with pytest.raises(ProtocolViolation) as excinfo:
            l8.measure_header_scan()

        assert "header_scan" in str(excinfo.value)

    def test_the_exc_info_figure_is_unaffected_because_it_uses_its_own_logger(self, _meter_untouched: None) -> None:
        """The non-regression arm.

        `measure_exc_info` builds its own logger, so the patched
        `database.logger` never sees its records. If this test ever starts
        raising, the fix has been applied too broadly and a clean measurement is
        being refused.
        """
        l8.install_instruments()

        exc = RuntimeError("probe")
        result = l8.measure_exc_info(exc, (type(exc), exc, exc.__traceback__))

        assert result.reportable_us() >= 0.0
        assert l8.METER.sources["exc_info"].count == 0

    def test_an_uninstrumented_figure_is_reported_and_the_meter_stays_at_zero(self, _meter_untouched: None) -> None:
        """The state phase 1 is supposed to run in: no instruments, no charge."""
        result = l8.measure_otel_span()

        assert result.reportable_us() >= 0.0
        assert l8.METER.sources["otel_span"].count == 0

    def test_the_refusal_says_what_is_wrong_and_what_to_do(self, _meter_untouched: None) -> None:
        """A refusal nobody can act on is a failed run with extra steps."""
        l8.install_instruments()

        with pytest.raises(ProtocolViolation) as excinfo:
            l8.measure_otel_span()

        message = str(excinfo.value)
        assert "install_instruments" in message
        assert "phase 1" in message.lower()

    def test_the_two_arms_are_named_distinctly(self, _meter_untouched: None) -> None:
        """Two arms that share a name are one arm reported twice."""
        guarded = l8.measure_otel_span(state=GUARDED)
        proxy = l8.measure_otel_span(state=PROXY_TRACER)

        assert guarded.name != proxy.name
        assert GUARDED in guarded.name
        assert PROXY_TRACER in proxy.name

    def test_an_unknown_state_is_refused(self, _meter_untouched: None) -> None:
        with pytest.raises(ValueError, match="guard state"):
            l8.measure_otel_span(state="guarded-ish")


# --------------------------------------------------------------------------
# (2) the guard flag, forced and restored
# --------------------------------------------------------------------------


class TestTheGuardFlag:
    def test_each_state_forces_the_flag_it_names(self) -> None:
        from src.app.core.observability import tracing

        with l8.span_guard(GUARDED):
            assert tracing._SDK_INSTALLED is False
        with l8.span_guard(PROXY_TRACER):
            assert tracing._SDK_INSTALLED is True

    def test_the_flag_is_restored_on_exit(self) -> None:
        from src.app.core.observability import tracing

        original = tracing._SDK_INSTALLED
        with l8.span_guard(PROXY_TRACER):
            pass

        assert tracing._SDK_INSTALLED is original

    def test_the_flag_is_restored_even_when_the_block_raises(self) -> None:
        """A measurement that aborts must not leave the process permanently
        believing an SDK is installed -- that would silently change every later
        span in the same interpreter."""
        from src.app.core.observability import tracing

        original = tracing._SDK_INSTALLED
        with pytest.raises(RuntimeError), l8.span_guard(PROXY_TRACER):
            msg = "phase 2 aborted"
            raise RuntimeError(msg)

        assert tracing._SDK_INSTALLED is original

    def test_an_unknown_state_is_refused_before_the_flag_is_touched(self) -> None:
        from src.app.core.observability import tracing

        original = tracing._SDK_INSTALLED
        with pytest.raises(ValueError, match="guard state"), l8.span_guard("nope"):
            pass

        assert tracing._SDK_INSTALLED is original

    def test_the_guarded_arm_is_the_short_circuit(self) -> None:
        """The validity condition for the whole A/B.

        If forcing the state did not change which path `trace_span` takes, the
        two arms would be the same measurement under two names and the delta
        between them would be noise wearing a conclusion.
        """
        from src.app.core.observability.tracing import trace_span

        with l8.span_guard(GUARDED):
            with trace_span("probe") as guarded_span:
                observed_guarded = guarded_span

        assert observed_guarded is otel_trace.INVALID_SPAN

    def test_the_proxy_tracer_arm_is_not_the_short_circuit(self) -> None:
        """The version-independent validity condition for the second arm.

        What the ProxyTracer YIELDS is version-dependent. On the installed
        opentelemetry-api (1.39.1) it yields the module-level INVALID_SPAN and
        allocates nothing per call, so "the yielded span differs" is not a
        condition that can be asserted -- and asserting it would have made this
        test fail for a reason that has nothing to do with the harness. What IS
        stable across the declared pin (>=1.20.0) is whether the tracer's span
        machinery was ENTERED, so that is what is checked.
        """
        from src.app.core.observability import tracing
        from src.app.core.observability.tracing import trace_span

        entered = 0
        original = tracing.tracer.start_as_current_span

        def counting(*args: Any, **kwargs: Any) -> Any:
            nonlocal entered
            entered += 1
            return original(*args, **kwargs)

        tracing.tracer.start_as_current_span = counting  # type: ignore[method-assign]
        try:
            with l8.span_guard(PROXY_TRACER):
                with trace_span("probe") as proxy_span:
                    observed_proxy = proxy_span
        finally:
            tracing.tracer.start_as_current_span = original  # type: ignore[method-assign]

        assert entered > 0, "the forced arm did not enter the tracer's span machinery at all"
        assert not observed_proxy.is_recording(), (
            "the arm is supposed to be the path that ENTERS the tracer's machinery while no SDK "
            "is installed; a recording span means an SDK is present and this arm is measuring "
            "something else entirely"
        )

    def test_no_sdk_is_installed_here(self) -> None:
        """Both arms are only meaningful while no SDK is present.

        If an SDK were installed, the "proxy_tracer" arm would be a real
        recording span, `is_recording()` would be True, and the whole A/B would
        be measuring a different thing. Asserted rather than assumed.
        """
        with pytest.raises(ImportError):
            import opentelemetry.sdk  # type: ignore[import-not-found]  # noqa: F401


# --------------------------------------------------------------------------
# (3) the ceiling is not retuned to fit the number this bead produces
# --------------------------------------------------------------------------


class TestTheSpanCeilingIsNotRetunedToFitTheMeasurement:
    def test_the_ceiling_still_holds_fifty_microseconds(self) -> None:
        """`protocol.py` bans `new < old`, which includes `new ceiling < old
        ceiling`. The value is frozen here so a future run cannot quietly relax
        the only bound this site has."""
        assert l8.SPAN_CEILING_US.value_us == 50.0

    def test_the_ceiling_is_still_anchored_to_the_sampler(self) -> None:
        """The anchor, not the measurement, is what makes a ceiling sound."""
        assert "SAMPLER_COST_CEILING_US" in l8.SPAN_CEILING_US.label

    def test_the_ceiling_no_longer_quotes_the_metered_measurement(self) -> None:
        """The 19.486 us figure was taken through the meter, so it is not a
        measurement of the span and must not be cited as one -- including by
        this ceiling's own label, which put it in every artefact."""
        assert "19.486" not in l8.SPAN_CEILING_US.label


# --------------------------------------------------------------------------
# (4) the separation rule, stated as arithmetic
# --------------------------------------------------------------------------


def _arm(name: str, mins: tuple[float, ...]) -> MicroResult:
    return protocol._build(name, "us", mins, tuple(mins), iterations=1, warmup=1, repetitions=len(mins))


class TestTheSeparationRule:
    def test_disjoint_batch_minima_make_the_delta_a_result(self) -> None:
        guarded = _arm("g", (1.0, 1.1, 1.05))
        proxy = _arm("p", (9.0, 9.2, 8.8))

        report = l8.span_guard_ab({"guarded": guarded, "proxy_tracer": proxy}, Ceiling(50.0, "anchor"))

        assert report["arms_separated"] is True
        assert report["dead_minus_guarded_us"] == pytest.approx(7.95, abs=0.01)

    def test_overlapping_batch_minima_make_the_delta_unresolvable(self) -> None:
        """Two arms whose slowest batch minimum overlaps the other's fastest are
        inside the noise. The honest report is 'not resolvable', not a delta."""
        guarded = _arm("g", (1.0, 1.9))
        proxy = _arm("p", (1.2, 1.4))

        report = l8.span_guard_ab({"guarded": guarded, "proxy_tracer": proxy}, Ceiling(50.0, "anchor"))

        assert report["arms_separated"] is False
        assert "NOT RESOLVABLE" in report["delta_verdict"]

    def test_the_stated_rule_is_the_rule_the_code_applies(self) -> None:
        """The label must name the SAME direction the separation test uses.

        The rule the code applies is the strict one: the dead arm's FASTEST
        batch minimum must exceed the guarded arm's SLOWEST, i.e. their batch
        minima may not overlap at all. The label once stated the opposite
        comparison, so the artefact carried a rule that contradicted the
        verdict printed beside it in the same object. A stated rule that is not
        the applied rule is a rule nobody can check the run against.
        """
        guarded = _arm("g", (1.0, 1.1))
        proxy = _arm("p", (9.0, 9.2))

        report = l8.span_guard_ab({"guarded": guarded, "proxy_tracer": proxy}, Ceiling(50.0, "anchor"))

        rule = report["separation_rule"]
        assert re.search(r"dead arm's FASTEST batch minimum.*guarded arm's SLOWEST", rule), rule
        assert "dead arm's slowest" not in rule
        assert "guarded arm's fastest" not in rule

    def test_both_arms_are_judged_against_the_same_absolute_ceiling(self) -> None:
        """Both arms get the SAME 50 us bound. Giving the cheap arm its own
        ceiling would make the comparison a self-fulfilling prophecy."""
        guarded = _arm("g", (1.0,))
        proxy = _arm("p", (49.0,))
        ceiling = Ceiling(50.0, "anchor")

        report = l8.span_guard_ab({"guarded": guarded, "proxy_tracer": proxy}, ceiling)

        assert report["ceiling_us"] == 50.0
        assert report["guarded"]["verdict_passed"] is True
        assert report["proxy_tracer"]["verdict_passed"] is True
        assert "is under the anchor ceiling 50.00us" in report["proxy_tracer"]["verdict"]

    def test_a_single_failing_arm_is_reported_as_failing(self) -> None:
        guarded = _arm("g", (1.0,))
        proxy = _arm("p", (60.0,))

        report = l8.span_guard_ab({"guarded": guarded, "proxy_tracer": proxy}, Ceiling(50.0, "anchor"))

        assert report["proxy_tracer"]["verdict_passed"] is False
        assert "EXCEEDS" in report["proxy_tracer"]["verdict"]

    def test_each_arm_reports_its_own_spread_against_the_declared_noise_floor(self) -> None:
        """protocol.py's noise floor is the yardstick for whether an arm is
        even self-consistent, so it travels with the arm."""
        guarded = _arm("g", (1.0, 5.0))
        proxy = _arm("p", (9.0, 9.1))

        report = l8.span_guard_ab({"guarded": guarded, "proxy_tracer": proxy}, Ceiling(50.0, "anchor"))

        assert report["guarded"]["spread_ratio"] == pytest.approx(5.0)
        assert report["guarded"]["inside_noise_floor"] is False
        assert report["proxy_tracer"]["spread_ratio"] == pytest.approx(9.1 / 9.0)
        assert report["proxy_tracer"]["inside_noise_floor"] is True
        assert NOISE_FLOOR_RATIO == 2.0

    def test_the_report_names_the_two_arms_and_says_which_is_which(self) -> None:
        """An A/B whose labels do not say which path is live is a trap for the
        next reader, and the live path is the one that is easy to get wrong."""
        report = l8.span_guard_ab(
            {"guarded": _arm("g", (1.0,)), "proxy_tracer": _arm("p", (9.0,))},
            Ceiling(50.0, "anchor"),
        )

        assert report["guarded"]["path"] == l8.SPAN_ARM_LABELS["guarded"]
        assert report["proxy_tracer"]["path"] == l8.SPAN_ARM_LABELS["proxy_tracer"]
        assert "INVALID_SPAN" in report["guarded"]["path"]
        assert "NonRecordingSpan" in report["proxy_tracer"]["path"]

    def test_the_report_never_compares_the_delta_against_the_ceiling(self) -> None:
        """A ceiling is an absolute bound on a cost, not on a difference. If a
        delta were scored against 50 us the report would be inviting exactly the
        `new < old` reasoning protocol.py bans."""
        report = l8.span_guard_ab(
            {"guarded": _arm("g", (1.0,)), "proxy_tracer": _arm("p", (9.0,))},
            Ceiling(50.0, "anchor"),
        )

        assert "delta_verdict" in report
        assert "delta_ceiling" not in report
        assert not any("EXCEEDS" in str(value) for value in report.values() if isinstance(value, str))


# --------------------------------------------------------------------------
# (5) the artefact carries both arms and the load attribution for each
# --------------------------------------------------------------------------


class TestTheArtefactCarriesBothArms:
    @staticmethod
    def _occupancy(state: str, per_call_us: float, count: int) -> dict[str, Any]:
        return {
            "guard_state": state,
            "wall_s": 10.0,
            "process_cpu_s": 8.0,
            "per_source": {
                "header_scan": {"wall_s": 0.1, "cpu_s": 0.05, "count": 10},
                "otel_span": {"wall_s": per_call_us * count / 1e6, "cpu_s": 0.02, "count": count},
                "exc_info": {"wall_s": 0.2, "cpu_s": 0.1, "count": 10},
            },
            "three_sources": {
                "wall_s": 0.3,
                "cpu_s": 0.17,
                "occupancy_injected": 0.03,
                "occupancy_accounted": 0.02,
            },
            "loop_lag": {
                "occupancy_observed": 0.5,
                "occupancy_observed_basis": "grid",
                "occupancy_naive_lag_sum": 0.6,
                "lag_sum_s": 6.0,
                "samples": 1200,
                "sampler_usable": True,
                "achieved_rate_hz": 125.0,
                "meets_minimum_rate": True,
                "minimum_rate_hz": 120.0,
                "requested_interval_s": 0.008,
                "loop_module": "asyncio",
                "representativeness": "production loop",
                "summary_ms": {"p50": 1.0},
                "sampler_cost_note": "",
                "occupancy_naive_lag_sum_note": "",
            },
        }

    def test_both_guard_states_are_attributed_in_the_load(self) -> None:
        """The acceptance asks for the figure to be attributed in the L-8
        profile, so the artefact must carry `calls_observed_in_load` and the
        occupancy for each state, not one of them."""
        scans = {
            "header_scan": _arm("header_scan_per_header", (56.0,)),
            "otel_span": _arm("otel_span_per_invocation_guarded", (1.0,)),
            "exc_info": _arm("exc_info_error_call", (300.0,)),
        }
        occupancy = {
            GUARDED: self._occupancy(GUARDED, 1.0, 2000),
            PROXY_TRACER: self._occupancy(PROXY_TRACER, 9.0, 2000),
        }
        arms = {
            GUARDED: _arm("otel_span_per_invocation_guarded", (1.0,)),
            PROXY_TRACER: _arm("otel_span_per_invocation_proxy_tracer", (9.0,)),
        }
        ceilings = {
            "header_scan": l8.HEADER_SCAN_CEILING_US,
            "otel_span": l8.SPAN_CEILING_US,
            "exc_info": l8.EXC_INFO_CEILING_US,
        }

        artifact = l8.build_artifact(
            protocol.stamp(),
            scans,
            ceilings,
            arms,
            occupancy,
            dict.fromkeys(scans, "under ceiling"),
            "ProxyTracer",
            False,
            "test-host",
        )

        by_state = artifact["span_guard_ab"]["occupancy_by_state"]
        assert set(by_state) == {GUARDED, PROXY_TRACER}
        assert by_state[GUARDED]["per_source"]["otel_span"]["count"] == 2000
        assert by_state[PROXY_TRACER]["per_source"]["otel_span"]["count"] == 2000
        assert by_state[PROXY_TRACER]["cross_check"]["sampler_usable"] is True
        assert artifact["span_guard_ab"]["guarded"]["per_call_us"] == 1.0
        assert artifact["span_guard_ab"]["proxy_tracer"]["per_call_us"] == 9.0

    def test_the_live_per_source_block_is_the_guarded_run(self) -> None:
        """`per_source` is the profile the bead and the P-19 artefacts quote, so
        it must be the state production is actually in -- the guarded one."""
        scans = {
            "header_scan": _arm("header_scan_per_header", (56.0,)),
            "otel_span": _arm("otel_span_per_invocation_guarded", (1.0,)),
            "exc_info": _arm("exc_info_error_call", (300.0,)),
        }
        occupancy = {
            GUARDED: self._occupancy(GUARDED, 1.0, 2000),
            PROXY_TRACER: self._occupancy(PROXY_TRACER, 9.0, 2000),
        }
        arms = {
            GUARDED: _arm("otel_span_per_invocation_guarded", (1.0,)),
            PROXY_TRACER: _arm("otel_span_per_invocation_proxy_tracer", (9.0,)),
        }

        artifact = l8.build_artifact(
            protocol.stamp(),
            scans,
            {
                "header_scan": l8.HEADER_SCAN_CEILING_US,
                "otel_span": l8.SPAN_CEILING_US,
                "exc_info": l8.EXC_INFO_CEILING_US,
            },
            arms,
            occupancy,
            dict.fromkeys(scans, "under ceiling"),
            "ProxyTracer",
            False,
            "test-host",
        )

        assert artifact["per_source"]["otel_span"]["calls_observed_in_load"] == 2000
        assert artifact["per_source"]["otel_span"]["occupancy_accounted_in_load"] == pytest.approx(0.02 / 8.0)
        assert artifact["live_guard_state"] == GUARDED

    def test_the_artefact_records_that_no_sdk_was_present(self) -> None:
        """Without it the two arms are not the two paths this bead names."""
        scans = {
            "header_scan": _arm("header_scan_per_header", (56.0,)),
            "otel_span": _arm("otel_span_per_invocation_guarded", (1.0,)),
            "exc_info": _arm("exc_info_error_call", (300.0,)),
        }
        arms = {
            GUARDED: _arm("otel_span_per_invocation_guarded", (1.0,)),
            PROXY_TRACER: _arm("otel_span_per_invocation_proxy_tracer", (9.0,)),
        }
        occupancy = {
            GUARDED: self._occupancy(GUARDED, 1.0, 2000),
            PROXY_TRACER: self._occupancy(PROXY_TRACER, 9.0, 2000),
        }

        artifact = l8.build_artifact(
            protocol.stamp(),
            scans,
            {
                "header_scan": l8.HEADER_SCAN_CEILING_US,
                "otel_span": l8.SPAN_CEILING_US,
                "exc_info": l8.EXC_INFO_CEILING_US,
            },
            arms,
            occupancy,
            dict.fromkeys(scans, "under ceiling"),
            "ProxyTracer",
            False,
            "test-host",
        )

        assert artifact["otel_sdk_present"] is False
        assert artifact["span_guard_ab"]["arms_forced_not_installed"] is True


# --------------------------------------------------------------------------
# (6) the untimed validity probe
# --------------------------------------------------------------------------


class TestTheValidityProbe:
    """The probe that decides whether the A/B measures what it claims.

    It is deliberately OUT of the timed region: a counter inside the measured
    code would inflate the arm that calls it and flatter the delta. So the
    probe runs once, untimed, and its answer is recorded in the artefact.
    """

    def test_the_forced_arm_is_recorded_as_entering_the_tracer(self) -> None:
        observation = l8.probe_span_path(PROXY_TRACER)

        assert observation["entered_the_tracer"] is True
        assert observation["tracer_machinery_entries"] == 1
        assert observation["is_recording"] is False, (
            "a recording span means an SDK is installed, and the arm is then measuring something else"
        )

    def test_the_guarded_arm_is_recorded_as_never_entering_it(self) -> None:
        observation = l8.probe_span_path(GUARDED)

        assert observation["entered_the_tracer"] is False
        assert observation["tracer_machinery_entries"] == 0

    def test_the_probe_leaves_the_flag_as_it_found_it(self) -> None:
        from src.app.core.observability import tracing

        before = tracing._SDK_INSTALLED
        l8.probe_span_path(PROXY_TRACER)

        assert tracing._SDK_INSTALLED is before

    def test_the_probe_does_not_charge_the_meter(self, _meter_untouched: None) -> None:
        l8.probe_span_path(PROXY_TRACER)
        l8.probe_span_path(GUARDED)

        assert l8.METER.sources["otel_span"].count == 0
