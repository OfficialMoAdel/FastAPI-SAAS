"""GCRA degradation must be countable, not just logged.

When the GCRA limiter raises, the check silently degrades to the
fixed-window engine. The log line existed and the verdict carried a
``degraded`` flag, but nothing counted it — so operators had no signal
while every auth/MFA budget quietly changed algorithm during a GCRA
outage.

The fixed-window path already calls ``record_fail_open`` on its own
failure (``core/utils/rate_limit.py:75,79``); this closes the gap on the
GCRA side.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.app.core.security import rate_policy


class TestGcraDegradationIsCounted:
    @pytest.mark.asyncio
    async def test_gcra_error_emits_a_fail_open_counter(self) -> None:
        """Pre-fix: a log line, no counter."""
        gcra = AsyncMock()
        gcra.check_rate_limit = AsyncMock(side_effect=RuntimeError("gcra down"))
        fallback = AsyncMock()
        fallback.check_rate_limit = AsyncMock(return_value=(False, 0))

        with (
            patch("src.app.core.utils.gcra_rate_limit.get_gcra_rate_limiter", return_value=gcra),
            patch("src.app.core.security.rate_policy.default_rate_limiter", fallback),
            patch("src.app.core.monitoring.r1_unified_metrics.record_fail_open") as mock_record,
        ):
            is_limited, remaining, retry_after, degraded = await rate_policy.consume(
                "rate_limit:user:42:budget:auth", 5, 60
            )

        mock_record.assert_called_once_with("rate_limit:gcra_degraded")

    @pytest.mark.asyncio
    async def test_degraded_flag_is_still_returned(self) -> None:
        gcra = AsyncMock()
        gcra.check_rate_limit = AsyncMock(side_effect=RuntimeError("gcra down"))
        fallback = AsyncMock()
        fallback.check_rate_limit = AsyncMock(return_value=(False, 0))

        with (
            patch("src.app.core.utils.gcra_rate_limit.get_gcra_rate_limiter", return_value=gcra),
            patch("src.app.core.security.rate_policy.default_rate_limiter", fallback),
            patch("src.app.core.monitoring.r1_unified_metrics.record_fail_open"),
        ):
            _, _, _, degraded = await rate_policy.consume("k", 5, 60)

        assert degraded is True, "the response must still be able to see the degradation"

    @pytest.mark.asyncio
    async def test_healthy_gcra_does_not_count_a_degradation(self) -> None:
        """The counter must mean something: zero on the happy path."""
        result = AsyncMock()
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
            _, _, _, degraded = await rate_policy.consume("k", 5, 60)

        assert degraded is False
        mock_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_gcra_limiter_does_not_count_a_degradation(self) -> None:
        """GCRA not configured is not a degradation."""
        fallback = AsyncMock()
        fallback.check_rate_limit = AsyncMock(return_value=(False, 0))

        with (
            patch("src.app.core.utils.gcra_rate_limit.get_gcra_rate_limiter", return_value=None),
            patch("src.app.core.security.rate_policy.default_rate_limiter", fallback),
            patch("src.app.core.monitoring.r1_unified_metrics.record_fail_open") as mock_record,
        ):
            _, _, _, degraded = await rate_policy.consume("k", 5, 60)

        assert degraded is False
        mock_record.assert_not_called()
