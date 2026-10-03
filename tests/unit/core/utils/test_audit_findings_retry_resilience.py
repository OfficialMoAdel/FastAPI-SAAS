"""Internal-audit findings #1-#5 (retries, jitter, fallback, sampling).

Each test pins one behaviour change so it cannot be silently reverted:

#1/#5a  `with_retry` refuses to retry programming errors even when the caller
       passes the `(Exception,)` default; Stripe sites get a real budget.
#2      the webhook ladder jitters, so N endpoints do not re-enqueue together.
#3      the AI fallback tier is retried like the primary tier.
#4      per-event sampling overrides let a rare event opt out of a low global
       rate.
#5      the notification send path no longer inherits `exceptions=(Exception,)`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import structlog

RETRY = Path("src/app/core/utils/retry.py")
DELIVERY = Path("src/app/modules/webhooks/delivery.py")
AI = Path("src/app/modules/ai/service.py")
STRIPE = Path("src/app/clients/billing/stripe.py")
NOTIF = Path("src/app/modules/notifications/service.py")
SAMPLING = Path("src/app/core/observability/log_sampling.py")


class TestNeverRetryGuard:
    """#5a — a bug must not be retried, whatever `exceptions` says."""

    def test_guard_tuple_is_defined(self) -> None:
        src = RETRY.read_text(encoding="utf-8")
        assert "_NEVER_RETRY" in src
        for exc in ("TypeError", "AttributeError", "AssertionError"):
            assert exc in src, f"{exc} must be non-retryable"

    @pytest.mark.asyncio
    async def test_type_error_is_not_retried_under_the_default(self) -> None:
        from src.app.core.utils.retry import with_retry

        calls = {"n": 0}

        async def _boom():
            calls["n"] += 1
            raise TypeError("bug, not a blip")

        # exceptions defaults to (Exception,) — the guard must still win.
        with pytest.raises(TypeError):
            await with_retry(_boom, max_attempts=5, delay_seconds=0.0)

        assert calls["n"] == 1, f"a TypeError was retried {calls['n']} times"

    @pytest.mark.asyncio
    async def test_transient_error_is_still_retried(self) -> None:
        """The guard must not swallow real retryable failures."""
        from src.app.core.utils.retry import with_retry

        calls = {"n": 0}

        async def _flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("transient")
            return "ok"

        assert await with_retry(_flaky, max_attempts=4, delay_seconds=0.0) == "ok"
        assert calls["n"] == 3

    @pytest.mark.asyncio
    async def test_cancellation_still_propagates(self) -> None:
        import asyncio

        from src.app.core.utils.retry import with_retry

        async def _cancelled():
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await with_retry(_cancelled, max_attempts=3, delay_seconds=0.0)


class TestStripeBudget:
    """#1 — a ~2s Stripe blip must no longer be a permanent checkout failure."""

    def test_every_stripe_site_has_a_total_budget(self) -> None:
        src = STRIPE.read_text(encoding="utf-8")
        sites = src.count("with_retry(")
        assert sites >= 5
        assert src.count("max_elapsed=8.0") == sites, (
            "every Stripe retry site needs max_elapsed; the old ladder slept "
            "0.2+0.4 = 0.6s total"
        )

    def test_no_stripe_site_still_uses_the_0_6s_ladder(self) -> None:
        src = STRIPE.read_text(encoding="utf-8")
        assert "delay_seconds=0.2" not in src, "the old 0.6s budget is still present"


class TestWebhookJitter:
    """#2 — the exact ladder re-enqueues every endpoint at the same second."""

    def test_random_is_imported(self) -> None:
        assert re.search(r"^import random$", DELIVERY.read_text(encoding="utf-8"), re.M)

    def test_delay_is_jittered(self) -> None:
        src = DELIVERY.read_text(encoding="utf-8")
        assert "random.uniform(0.9, 1.1)" in src, "the retry delay must be jittered"

    @pytest.mark.asyncio
    async def test_jitter_varies_and_stays_within_10_percent(self) -> None:
        from src.app.modules.webhooks.delivery import _handle_failure

        raw = 60.0
        seen = []
        for _ in range(40):
            d = MagicMock()
            d.id = "id"
            d.tenant_id = None
            d.attempt_count = 1  # first retry -> delays[0] = 60
            with (
                patch("src.app.modules.webhooks.delivery.logger"),
                patch("src.app.modules.webhooks.delivery.get_retry_delays", return_value=[60]),
            ):
                out = await _handle_failure(MagicMock(), d, override_delay=raw)
            assert out is not None
            seen.append(out[1])

        assert len(set(seen)) > 1, "the delay is not jittered at all"
        for value in seen:
            assert 54 <= value <= 66, f"{value}s is outside +/-10% of {raw}s"


class TestFallbackRetried:
    """#3 — the backup provider is retried like the primary."""

    @pytest.mark.asyncio
    async def test_fallback_call_wraps_invoke_chat_in_with_retry(self) -> None:
        src = AI.read_text(encoding="utf-8")
        tree = ast.parse(src)
        fallback = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "_fallback_call"
        )
        calls = [
            ast.unparse(n)
            for n in ast.walk(fallback)
            if isinstance(n, ast.Call) and "_invoke_chat" in ast.unparse(n)
        ]
        assert calls, "_invoke_chat call not found in _fallback_call"
        assert any("with_retry" in c for c in calls), (
            "the fallback tier is single-shot: one transient 500 from the BACKUP "
            "provider discards the request"
        )


class TestPerEventSampling:
    """#4 — a rare event must be able to opt out of a low global rate."""

    @pytest.mark.asyncio
    async def test_override_keeps_a_pinned_event(self) -> None:

        from src.app.core.observability.log_sampling import SamplingProcessor

        p = SamplingProcessor(rate=0.0, rates={"security_denial": 1.0})
        out = p(MagicMock(), "info", {"event": "security_denial"})
        assert out["event"] == "security_denial", "a rate-0 global must not drop a pinned event"

    @pytest.mark.asyncio
    async def test_global_rate_still_applies_to_unpinned_events(self) -> None:
        from src.app.core.observability.log_sampling import SamplingProcessor

        p = SamplingProcessor(rate=0.0, rates={"security_denial": 1.0})
        with pytest.raises(structlog.DropEvent):
            p(MagicMock(), "info", {"event": "http_request"})

    @pytest.mark.asyncio
    async def test_missing_event_key_is_handled(self) -> None:
        from src.app.core.observability.log_sampling import SamplingProcessor

        p = SamplingProcessor(rate=0.0, rates={"x": 1.0})
        with pytest.raises(structlog.DropEvent):
            p(MagicMock(), "info", {"no_event_key": True})

    @pytest.mark.asyncio
    async def test_warning_is_never_sampled_regardless(self) -> None:
        from src.app.core.observability.log_sampling import SamplingProcessor

        p = SamplingProcessor(rate=0.0)
        out = p(MagicMock(), "warning", {"event": "anything"})
        assert out is not None


class TestNotificationSendNoLongerRetriesBugs:
    """#5 — the one site that inherited exceptions=(Exception,)."""

    def test_the_send_path_names_its_exceptions(self) -> None:
        src = NOTIF.read_text(encoding="utf-8")
        block = src[src.index("await with_retry(") :]
        block = block[: block.index(")") + 1]
        assert "exceptions=" in block, (
            "the email send path still inherits exceptions=(Exception,) and "
            "retries TypeError/ValueError with a 1.0+2.0 = 3s ladder"
        )
