"""Contracts the ARQ event-handler worker must keep.

These pin the behaviours that were wrong before the Cluster A fixes:

- the handler timeout is read BEFORE the `try` that can raise TimeoutError, so
  the timeout diagnosis can never be lost to an UnboundLocalError;
- the retry counter is clamped to the first attempt, so a malformed ctx
  cannot select the LONGEST backoff by indexing the schedule at -1;
- `Retry` is imported at module level, not re-imported on every failure.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from arq.worker import Retry

from src.app.core import events as _pkg
from src.app.core.events import worker as event_worker
from src.app.core.events.worker import process_event_handler


def _handler_module(handler: Any) -> SimpleNamespace:
    """A stand-in for the module the worker imports the handler from."""
    return SimpleNamespace(handler=handler)


async def _run_worker(ctx: dict[str, Any], handler: Any, data: dict[str, Any] | None = None) -> None:
    """Run the worker against a stub handler module."""
    module = _handler_module(handler)
    with patch("importlib.import_module", return_value=module):
        await process_event_handler(ctx, "evt.test", "tests.stub", "handler", data or {})


class TestTimeoutReading:
    async def test_settings_read_failure_keeps_the_original_diagnosis(self) -> None:
        """A1: the timeout read happens outside the `try` that can time out.

        When the read itself raised TimeoutError, the `except TimeoutError`
        handler referenced an unbound `timeout_val`, so the event was DLQ'd
        with "cannot access local variable 'timeout_val'" instead of the real
        cause.
        """

        class ExplodingEvents:
            @property
            def EVENT_HANDLER_TIMEOUT_SECONDS(self) -> float:
                raise TimeoutError("settings read timed out")

        exploding_settings = SimpleNamespace(events=ExplodingEvents())

        handler = AsyncMock()
        with (
            patch("src.app.core.events.settings", exploding_settings),
            patch.object(_pkg.event_bus, "add_to_dlq", new_callable=AsyncMock) as add_to_dlq,
        ):
            await _run_worker({"job_try": _pkg.EventBus.MAX_RETRIES}, handler)

        assert add_to_dlq.await_count == 1
        error = add_to_dlq.await_args.kwargs["error"]
        assert error == "settings read timed out"
        assert "UnboundLocalError" not in error
        handler.assert_not_awaited()


class TestRetryBackoff:
    async def test_malformed_job_try_uses_the_first_delay(self) -> None:
        """A4: `job_try` 0 would index the schedule at -1.

        `delays[min(job_try - 1, len(delays) - 1)]` with job_try=0 selects the
        LAST (longest) delay; the counter is clamped to the first attempt.
        """
        handler = AsyncMock(side_effect=ValueError("boom"))

        with patch.object(_pkg.event_bus, "add_to_dlq", new_callable=AsyncMock):
            with pytest.raises(Retry) as exc_info:
                await _run_worker({"job_try": 0}, handler)

        delays = list(_pkg.settings.events.EVENT_BUS_RETRY_DELAYS) or list(_pkg.EventBus.RETRY_DELAYS)
        # arq stores the defer in milliseconds (`Retry.defer_score`). P3-5
        # jitters the delay within the [0.9, 1.1] x base band, so the first
        # rung is asserted as a band, not an exact value.
        assert delays[0] * 1000 * 0.9 <= exc_info.value.defer_score <= delays[0] * 1000 * 1.1
        assert exc_info.value.defer_score != delays[-1] * 1000

    async def test_negative_job_try_uses_the_first_delay(self) -> None:
        """A negative counter is clamped the same way."""
        handler = AsyncMock(side_effect=ValueError("boom"))

        with patch.object(_pkg.event_bus, "add_to_dlq", new_callable=AsyncMock):
            with pytest.raises(Retry) as exc_info:
                await _run_worker({"job_try": -5}, handler)

        delays = list(_pkg.settings.events.EVENT_BUS_RETRY_DELAYS) or list(_pkg.EventBus.RETRY_DELAYS)
        assert delays[0] * 1000 * 0.9 <= exc_info.value.defer_score <= delays[0] * 1000 * 1.1


class TestModuleSurface:
    def test_retry_is_imported_at_module_level(self) -> None:
        """A5: the import was inside the `except` block."""
        assert event_worker.Retry is Retry
