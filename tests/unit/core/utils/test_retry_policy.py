"""One retry schedule, three worker bodies.

The three job-level retry ladders (event bus, notification email, webhook
delivery) each carried their own copy of "attempt -> delay -> jitter". The
copies had drifted in exactly the places nobody looked:

* only the event-bus copy clamped a malformed ``job_try``, so ``0`` selected the
  LAST (longest) entry there and the FIRST one in notifications;
* two copies used ``round`` and one used ``ceil``, so the same nominal jitter
  produced delays up to a second apart.

These tests pin the consolidated policy. They deliberately do NOT assert that
the three callers share a ladder or a ceiling — those differ on purpose (email
and events have different blast radii, and the webhook ladder is five entries
long), which is why the ladder and the ceiling stay caller-supplied.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.app.core.utils.retry_policy import JITTER_FRACTION, jittered_delay, retry_delay

LADDER = [10, 60, 360]


class TestTheScheduleIsIndexedByAttempt:
    def test_first_attempt_takes_the_first_entry(self) -> None:
        assert retry_delay(LADDER, 1, max_tries=3, jitter=False) == 10

    def test_later_attempts_walk_the_ladder(self) -> None:
        assert retry_delay(LADDER, 2, max_tries=4, jitter=False) == 60
        assert retry_delay(LADDER, 3, max_tries=4, jitter=False) == 360

    def test_the_last_entry_repeats_past_the_ladder(self) -> None:
        """An operator who raises max_tries must not index off the end."""
        assert retry_delay(LADDER, 9, max_tries=12, jitter=False) == 360

    def test_exhaustion_returns_none(self) -> None:
        """None means "run your terminal branch" — the caller owns that action."""
        assert retry_delay(LADDER, 3, max_tries=3, jitter=False) is None
        assert retry_delay(LADDER, 4, max_tries=3, jitter=False) is None

    def test_an_empty_ladder_is_none_not_zero(self) -> None:
        """An empty schedule must not degrade to "retry immediately"."""
        assert retry_delay([], 1, max_tries=3, jitter=False) is None


class TestAMalformedCounterIsClamped:
    """The clamp only one of the three copies had.

    ``job_try`` indexes the ladder, so ``0`` or a negative selects the LAST
    entry — a bad counter silently becoming the SLOWEST possible retry.
    """

    @pytest.mark.parametrize("bad", [0, -1, -100])
    def test_zero_and_negative_take_the_first_entry(self, bad: int) -> None:
        assert retry_delay(LADDER, bad, max_tries=3, jitter=False) == 10

    def test_a_non_int_counter_takes_the_first_entry(self) -> None:
        assert retry_delay(LADDER, "2", max_tries=3, jitter=False) == 10  # type: ignore[arg-type]
        assert retry_delay(LADDER, None, max_tries=3, jitter=False) == 10  # type: ignore[arg-type]

    def test_clamping_does_not_bypass_exhaustion(self) -> None:
        """A clamped counter is attempt 1, so max_tries=1 is still terminal."""
        assert retry_delay(LADDER, 0, max_tries=1, jitter=False) is None


class TestJitterIsOneExpression:
    def test_jitter_stays_inside_the_band(self) -> None:
        for base in (10, 60, 360):
            low = base * (1 - JITTER_FRACTION)
            high = base * (1 + JITTER_FRACTION)
            for _ in range(200):
                delay = jittered_delay(base)
                assert low <= delay <= high + 1, f"{delay} outside [{low}, {high}] for {base}"

    def test_the_floor_is_one_second(self) -> None:
        """A sub-second delay must never become an immediate retry hot loop."""
        with patch("src.app.core.utils.retry_policy.random.uniform", return_value=0.0):
            assert jittered_delay(0.2) == 1
            assert jittered_delay(0) == 1

    def test_it_ceils_rather_than_rounds(self) -> None:
        """The divergence that was fixed: ``round`` truncated toward a retry.

        With no jitter, a fractional delay must round UP. ``round(0.5)`` is 0 in
        Python's banker's rounding, which would have scheduled an immediate
        retry — and ``int(1.9)`` is 1, the same truncation one step later. BOTH
        branches ceil, so the un-jittered schedule a test pins here is the one
        production runs.
        """
        assert jittered_delay(0.5, jitter=False) == 1
        assert jittered_delay(1.2, jitter=False) == 2
        assert jittered_delay(1.9, jitter=False) == 2
        assert jittered_delay(2.1, jitter=False) == 3

    def test_jitter_can_be_disabled_for_exact_schedules(self) -> None:
        assert jittered_delay(60, jitter=False) == 60

    def test_both_branches_agree_on_a_fractional_delay(self) -> None:
        """The un-jittered branch used ``int()``, so 1.9 answered 1 while the
        jittered branch answered 2 — the documented "never truncate toward an
        immediate retry" rule held in one branch only."""
        with patch("src.app.core.utils.retry_policy.random.uniform", return_value=1.0):
            for nominal in (1.2, 1.5, 1.9):
                assert jittered_delay(nominal, jitter=False) == jittered_delay(nominal, jitter=True)

    def test_a_burst_decorrelates(self) -> None:
        """The point of jitter: N simultaneous failures must not align."""
        delays = {jittered_delay(60) for _ in range(50)}
        assert len(delays) > 1, "an unjittered ladder retries a burst in lockstep"


class TestTheThreeCallSitesShareThePolicy:
    """The callers keep their own ladder and ceiling; they share the arithmetic."""

    def test_the_event_bus_reads_its_ladder_live_from_settings(self) -> None:
        import inspect

        from src.app.core.events import worker

        source = inspect.getsource(worker.process_event_handler)
        assert "retry_delay(" in source, "the event bus must use the shared policy"
        assert "EVENT_BUS_RETRY_DELAYS" in source, (
            "the event bus resolves its ladder from settings, not a constant"
        )
        assert "uniform(" not in source, "jitter must not be re-implemented locally"

    def test_the_email_worker_uses_the_shared_policy(self) -> None:
        import inspect

        from src.app.modules.notifications import service

        source = inspect.getsource(service.send_email_task)
        assert "retry_delay(" in source
        assert "EMAIL_RETRY_DELAYS" in source, "the email ladder stays local (a constant)"
        assert "uniform(" not in source, "jitter must not be re-implemented locally"

    def test_the_webhook_delivery_uses_the_shared_jitter(self) -> None:
        import inspect

        from src.app.modules.webhooks import delivery

        source = inspect.getsource(delivery)
        assert "jittered_delay(" in source
        assert "uniform(" not in source, (
            "the webhook ladder indexes its own schedule (it has an override "
            "path) but must not re-implement the jitter arithmetic"
        )

    def test_no_caller_still_uses_the_old_round_jitter(self) -> None:
        """The exact expression that drifted must be gone from all three."""
        import inspect

        from src.app.core.events import worker
        from src.app.modules.notifications import service
        from src.app.modules.webhooks import delivery

        for module in (worker, service, delivery):
            source = inspect.getsource(module)
            assert "int(round(" not in source, f"{module.__name__} still rounds a delay"
