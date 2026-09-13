"""Tests for the P2-#12 webhook subscription lock helpers.

The two-phase lock dance (bypass-context lookup by stripe id, then tenant-
context re-lock by primary key) must preserve the exact semantics the
handlers relied on: one execute per phase, ValueError on missing rows, and
late-binding patch seams on the billing.webhook package namespace.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.billing.models import UserSubscription
from src.app.modules.billing.webhook.locks import (
    SubscriptionRef,
    find_subscription_by_stripe_id,
    lock_subscription_by_id,
)


class AsyncContextManagerMock:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


def _make_mock_sub(tenant_id=None):
    mock_sub = MagicMock()
    mock_sub.tenant_id = tenant_id if tenant_id is not None else uuid4()
    mock_sub.user_id = 1
    mock_sub.id = 1
    mock_sub.stripe_subscription_id = "sub_123"
    return mock_sub


class TestFindSubscriptionByStripeId:
    @pytest.mark.asyncio
    @patch("src.app.modules.billing.webhook.crud_user_subscriptions")
    @patch("src.app.modules.billing.webhook.job_bypass_context")
    async def test_found_row_returns_subscription_ref(self, mock_bypass_ctx, mock_crud_subs):
        """A found row is captured into a SubscriptionRef inside the bypass session,
        with exactly one locked lookup."""
        bypass_db = AsyncMock()
        mock_bypass_ctx.return_value = AsyncContextManagerMock(bypass_db)
        mock_crud_subs.model = UserSubscription

        mock_sub = _make_mock_sub()
        bypass_result = MagicMock()
        bypass_result.scalar_one_or_none.return_value = mock_sub
        bypass_db.execute = AsyncMock(return_value=bypass_result)

        ref = await find_subscription_by_stripe_id("sub_123")

        assert isinstance(ref, SubscriptionRef)
        assert ref.tenant_id is mock_sub.tenant_id
        assert ref.user_id == 1
        assert ref.subscription_id == 1
        # The lookup acquires the row lock (compiled SQL: FOR UPDATE).
        bypass_db.execute.assert_awaited_once()
        compiled = str(bypass_db.execute.call_args[0][0].compile())
        assert "FOR UPDATE" in compiled

    @pytest.mark.asyncio
    @patch("src.app.modules.billing.webhook.crud_user_subscriptions")
    @patch("src.app.modules.billing.webhook.job_bypass_context")
    async def test_missing_row_raises_value_error_for_stripe_retry(
        self, mock_bypass_ctx, mock_crud_subs
    ):
        """A missing row must raise ValueError so Stripe retries delivery."""
        bypass_db = AsyncMock()
        mock_bypass_ctx.return_value = AsyncContextManagerMock(bypass_db)
        mock_crud_subs.model = UserSubscription

        bypass_result = MagicMock()
        bypass_result.scalar_one_or_none.return_value = None
        bypass_db.execute = AsyncMock(return_value=bypass_result)

        with pytest.raises(ValueError, match="Subscription not found for sub_404"):
            await find_subscription_by_stripe_id("sub_404")

    @pytest.mark.asyncio
    @patch("src.app.modules.billing.webhook.crud_user_subscriptions")
    @patch("src.app.modules.billing.webhook.job_bypass_context")
    async def test_uses_patched_package_seam_late_binding(self, mock_bypass_ctx, mock_crud_subs):
        """Patches on the billing.webhook package namespace keep intercepting —
        the helper resolves shared names through _pkg, like the handlers do."""
        bypass_db = AsyncMock()
        mock_bypass_ctx.return_value = AsyncContextManagerMock(bypass_db)
        mock_crud_subs.model = UserSubscription

        mock_sub = _make_mock_sub()
        bypass_result = MagicMock()
        bypass_result.scalar_one_or_none.return_value = mock_sub
        bypass_db.execute = AsyncMock(return_value=bypass_result)

        await find_subscription_by_stripe_id("sub_123")

        mock_bypass_ctx.assert_called_once_with("Webhook subscription lookup")


class TestLockSubscriptionById:
    @pytest.mark.asyncio
    async def test_returns_locked_row(self):
        """The re-lock returns the row found in the tenant-context session."""
        tenant_db = AsyncMock()
        mock_sub = _make_mock_sub()
        lock_result = MagicMock()
        lock_result.scalar_one_or_none.return_value = mock_sub
        tenant_db.execute = AsyncMock(return_value=lock_result)

        row = await lock_subscription_by_id(tenant_db, mock_sub.id)

        assert row is mock_sub
        tenant_db.execute.assert_awaited_once()
        compiled = str(tenant_db.execute.call_args[0][0].compile())
        assert "FOR UPDATE" in compiled

    @pytest.mark.asyncio
    async def test_returns_none_when_row_vanished_in_race_gap(self):
        """A row deleted between phases surfaces as None — callers skip."""
        tenant_db = AsyncMock()
        lock_result = MagicMock()
        lock_result.scalar_one_or_none.return_value = None
        tenant_db.execute = AsyncMock(return_value=lock_result)

        assert await lock_subscription_by_id(tenant_db, 999) is None


class TestLockInvariantMovedToHelpers:
    """P2-#12 invariant: the raw lock queries exist ONLY in locks.py — no
    handler can forget the row lock because handlers no longer express it.

    Source-introspection uses tokenize to strip comments/docstrings, so
    rationale comments mentioning the method name don't trip the check.
    """

    @staticmethod
    def _code_only(module) -> str:
        import io
        import tokenize

        source = inspect.getsource(module)
        tokens = []
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type not in (tokenize.COMMENT, tokenize.STRING):
                tokens.append(tok.string)
        return " ".join(tokens)

    def test_handlers_no_longer_express_raw_lock_queries(self):
        from src.app.modules.billing.webhook.handlers import invoices, refunds, subscriptions

        for module in (subscriptions, invoices, refunds):
            code = self._code_only(module)
            assert ".with_for_update" not in code, (
                f"{module.__name__} still contains a raw with_for_update lock — "
                "route it through webhook.locks (P2-#12)"
            )
            assert "job_bypass_context" not in code, (
                f"{module.__name__} still opens its own bypass lookup — "
                "route it through webhook.locks (P2-#12)"
            )
            assert "find_subscription_by_stripe_id" in code or "lock_subscription_by_id" in code, (
                f"{module.__name__} does not route through webhook.locks (P2-#12)"
            )

    def test_locks_helpers_own_the_lock_queries(self):
        from src.app.modules.billing import webhook as _pkg
        from src.app.modules.billing.webhook import locks

        code = self._code_only(locks)
        assert code.count("with_for_update") == 2
        # Late-binding seam preserved: shared names resolve via the package.
        assert "_pkg . job_bypass_context" in code
        assert "_pkg . crud_user_subscriptions" in code
        # And the helpers are re-exported on the package namespace.
        assert _pkg.find_subscription_by_stripe_id is find_subscription_by_stripe_id
        assert _pkg.lock_subscription_by_id is lock_subscription_by_id
        assert _pkg.SubscriptionRef is SubscriptionRef
