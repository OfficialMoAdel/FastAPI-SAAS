"""The rate-limit engine must REPORT a degradation, not hide it.

Two degradations are possible, and both mean "the limit was NOT enforced by
the configured algorithm":

* GCRA is configured but the call failed — the fixed window took over, and
  the 2x boundary burst GCRA exists to remove is back;
* GCRA is configured but the limiter was never initialised (Redis was
  absent at boot), so the fixed window enforces the limit from the start.

The fixed-window path already counted its own failures; the GCRA side did
not, so an operator saw nothing while every auth/MFA budget quietly changed
engine. The degradation now travels with the result, so the caller can mark
the verdict degraded and the response can say so.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from src.app.core.security import rate_policy
from src.app.core.security.rate_policy import build_verdict, window_reset_seconds
from src.app.core.utils import gcra_rate_limit  # noqa: F401 — imported so the patch target resolves
from src.app.core.utils.rate_limit import RateLimiter


class TestEngineReportsDegradation:
    async def test_a_failed_gcra_call_is_counted_and_reported(self) -> None:
        """The swap to the fixed window is counted and returned."""
        gcra = AsyncMock()
        gcra.check_rate_limit = AsyncMock(side_effect=RuntimeError("gcra down"))
        fallback = AsyncMock()
        fallback.check_rate_limit = AsyncMock(return_value=(False, 0))

        with (
            patch("src.app.core.utils.gcra_rate_limit.get_gcra_rate_limiter", return_value=gcra),
            patch("src.app.core.security.rate_policy.default_rate_limiter", fallback),
            patch("src.app.core.monitoring.r1_unified_metrics.record_fail_open") as mock_record,
        ):
            is_limited, _remaining, _retry, degraded = await rate_policy.consume("k", 5, 60)

        assert degraded is True
        assert is_limited is False
        mock_record.assert_called_once_with("rate_limit:gcra_degraded")

    async def test_an_uninitialised_gcra_limiter_is_counted_and_reported(self) -> None:
        """GCRA configured, limiter never initialised — the same degradation."""
        fallback = AsyncMock()
        fallback.check_rate_limit = AsyncMock(return_value=(False, 0))

        with (
            patch("src.app.core.utils.gcra_rate_limit.get_gcra_rate_limiter", return_value=None),
            patch("src.app.core.security.rate_policy.default_rate_limiter", fallback),
            patch("src.app.core.monitoring.r1_unified_metrics.record_fail_open") as mock_record,
        ):
            _is_limited, _remaining, _retry, degraded = await rate_policy.consume("k", 5, 60)

        assert degraded is True
        mock_record.assert_called_once_with("rate_limit:gcra_uninitialized")

    async def test_the_healthy_path_reports_no_degradation(self) -> None:
        """The signal has to mean something: zero on the happy path."""
        result = MagicMock()
        result.is_limited = False
        result.remaining = 4
        result.retry_after = 0
        gcra = AsyncMock()
        gcra.check_rate_limit = AsyncMock(return_value=result)

        with (
            patch("src.app.core.utils.gcra_rate_limit.get_gcra_rate_limiter", return_value=gcra),
            patch("src.app.core.security.rate_policy.default_rate_limiter", AsyncMock()),
            patch("src.app.core.monitoring.r1_unified_metrics.record_fail_open") as mock_record,
        ):
            _is_limited, _remaining, _retry, degraded = await rate_policy.consume("k", 5, 60)

        assert degraded is False
        mock_record.assert_not_called()

    async def test_the_legacy_three_tuple_contract_is_unchanged(self) -> None:
        """`check_rate_limit_algorithm` keeps its (is_limited, remaining, retry_after).

        The degradation travels on the sibling method, so every existing
        caller — and every injected test engine — keeps working.
        """
        result = MagicMock()
        result.is_limited = False
        result.remaining = 4
        result.retry_after = 0
        gcra = AsyncMock()
        gcra.check_rate_limit = AsyncMock(return_value=result)

        with patch("src.app.core.utils.gcra_rate_limit.get_gcra_rate_limiter", return_value=gcra):
            outcome = await RateLimiter().check_rate_limit_algorithm("k", 5, 60)

        assert len(outcome) == 3
        assert outcome == (False, 4, 0)

    async def test_the_status_method_reports_the_degradation(self) -> None:
        """The sibling method is the one that carries the flag."""
        gcra = AsyncMock()
        gcra.check_rate_limit = AsyncMock(side_effect=RuntimeError("gcra down"))
        fallback = AsyncMock()
        fallback.check_rate_limit = AsyncMock(return_value=(False, 0))

        with (
            patch("src.app.core.utils.gcra_rate_limit.get_gcra_rate_limiter", return_value=gcra),
            patch("src.app.core.utils.rate_limit.RateLimiter.check_rate_limit", AsyncMock(return_value=(False, 0))),
            patch("src.app.core.monitoring.r1_unified_metrics.record_fail_open"),
        ):
            _is_limited, _remaining, _retry, degraded = await RateLimiter().check_rate_limit_algorithm_with_status(
                "k", 5, 60
            )

        assert degraded is True
        assert fallback is not None


class TestVerdictCarriesTheDegradation:
    def test_a_degraded_verdict_says_so_in_the_headers(self) -> None:
        verdict = build_verdict(
            key="k",
            limit=50,
            period=60,
            is_limited=False,
            remaining=49,
            degraded=True,
        )

        assert verdict.headers["X-RateLimit-Algorithm"] == "fixed-window (degraded)"

    def test_a_healthy_verdict_has_no_algorithm_header(self) -> None:
        verdict = build_verdict(
            key="k",
            limit=50,
            period=60,
            is_limited=False,
            remaining=49,
        )

        assert "X-RateLimit-Algorithm" not in verdict.headers


class TestWindowReset:
    def test_the_reset_is_the_remainder_of_the_window(self) -> None:
        """The window reset, not the retry-after.

        The AI adapter used to send `retry_after` as the reset, which is 0 on
        every allowed request — the client was told nothing.
        """
        reset = window_reset_seconds(60)
        assert 1 <= reset <= 60

    def test_a_missing_window_has_no_reset(self) -> None:
        assert window_reset_seconds(None) == 0
        assert window_reset_seconds(0) == 0


class TestTierFallback:
    async def test_an_unconfigured_tier_is_logged_loudly(self) -> None:
        """A NAMED tier that is not configured is a configuration mistake."""
        with (
            patch("src.app.core.security.rate_policy.settings") as settings_mock,
            patch("src.app.core.security.rate_policy.get_tier_policy_limits", AsyncMock(return_value=(None, None))),
            patch("src.app.core.security.rate_policy.logger") as mock_logger,
        ):
            settings_mock.rate_limit.TIER_RATE_LIMITS = {"free": (10, 3600)}
            settings_mock.rate_limit.DEFAULT_AUTHENTICATED_LIMIT = (50, 3600)

            limit, period = await rate_policy.resolve_rate_limits("/x", tier_name="enterprise", user_id="42")

        assert (limit, period) == (50, 3600)
        mock_logger.warning.assert_called_once()
        assert mock_logger.warning.call_args.args[0] == "rate_limit_tier_not_configured"

    async def test_the_anonymous_fallback_is_not_logged(self) -> None:
        """No tier name is not a mistake — it must not spam the warning."""
        with (
            patch("src.app.core.security.rate_policy.settings") as settings_mock,
            patch("src.app.core.security.rate_policy.get_tier_policy_limits", AsyncMock(return_value=(None, None))),
            patch("src.app.core.security.rate_policy.logger") as mock_logger,
        ):
            settings_mock.rate_limit.TIER_RATE_LIMITS = {"free": (10, 3600)}
            settings_mock.rate_limit.DEFAULT_ANONYMOUS_LIMIT = (10, 3600)

            limit, period = await rate_policy.resolve_rate_limits("/x", tier_name=None, user_id=None)

        assert (limit, period) == (10, 3600)
        mock_logger.warning.assert_not_called()


class TestPeriodFloor:
    async def test_a_zero_period_is_floored(self) -> None:
        """A period of 0 makes the 429 carry no Retry-After."""
        with (
            patch("src.app.core.security.rate_policy.settings") as settings_mock,
            patch("src.app.core.security.rate_policy.get_tier_policy_limits", AsyncMock(return_value=(None, None))),
        ):
            settings_mock.rate_limit.TIER_RATE_LIMITS = {"free": (10, 0)}
            settings_mock.rate_limit.DEFAULT_AUTHENTICATED_LIMIT = (50, 3600)

            limit, period = await rate_policy.resolve_rate_limits("/x", tier_name="free", user_id="42")

        assert limit == 10
        assert period == 1
