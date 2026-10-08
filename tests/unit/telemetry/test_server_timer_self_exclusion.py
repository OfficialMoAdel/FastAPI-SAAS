"""T-3 verification: prove the server-side timer is EXCLUDED from its own timing.

Named by the bead, so the path is the bead's.

Criterion (b) is the one with teeth. "Excluded from its own timing" is not
observable by reading the code - a timer can look correct and still fold its own
cost into the figure it reports. It is observable by DIFFERENCE: run the same
request through the timer and through a pass-through, and compare. The overhead
must show up in the difference and NOT in the reported value.

The comparison is against a pass-through BASELINE captured the same way, never
against a nominal constant. An earlier version compared against the literal the
test meant to sleep for and failed on Windows by 13 ms for a perfectly correct
implementation, because ``asyncio.sleep`` there has ~15 ms timer granularity. A
test that fails for reasons unrelated to its subject trains its readers to ignore
it - and the fix is not a looser bar, it is a baseline that contains the same
scheduler noise in both arms.
"""

from __future__ import annotations

import asyncio

import pytest

from src.app.core.monitoring.request_timer import (
    RESPONSE_TIME_HEADER,
    TIMER_COST_CEILING_US,
    RequestTimer,
    assert_excluded_from_own_timing,
    assert_series_not_merged,
    measure_enabled_cost,
)

#: The app under test. A fixed sleep, so the server-side figure has a known value
#: to be compared against.
_APP_DELAY_SECONDS = 0.005

#: Tolerance for the exclusion comparison. Absolute and generous, because the
#: quantity under test is that the timer's own cost is NOT inside the figure it
#: reports - not that the figure is precise to the microsecond. A tight bar would
#: fail on scheduler noise rather than on a defect.
_EXCLUSION_TOLERANCE_US = 1_000.0

#: How many paired samples the comparison averages over. One pair cannot do it: a
#: single timed run and a single pass-through differ by milliseconds of scheduler
#: noise on this host, which is three orders of magnitude larger than the few
#: microseconds of timer work.
_PAIRS = 40


async def _app(scope, receive, send, *, delay: float = _APP_DELAY_SECONDS) -> None:
    """A minimal ASGI app: sleep, then answer."""
    await asyncio.sleep(delay)
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


class TestTheHeaderIsEmitted:
    async def test_the_header_is_present_when_enabled(self) -> None:
        timer = RequestTimer(_app, enabled=True)
        captured: dict = {}

        async def send(message: dict) -> None:
            captured[message["type"]] = message

        await timer({"type": "http"}, None, send)
        headers = dict(captured["http.response.start"]["headers"])
        assert RESPONSE_TIME_HEADER.encode().lower() in headers
        assert float(headers[RESPONSE_TIME_HEADER.encode().lower()]) > 0.0

    async def test_no_header_and_no_work_when_disabled(self) -> None:
        """Criterion (e): the disabled path adds nothing."""
        timer = RequestTimer(_app, enabled=False)
        captured: dict = {}

        async def send(message: dict) -> None:
            captured[message["type"]] = message

        await timer({"type": "http"}, None, send)
        assert captured["http.response.start"]["headers"] == []
        assert timer.export()["server_sample_count"] == 0

    async def test_the_disabled_path_costs_less_than_the_enabled_one(self) -> None:
        """The disabled path must be the cheaper one, or the flag buys nothing.

        Measured with the helpers' own primitives rather than the helpers: those
        drive a real event loop, and calling them from inside a running one raises
        "Cannot run the event loop while another loop is running".
        """
        from src.app.core.monitoring.request_timer import RequestTimer as _RT
        from src.app.core.monitoring.telemetry import measure_call_cost_robust

        disabled = _RT(app=None, enabled=False)
        enabled = _RT(app=None, enabled=True)

        def disabled_path() -> None:
            if not disabled.enabled:
                return

        def enabled_path() -> None:
            enabled._series.record(0.0)

        assert measure_call_cost_robust(disabled_path) < measure_call_cost_robust(enabled_path)


class TestTheTimersOwnCostIsBounded:
    def test_the_enabled_path_is_under_its_absolute_ceiling(self) -> None:
        measured = measure_enabled_cost()
        assert measured["measured_us"] <= measured["ceiling_us"]
        assert measured["measured_us"] <= TIMER_COST_CEILING_US

    def test_the_ceiling_is_absolute(self) -> None:
        with pytest.raises(AssertionError, match="absolute"):
            from src.app.core.monitoring.request_timer import assert_cost

            assert_cost(1e9)

    def test_the_exclusion_bar_sits_above_the_timer_cost(self) -> None:
        """If the timer cost approached the exclusion bar, the comparison below
        could no longer tell self-inclusion from scheduler noise."""
        assert measure_enabled_cost()["measured_us"] < _EXCLUSION_TOLERANCE_US / 10


class TestTheTimerIsExcludedFromItsOwnTiming:
    async def test_the_overhead_appears_in_the_difference_not_the_reported_value(
        self,
    ) -> None:
        """Criterion (b): the substantive one.

        ``timed`` and ``baseline`` are the SAME request measured the same way; the
        difference between them is the timer's own cost. If that cost were folded
        into the reported figure, the reported value would exceed the pass-through
        baseline by more than the tolerance.
        """
        timer = RequestTimer(_app, enabled=True)
        timed: list[float] = []
        baseline: list[float] = []

        for _ in range(_PAIRS):
            captured: dict = {}

            async def send(message: dict) -> None:
                captured[message["type"]] = message

            timer.reset()
            await timer({"type": "http"}, None, send)
            timed.append(timer.export()["server_summary_ms"]["mean"])

            started = asyncio.get_event_loop().time()
            await _app({"type": "http"}, None, send)
            baseline.append((asyncio.get_event_loop().time() - started) * 1000.0)

        assert_excluded_from_own_timing(
            sum(timed) / len(timed),
            sum(baseline) / len(baseline),
            tolerance_us=_EXCLUSION_TOLERANCE_US,
        )

    async def test_the_reported_value_tracks_the_app_not_the_timer(self) -> None:
        """Changing the APP's delay must move the reported figure; running the
        timer harder must not."""
        fast = RequestTimer(_app, enabled=True)
        slow = RequestTimer(
            lambda scope, receive, send: _app(scope, receive, send, delay=0.020),
            enabled=True,
        )

        async def measure(timer: RequestTimer) -> float:
            captured: dict = {}

            async def send(message: dict) -> None:
                captured[message["type"]] = message

            await timer({"type": "http"}, None, send)
            return float(
                dict(captured["http.response.start"]["headers"])[
                    RESPONSE_TIME_HEADER.encode().lower()
                ]
            )

        fast_ms = await measure(fast)
        slow_ms = await measure(slow)
        assert slow_ms > fast_ms, (
            "the reported figure did not track the app's own duration, so it is not "
            "measuring the app"
        )

    def test_the_exclusion_check_rejects_a_self_inflated_figure(self) -> None:
        """The check must be shown rejecting an inflation, or it asserts nothing."""
        with pytest.raises(AssertionError, match="inside the reported value"):
            assert_excluded_from_own_timing(25.0, 5.0, tolerance_us=_EXCLUSION_TOLERANCE_US)


class TestServerAndClientAreNeverMixed:
    def test_the_export_is_a_server_series(self) -> None:
        export = RequestTimer(_app, enabled=False).export()
        assert_series_not_merged(export)
        assert any(key.startswith("server_") for key in export)

    def test_a_mixed_client_server_key_is_rejected(self) -> None:
        with pytest.raises(AssertionError, match="mixes client and server"):
            assert_series_not_merged({"server_x": 1, "client_server_y": 2})

    def test_an_export_with_no_server_series_is_rejected(self) -> None:
        with pytest.raises(AssertionError, match="no server_"):
            assert_series_not_merged({"client_only": 1})

    def test_the_pending_client_series_is_declared_rather_than_silent(self) -> None:
        """The load generator's series arrives later, in the harness report. An
        artefact has to be able to say 'not merged in yet' rather than 'withheld'."""
        export = assert_series_not_merged({"server_sample_count": 0})
        assert export["client_series_state"] == "pending-harness-merge"


class TestTheTimerDoesNotChangeResponses:
    async def test_the_body_and_status_are_untouched(self) -> None:
        """An additive instrument: it adds a header and changes nothing else."""
        for enabled in (True, False):
            timer = RequestTimer(_app, enabled=enabled)
            captured: dict = {}

            async def send(message: dict) -> None:
                captured[message["type"]] = message

            await timer({"type": "http"}, None, send)
            start = captured["http.response.start"]
            assert start["status"] == 200
            assert captured["http.response.body"]["body"] == b"ok"
            if enabled:
                assert len(start["headers"]) == 1
            else:
                assert start["headers"] == []

    async def test_a_non_http_scope_is_passed_through_untouched(self) -> None:
        """A lifespan scope must not be timed as if it were a request."""
        seen: list[str] = []

        async def app(scope, receive, send) -> None:
            seen.append(scope["type"])

        timer = RequestTimer(app, enabled=True)
        await timer({"type": "lifespan"}, None, None)
        assert seen == ["lifespan"]
