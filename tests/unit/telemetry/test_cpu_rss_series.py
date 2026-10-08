"""T-7 verification: prove the CPU/RSS series is APP-HOST, windowed, and never over-claimed.

Named by the bead, so the path is the bead's.

Criterion (f) is the one with teeth, and it is why this file is mostly about what
the instrument REFUSES to say. Q-5 is open precisely because the prior memory
evidence is void on Linux, and there is now NO negative result to lean on. An
instrument that closed Q-5 from a short Windows run would be manufacturing the one
finding the programme most needs and does not have. So every test here is written
against a claim the instrument must NOT make, and the >=30 minute requirement is
asserted as a CONSTANT rather than demonstrated by a 30 minute test.

Criterion (b) is the other load-bearing one: under H-9 the load host and the app
host are different machines, so a series that does not say which machine it came
from cannot be checked against that rule.
"""

from __future__ import annotations

import pytest

from src.app.core.monitoring.process_series import (
    MINIMUM_WINDOW_SECONDS,
    PACK_WINDOW_SECONDS,
    SAMPLER_COST_CEILING_US,
    ProcessSeriesSampler,
    assert_cost,
    assert_host_recorded,
    assert_tracemalloc_is_attribution_only,
    assert_window_long_enough,
    measure_sampler_cost,
)
from src.app.core.monitoring.telemetry import measure_call_cost_robust


@pytest.fixture
def sampler() -> ProcessSeriesSampler:
    instance = ProcessSeriesSampler(enabled=True, interval=0.01)
    instance.record_host_identity("app-host-1")
    instance.start()
    yield instance
    instance.stop()


class TestTheSeriesIsAppHost:
    def test_a_series_without_a_recorded_host_is_refused(self) -> None:
        """Criterion (b): H-9 puts the generator on a different machine, so a series
        that does not say which one it describes cannot be attributed to the server."""
        unlabelled = ProcessSeriesSampler(enabled=True)
        unlabelled.start()
        unlabelled.stop()
        with pytest.raises(AssertionError, match="host identity"):
            assert_host_recorded(unlabelled.export())

    def test_a_load_host_series_is_not_accepted_as_the_apps(self) -> None:
        """The host is recorded as a FACT, so a load-host series is visibly wrong
        rather than silently attributable."""
        on_load_host = ProcessSeriesSampler(enabled=True)
        on_load_host.record_host_identity("load-host-1")
        on_load_host.start()
        on_load_host.stop()
        export = on_load_host.export()
        assert export["host_identity"] == "load-host-1"
        assert export["host_recorded"] is True
        assert assert_host_recorded(export)["host_identity"] != "app-host-1"

    def test_the_recorded_host_travels_with_the_series(self, sampler: ProcessSeriesSampler) -> None:
        export = sampler.export()
        assert export["host_identity"] == "app-host-1"
        assert export["host_recorded"] is True


class TestTheWindowRequirement:
    def test_the_minimum_window_is_thirty_minutes(self) -> None:
        """Criterion (c): asserted as a CONSTANT. Addendum A-8."""
        assert MINIMUM_WINDOW_SECONDS == 30 * 60 == 1800

    def test_the_packs_five_second_window_fails_it(self) -> None:
        """The defect being corrected: 5.81 s cannot tell a leak from arena growth,
        which is exactly how perf G14's numbers were produced."""
        assert PACK_WINDOW_SECONDS == 5.81
        with pytest.raises(AssertionError, match="minimum"):
            assert_window_long_enough(PACK_WINDOW_SECONDS)

    def test_a_window_below_thirty_minutes_is_refused(self) -> None:
        for window in (1.0, 5.81, 60.0, 1799.0):
            with pytest.raises(AssertionError):
                assert_window_long_enough(window)

    def test_thirty_minutes_is_accepted(self) -> None:
        assert assert_window_long_enough(1800.0) == 1800.0

    def test_the_superseded_window_is_recorded_in_the_artefact(
        self, sampler: ProcessSeriesSampler
    ) -> None:
        """The series must show what it replaced, not only its own numbers."""
        export = sampler.export()
        assert export["pack_window_seconds_superseded"] == 5.81
        assert export["minimum_window_seconds"] == MINIMUM_WINDOW_SECONDS

    def test_a_short_run_is_labelled_rather_than_reported_as_valid(
        self, sampler: ProcessSeriesSampler
    ) -> None:
        """A short run is honest about itself: window_meets_minimum is False and the
        refusal is separate from collection."""
        export = sampler.export()
        assert export["window_seconds"] < MINIMUM_WINDOW_SECONDS
        assert export["window_meets_minimum"] is False


class TestTracemallocAttributesAndNeverConcludes:
    def test_both_instruments_appear_side_by_side(self, sampler: ProcessSeriesSampler) -> None:
        """Criterion (d): perf G14 is the disagreement - RSS grew 8.18 MB while
        tracemalloc accounted for 1.865 MB - and averaging them erases the finding."""
        sampler.record_tracemalloc_peak(1_865_000)
        export = sampler.export()
        assert export["rss_summary_bytes"]["count"] > 0
        assert export["tracemalloc_peak_bytes"] == 1_865_000
        assert export["tracemalloc_role"] == "attribution-only"

    def test_a_tracemalloc_figure_with_no_rss_beside_it_is_rejected(self) -> None:
        """Criterion (d), the negative: tracemalloc alone is a conclusion."""
        with pytest.raises(AssertionError, match="tracemalloc peak with no RSS"):
            assert_tracemalloc_is_attribution_only(
                {"tracemalloc_peak_bytes": 1_865_000, "rss_summary_bytes": {"count": 0}}
            )

    def test_promoting_tracemalloc_into_a_concluding_key_is_rejected(self) -> None:
        export = {
            "tracemalloc_peak_bytes": 1,
            "tracemalloc_conclusion": "no leak",
            "rss_summary_bytes": {"count": 3},
        }
        with pytest.raises(AssertionError, match="promotes tracemalloc"):
            assert_tracemalloc_is_attribution_only(export)

    def test_a_well_formed_export_is_accepted(self, sampler: ProcessSeriesSampler) -> None:
        sampler.record_tracemalloc_peak(1_865_000)
        export = sampler.export()
        assert assert_tracemalloc_is_attribution_only(export) is export


class TestTheDisabledPathIsCostFree:
    def test_a_disabled_sampler_records_nothing(self) -> None:
        off = ProcessSeriesSampler(enabled=False)
        off.start()
        off.stop()
        assert off.export()["sample_count"] == 0

    def test_the_disabled_path_is_under_its_ceiling(self) -> None:
        """Criterion (e). The disabled path is what every deployment that never
        scrapes this series pays."""

        def one_record() -> None:
            off = ProcessSeriesSampler(enabled=False)
            off.sample()

        assert measure_call_cost_robust(one_record) <= SAMPLER_COST_CEILING_US

    def test_the_enabled_sampler_is_under_its_ceiling(self) -> None:
        measured = measure_sampler_cost()
        assert measured["measured_us"] <= measured["ceiling_us"]

    def test_the_ceiling_is_absolute(self) -> None:
        with pytest.raises(AssertionError, match="absolute"):
            assert_cost(1e9)


class TestNoInferenceIsRestatedAsAMeasurement:
    """Criterion (f), and the reason this bead is written the way it is.

    Q-5 asks whether the Linux allocation profile shows retained growth that the
    Windows Proactor artefact was masking. It is OPEN, and F-18 being void means
    G13's 'no leak demonstrated' no longer transfers. Nothing measured on this host
    - Windows, no /proc, RSS reading 0 - can answer it, and an instrument that let
    a reader infer otherwise would be manufacturing the one finding the programme
    most needs and does not have.
    """

    def test_a_platform_without_proc_reports_zero_rather_than_a_guess(self) -> None:
        """Zero is distinguishable from a real low reading. An invented figure is not."""
        from src.app.core.monitoring.process_series import _STATM_AVAILABLE

        if _STATM_AVAILABLE:  # pragma: no cover - Linux CI
            pytest.skip("this host can report RSS, so the fallback is not exercised")
        instance = ProcessSeriesSampler(enabled=True)
        instance.start()
        instance.sample()
        instance.stop()
        assert instance.export()["rss_first_bytes"] == 0

    def test_the_export_carries_no_leak_verdict(self) -> None:
        """No field may read as a conclusion about retained growth."""
        export = ProcessSeriesSampler(enabled=True).export()
        for key in export:
            assert "leak" not in key.lower(), f"{key} reads as a leak conclusion"

    def test_a_short_window_is_not_reported_as_meets_minimum(
        self, sampler: ProcessSeriesSampler
    ) -> None:
        assert sampler.export()["window_meets_minimum"] is False
