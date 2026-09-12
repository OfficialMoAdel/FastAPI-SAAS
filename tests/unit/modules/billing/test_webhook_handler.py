from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.billing.webhook import (
    _db_idempotency_check,
    _try_acquire_event_lock,
    handle_charge_refunded,
    handle_checkout_session_completed,
    handle_invoice_payment_succeeded,
    handle_subscription_deleted,
    handle_subscription_updated,
    handle_webhook_event,
    verify_webhook_signature,
)


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def mock_stripe_event():
    return {
        "id": "evt_123",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_123",
                "metadata": {"tenant_id": str(uuid4()), "user_id": "1", "plan_id": "2"},
                "subscription": "sub_123",
            }
        },
    }


@pytest.mark.asyncio
@patch("src.app.core.utils.cache.client")
async def test_try_acquire_event_lock_success(mock_redis):
    mock_redis.set = AsyncMock(return_value=True)
    result = await _try_acquire_event_lock("evt_123")
    assert result is True
    mock_redis.set.assert_awaited_once()


@pytest.mark.asyncio
@patch("src.app.core.utils.cache.client")
async def test_try_acquire_event_lock_already_processed(mock_redis):
    mock_redis.set = AsyncMock(return_value=None)  # None when NX=True and key exists
    result = await _try_acquire_event_lock("evt_123")
    assert result is False


@patch("stripe.Webhook.construct_event")
def test_verify_webhook_signature(mock_construct_event):
    mock_event = MagicMock()
    mock_construct_event.return_value = mock_event

    event = verify_webhook_signature(b"payload", "sig", "secret")
    assert event == mock_event
    mock_construct_event.assert_called_once_with(b"payload", "sig", "secret")


@pytest.mark.asyncio
@patch("src.app.modules.billing.webhook._try_acquire_event_lock", return_value=False)
async def test_handle_webhook_event_already_processed(mock_lock, mock_stripe_event):
    result = await handle_webhook_event(mock_stripe_event)
    assert result == {"status": "already_processed"}


@pytest.mark.asyncio
@patch("src.app.modules.billing.webhook.mark_event_processed", new_callable=AsyncMock)
@patch("src.app.modules.billing.webhook._is_event_processed", new_callable=AsyncMock, return_value=False)
@patch("src.app.modules.billing.webhook._try_acquire_event_lock", return_value=True)
@patch("src.app.modules.billing.webhook.handle_checkout_session_completed")
async def test_handle_webhook_event_routing(mock_handler, mock_lock, _processed, _mark, mock_stripe_event):
    result = await handle_webhook_event(mock_stripe_event)
    assert result == {"status": "success"}
    mock_handler.assert_awaited_once_with(mock_stripe_event)


@pytest.mark.asyncio
@patch("src.app.modules.billing.webhook.mark_event_processed", new_callable=AsyncMock)
@patch("src.app.modules.billing.webhook._is_event_processed", new_callable=AsyncMock, return_value=False)
@patch("src.app.modules.billing.webhook._try_acquire_event_lock", return_value=True)
@patch("src.app.core.utils.cache.client")
async def test_handle_webhook_event_error_releases_lock(mock_redis, mock_lock, _processed, _mark, mock_stripe_event):
    # Route to an unhandled event type which will fail if we patch routing or raise an error
    mock_stripe_event["type"] = "checkout.session.completed"

    with patch(
        "src.app.modules.billing.webhook.handle_checkout_session_completed", side_effect=Exception("Test error")
    ):  # noqa: E501
        mock_redis.delete = AsyncMock()
        with pytest.raises(Exception):
            await handle_webhook_event(mock_stripe_event)

        mock_redis.delete.assert_awaited_once()


# We mock job_tenant_context to just yield mock_db
class AsyncContextManagerMock:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


@pytest.mark.asyncio
@patch(
    "src.app.modules.billing.crud.crud_subscription_plans.get",
    new_callable=AsyncMock,
)
@patch("src.app.modules.billing.webhook.job_tenant_context")
@patch("src.app.modules.billing.webhook.crud_user_subscriptions")
@patch(
    "src.app.modules.billing.gateway.StripeGateway.retrieve_subscription",
    new_callable=AsyncMock,
)
async def test_handle_checkout_session_completed(
    mock_retrieve, mock_crud, mock_tenant_context, mock_plan_get, mock_stripe_event, mock_db
):  # noqa: E501
    # B6/B2-06: the handler validates plan/tenant and user/tenant ownership
    # before minting. The user-membership lookup runs inside a bypass context
    # against the real DB, so on a fresh/empty database it yields NULL and the
    # handler (correctly, fail-closed) rejects. Mock just that lookup to
    # return the event tenant so the success path is hermetic; the Stripe-key
    # resolve and plan-ownership contexts stay real.
    from uuid import UUID as _UUID

    from src.app.modules.billing import webhook as _webhook_pkg

    event_tenant_id = _UUID(mock_stripe_event["data"]["object"]["metadata"]["tenant_id"])
    mock_plan_get.return_value = {"id": 2, "tenant_id": event_tenant_id}

    _real_bypass = _webhook_pkg.job_bypass_context
    _user_row = MagicMock()
    _user_row.scalar_one_or_none.return_value = event_tenant_id
    _user_db = AsyncMock()
    _user_db.execute = AsyncMock(return_value=_user_row)

    def _bypass(label, *args, **kwargs):
        if label == "Checkout user/tenant membership check":
            return AsyncContextManagerMock(_user_db)
        return _real_bypass(label, *args, **kwargs)

    # Mock subscription retrieval via PaymentGateway seam
    mock_retrieve.return_value = {
        "status": "active",
        "current_period_start": 1600000000,
        "current_period_end": 1602592000,
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"recurring": {"interval": "month"}}}]},
    }

    mock_crud.exists = AsyncMock(return_value=False)
    mock_crud.create = AsyncMock()

    with patch("src.app.modules.billing.webhook.job_bypass_context", side_effect=_bypass):
        await handle_checkout_session_completed(mock_stripe_event)

    mock_crud.create.assert_awaited_once()
    assert mock_crud.create.call_args[1]["object"].status == "active"


from src.app.modules.billing.models import UserSubscription


@pytest.mark.asyncio
@patch("src.app.modules.billing.webhook.job_tenant_context")
@patch("src.app.modules.billing.webhook.job_bypass_context")
@patch("src.app.modules.billing.webhook.crud_user_subscriptions")
@patch("src.app.modules.billing.webhook.crud_billing_records")
async def test_handle_invoice_payment_succeeded(
    mock_crud_billing, mock_crud_subs, mock_bypass_ctx, mock_tenant_ctx, mock_db
):  # noqa: E501
    mock_bypass_ctx.return_value = AsyncContextManagerMock(mock_db)
    mock_tenant_ctx.return_value = AsyncContextManagerMock(mock_db)
    mock_crud_subs.model = UserSubscription

    event = {
        "type": "invoice.payment_succeeded",
        "data": {
            "object": {
                "id": "in_123",
                "subscription": "sub_123",
                "payment_intent": "pi_123",
                "amount_paid": 1000,
                "currency": "usd",
            }
        },
    }

    # Mock db.execute finding the subscription
    mock_result = MagicMock()
    mock_sub = MagicMock()
    mock_sub.tenant_id = uuid4()
    mock_sub.user_id = 1
    mock_sub.id = 1
    mock_result.scalar_one_or_none.return_value = mock_sub
    mock_db.execute = AsyncMock(return_value=mock_result)

    mock_crud_billing.create = AsyncMock()
    mock_crud_billing.get = AsyncMock(return_value=None)

    await handle_invoice_payment_succeeded(event)

    mock_crud_billing.create.assert_awaited_once()
    created_record = mock_crud_billing.create.call_args[1]["object"]
    assert created_record.status == "succeeded"
    assert created_record.amount_cents == 1000
    assert created_record.stripe_invoice_id == "in_123"


# ============================================================================
# MISSING COVERAGE TESTS - Lines 44, 52-59, 74-100, 189-190
# ============================================================================


@pytest.mark.asyncio
@patch("src.app.core.utils.cache.client", None)
async def test_try_acquire_event_lock_redis_unavailable(mock_db):
    """Test fallback to DB idempotency check when Redis is unavailable (line 44)."""
    # When redis_client is None, should fall back to DB check
    with patch("src.app.modules.billing.webhook._db_idempotency_check", new_callable=AsyncMock) as mock_db_check:
        mock_db_check.return_value = True
        result = await _try_acquire_event_lock("evt_fallback")
        assert result is True
        mock_db_check.assert_called_once_with("evt_fallback")


@pytest.mark.asyncio
@patch("src.app.core.utils.cache.client")
async def test_try_acquire_event_lock_redis_error(mock_redis):
    """Test Redis error triggers fallback to DB idempotency check (lines 52-59)."""
    mock_redis.set = AsyncMock(side_effect=Exception("Redis connection failed"))

    with patch("src.app.modules.billing.webhook._db_idempotency_check", new_callable=AsyncMock) as mock_db_check:
        mock_db_check.return_value = False  # DB says already processed
        result = await _try_acquire_event_lock("evt_error")

        assert result is False
        mock_db_check.assert_called_once_with("evt_error")


@pytest.mark.asyncio
@patch("src.app.core.utils.cache.client")
async def test_try_acquire_event_lock_redis_exception(mock_redis):
    """Test Redis exception logs error and falls back to DB (lines 52-59)."""
    mock_redis.set = AsyncMock(side_effect=Exception("Network error"))

    with patch("src.app.modules.billing.webhook._db_idempotency_check", new_callable=AsyncMock) as mock_db_check:
        mock_db_check.return_value = True
        result = await _try_acquire_event_lock("evt_exception")

        # Should fall back to DB and return True (process the event)
        assert result is True


@pytest.mark.asyncio
async def test_db_idempotency_check_success():
    """Test DB idempotency check returns True when event is new (lines 74-91)."""
    mock_db = AsyncMock()
    mock_db.commit = AsyncMock()

    with patch("src.app.modules.billing.webhook.job_bypass_context") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("src.app.modules.billing.models.WebhookEvent") as mock_event_class:
            mock_event_instance = MagicMock()
            mock_event_class.return_value = mock_event_instance
            mock_db.add = AsyncMock()

            result = await _db_idempotency_check("evt_new")

            assert result is True
            mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_db_idempotency_check_duplicate():
    """Test DB idempotency check returns False when event is duplicate (lines 85-90)."""
    from sqlalchemy.exc import IntegrityError

    mock_db = AsyncMock()
    mock_db.commit = AsyncMock(side_effect=IntegrityError("duplicate", "duplicate", "duplicate"))
    mock_db.rollback = AsyncMock()

    with patch("src.app.modules.billing.webhook.job_bypass_context") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("src.app.modules.billing.models.WebhookEvent") as mock_event_class:
            mock_event_instance = MagicMock()
            mock_event_class.return_value = mock_event_instance
            mock_db.add = AsyncMock()

            result = await _db_idempotency_check("evt_duplicate")

            assert result is False
            mock_db.rollback.assert_called_once()


@pytest.mark.asyncio
async def test_db_idempotency_check_failure_fails_closed():
    """Test DB idempotency check fails closed when DB also fails (lines 92-100)."""
    from src.app.shared.exceptions import ServiceUnavailableError

    mock_db = AsyncMock()
    mock_db.commit = AsyncMock(side_effect=Exception("DB connection failed"))

    with patch("src.app.modules.billing.webhook.job_bypass_context") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("src.app.modules.billing.models.WebhookEvent") as mock_event_class:
            mock_event_instance = MagicMock()
            mock_event_class.return_value = mock_event_instance
            mock_db.add = AsyncMock()

            # Should fail closed - raise 503 to allow Stripe to retry
            with pytest.raises(ServiceUnavailableError, match="idempotency check"):
                await _db_idempotency_check("evt_error")


@pytest.mark.asyncio
@patch("src.app.core.utils.cache.client")
async def test_handle_webhook_event_error_releases_lock_missing_coverage(mock_redis):
    """Test that failed webhook processing releases the lock (lines 189-190)."""
    mock_redis.delete = AsyncMock()

    mock_stripe_event = {
        "id": "evt_error_test",
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_test", "metadata": {"tenant_id": str(uuid4()), "user_id": "1", "plan_id": "1"}}},
    }

    # First call to check lock returns True (we acquired it)
    with patch("src.app.modules.billing.webhook._try_acquire_event_lock", new_callable=AsyncMock) as mock_lock:
        mock_lock.return_value = True

        # Handler raises an error
        with patch(
            "src.app.modules.billing.webhook.handle_checkout_session_completed",
            side_effect=Exception("Processing failed"),
        ):
            with pytest.raises(Exception, match="Processing failed"):
                await handle_webhook_event(mock_stripe_event)

            # Verify lock was released on error
            mock_redis.delete.assert_called_once()


# ============================================================================
# P0-1 / P1-6 REGRESSION TESTS — tenant-context + re-lock patterns
# ============================================================================


def _make_subscription_updated_event() -> dict:
    """customer.subscription.updated event with items.data[0] period fields
    (Stripe API 2025-03-31.basil+ moved current_period_* to SubscriptionItem)."""
    return {
        "id": "evt_sub_updated",
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": "sub_123",
                "status": "active",
                "current_period_start": 1600000000,
                "current_period_end": 1602592000,
                "cancel_at_period_end": False,
                "items": {"data": [{"current_period_start": 1600000000, "current_period_end": 1602592000}]},
            }
        },
    }


def _make_invoice_event() -> dict:
    return {
        "id": "evt_invoice_paid",
        "type": "invoice.payment_succeeded",
        "data": {
            "object": {
                "id": "in_123",
                "subscription": "sub_123",
                "payment_intent": "pi_123",
                "amount_paid": 1000,
                "currency": "usd",
            }
        },
    }


def _make_mock_sub(tenant_id=None):
    """Mock subscription row with realistic attrs."""
    mock_sub = MagicMock()
    mock_sub.tenant_id = tenant_id if tenant_id is not None else uuid4()
    mock_sub.user_id = 1
    mock_sub.id = 1
    mock_sub.stripe_subscription_id = "sub_123"
    return mock_sub


@pytest.mark.asyncio
@patch("src.app.modules.billing.webhook.job_tenant_context")
@patch("src.app.modules.billing.webhook.job_bypass_context")
@patch("src.app.modules.billing.webhook.crud_user_subscriptions")
async def test_subscription_updated_runs_crud_on_tenant_session(
    mock_crud_subs, mock_bypass_ctx, mock_tenant_ctx
):  # noqa: E501
    """P0-1 regression: tenant-scoped CRUD must run on the job_tenant_context
    session, NOT the bypass session.

    The webhook route is tenant-exempt (no X-Tenant-ID from Stripe), so the
    bypass session has no tenant ContextVar — running TenantAwareCRUD on it
    raised TenantContextError on every event and the sync never applied.
    """
    bypass_db = AsyncMock()
    tenant_db = AsyncMock()
    mock_bypass_ctx.return_value = AsyncContextManagerMock(bypass_db)
    mock_tenant_ctx.return_value = AsyncContextManagerMock(tenant_db)
    mock_crud_subs.model = UserSubscription

    mock_sub = _make_mock_sub()
    bypass_result = MagicMock()
    bypass_result.scalar_one_or_none.return_value = mock_sub
    bypass_db.execute = AsyncMock(return_value=bypass_result)

    # Tenant-context re-lock returns the same row.
    lock_result = MagicMock()
    lock_result.scalar_one_or_none.return_value = mock_sub
    tenant_db.execute = AsyncMock(return_value=lock_result)

    mock_crud_subs.update = AsyncMock()

    await handle_subscription_updated(_make_subscription_updated_event())

    # The CRUD update must use the tenant-context session, not the bypass one.
    mock_crud_subs.update.assert_awaited_once()
    assert mock_crud_subs.update.call_args[1]["db"] is tenant_db
    # Both sessions performed their own row lookups.
    bypass_db.execute.assert_awaited_once()
    tenant_db.execute.assert_awaited_once()
    # The tenant context must be entered with the row's tenant_id.
    mock_tenant_ctx.assert_called_once_with(mock_sub.tenant_id)
    tenant_db.commit.assert_awaited_once()


@pytest.mark.asyncio
@patch("src.app.modules.billing.webhook.job_tenant_context")
@patch("src.app.modules.billing.webhook.job_bypass_context")
@patch("src.app.modules.billing.webhook.crud_user_subscriptions")
async def test_subscription_updated_race_gap_skips_update(
    mock_crud_subs, mock_bypass_ctx, mock_tenant_ctx
):  # noqa: E501
    """P0-1 companion: if the row vanishes between bypass lookup and tenant
    re-lock (race gap), the handler must skip the update and not crash."""
    bypass_db = AsyncMock()
    tenant_db = AsyncMock()
    mock_bypass_ctx.return_value = AsyncContextManagerMock(bypass_db)
    mock_tenant_ctx.return_value = AsyncContextManagerMock(tenant_db)
    mock_crud_subs.model = UserSubscription

    mock_sub = _make_mock_sub()
    bypass_result = MagicMock()
    bypass_result.scalar_one_or_none.return_value = mock_sub
    bypass_db.execute = AsyncMock(return_value=bypass_result)

    # Tenant-context re-lock finds nothing (deleted in the race gap).
    lock_result = MagicMock()
    lock_result.scalar_one_or_none.return_value = None
    tenant_db.execute = AsyncMock(return_value=lock_result)

    mock_crud_subs.update = AsyncMock()

    await handle_subscription_updated(_make_subscription_updated_event())

    mock_crud_subs.update.assert_not_awaited()
    tenant_db.commit.assert_not_awaited()


@pytest.mark.asyncio
@patch("src.app.modules.billing.webhook.job_tenant_context")
@patch("src.app.modules.billing.webhook.job_bypass_context")
@patch("src.app.modules.billing.webhook.crud_user_subscriptions")
@patch("src.app.modules.billing.webhook.crud_billing_records")
async def test_invoice_payment_succeeded_no_resurrection_of_canceled(
    mock_crud_billing, mock_crud_subs, mock_bypass_ctx, mock_tenant_ctx
):  # noqa: E501
    """P1-6 regression: a concurrently canceled subscription must NOT be
    resurrected to active by invoice.payment_succeeded.

    Race scenario: the bypass-context snapshot read shows past_due (lock
    released when that session exited), then customer.subscription.deleted
    cancels the row, then this handler re-locks and must see canceled —
    the blind past_due→active update is forbidden.
    """
    bypass_db = AsyncMock()
    tenant_db = AsyncMock()
    mock_bypass_ctx.return_value = AsyncContextManagerMock(bypass_db)
    mock_tenant_ctx.return_value = AsyncContextManagerMock(tenant_db)
    mock_crud_subs.model = UserSubscription

    # Bypass snapshot: stale past_due read.
    stale_sub = _make_mock_sub()
    stale_sub.status = "past_due"
    bypass_result = MagicMock()
    bypass_result.scalar_one_or_none.return_value = stale_sub
    bypass_db.execute = AsyncMock(return_value=bypass_result)

    # Tenant-context re-lock: fresh canceled status (concurrent deletion won).
    locked_sub = _make_mock_sub()
    locked_sub.status = "canceled"
    lock_result = MagicMock()
    lock_result.scalar_one_or_none.return_value = locked_sub
    tenant_db.execute = AsyncMock(return_value=lock_result)

    mock_crud_subs.update = AsyncMock()
    mock_crud_billing.create = AsyncMock()
    mock_crud_billing.get = AsyncMock(return_value=None)

    await handle_invoice_payment_succeeded(_make_invoice_event())

    # Billing record is still created (payment happened)...
    mock_crud_billing.create.assert_awaited_once()
    # ...but the subscription must NOT be flipped back to active.
    mock_crud_subs.update.assert_not_awaited()
    tenant_db.commit.assert_awaited_once()


@pytest.mark.asyncio
@patch("src.app.modules.billing.webhook.job_tenant_context")
@patch("src.app.modules.billing.webhook.job_bypass_context")
@patch("src.app.modules.billing.webhook.crud_user_subscriptions")
@patch("src.app.modules.billing.webhook.crud_billing_records")
async def test_invoice_payment_succeeded_restores_past_due(
    mock_crud_billing, mock_crud_subs, mock_bypass_ctx, mock_tenant_ctx
):  # noqa: E501
    """P1-6 companion: a genuinely past_due subscription (confirmed under the
    re-lock) must be restored to active after successful payment."""
    bypass_db = AsyncMock()
    tenant_db = AsyncMock()
    mock_bypass_ctx.return_value = AsyncContextManagerMock(bypass_db)
    mock_tenant_ctx.return_value = AsyncContextManagerMock(tenant_db)
    mock_crud_subs.model = UserSubscription

    past_due_sub = _make_mock_sub()
    past_due_sub.status = "past_due"
    bypass_result = MagicMock()
    bypass_result.scalar_one_or_none.return_value = past_due_sub
    bypass_db.execute = AsyncMock(return_value=bypass_result)

    locked_sub = _make_mock_sub()
    locked_sub.status = "past_due"
    lock_result = MagicMock()
    lock_result.scalar_one_or_none.return_value = locked_sub
    tenant_db.execute = AsyncMock(return_value=lock_result)

    mock_crud_subs.update = AsyncMock()
    mock_crud_billing.create = AsyncMock()
    mock_crud_billing.get = AsyncMock(return_value=None)

    await handle_invoice_payment_succeeded(_make_invoice_event())

    mock_crud_billing.create.assert_awaited_once()
    mock_crud_subs.update.assert_awaited_once()
    assert mock_crud_subs.update.call_args[1]["db"] is tenant_db
    assert mock_crud_subs.update.call_args[1]["object"] == {"status": "active"}
    tenant_db.commit.assert_awaited_once()


@pytest.mark.asyncio
@patch("src.app.modules.billing.webhook.job_tenant_context")
@patch("src.app.modules.billing.webhook.job_bypass_context")
@patch("src.app.modules.billing.webhook.crud_user_subscriptions")
@patch("src.app.modules.billing.webhook.crud_billing_records")
async def test_invoice_payment_succeeded_promotes_failed_to_succeeded(
    mock_crud_billing, mock_crud_subs, mock_bypass_ctx, mock_tenant_ctx
):  # noqa: E501
    """P2-3: a prior failed row for a retried invoice must be promoted to
    succeeded, not skipped — otherwise history shows only "failed" forever."""
    bypass_db = AsyncMock()
    tenant_db = AsyncMock()
    mock_bypass_ctx.return_value = AsyncContextManagerMock(bypass_db)
    mock_tenant_ctx.return_value = AsyncContextManagerMock(tenant_db)
    mock_crud_subs.model = UserSubscription

    bypass_result = MagicMock()
    bypass_result.scalar_one_or_none.return_value = _make_mock_sub()
    bypass_db.execute = AsyncMock(return_value=bypass_result)

    lock_result = MagicMock()
    lock_result.scalar_one_or_none.return_value = _make_mock_sub()
    tenant_db.execute = AsyncMock(return_value=lock_result)

    mock_crud_billing.get = AsyncMock(return_value={"id": 7, "status": "failed"})
    mock_crud_billing.create = AsyncMock()
    mock_crud_billing.update = AsyncMock()

    await handle_invoice_payment_succeeded(_make_invoice_event())

    mock_crud_billing.create.assert_not_awaited()
    mock_crud_billing.update.assert_awaited_once()
    assert mock_crud_billing.update.call_args[1]["id"] == 7
    assert mock_crud_billing.update.call_args[1]["object"]["status"] == "succeeded"
    tenant_db.commit.assert_awaited_once()


@pytest.mark.asyncio
@patch(
    "src.app.modules.billing.crud.crud_subscription_plans.get",
    new_callable=AsyncMock,
)
@patch("src.app.modules.billing.webhook.job_tenant_context")
@patch("src.app.modules.billing.webhook.crud_user_subscriptions")
@patch(
    "src.app.modules.billing.gateway.StripeGateway.retrieve_subscription",
    new_callable=AsyncMock,
)
async def test_checkout_session_completed_maps_stripe_status(
    mock_retrieve, mock_crud, mock_tenant_context, mock_plan_get, mock_stripe_event, mock_db
):  # noqa: E501
    """P2-3: checkout must persist the real Stripe subscription status
    (e.g. trialing), not hardcode active."""
    from uuid import UUID as _UUID

    from src.app.modules.billing import webhook as _webhook_pkg

    event_tenant_id = _UUID(mock_stripe_event["data"]["object"]["metadata"]["tenant_id"])
    mock_plan_get.return_value = {"id": 2, "tenant_id": event_tenant_id}

    _real_bypass = _webhook_pkg.job_bypass_context
    _user_row = MagicMock()
    _user_row.scalar_one_or_none.return_value = event_tenant_id
    _user_db = AsyncMock()
    _user_db.execute = AsyncMock(return_value=_user_row)

    def _bypass(label, *args, **kwargs):
        if label == "Checkout user/tenant membership check":
            return AsyncContextManagerMock(_user_db)
        return _real_bypass(label, *args, **kwargs)

    mock_retrieve.return_value = {
        "status": "trialing",
        "current_period_start": 1600000000,
        "current_period_end": 1602592000,
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"recurring": {"interval": "month"}}}]},
    }

    mock_crud.exists = AsyncMock(return_value=False)
    mock_crud.create = AsyncMock()

    with patch("src.app.modules.billing.webhook.job_bypass_context", side_effect=_bypass):
        await handle_checkout_session_completed(mock_stripe_event)

    mock_crud.create.assert_awaited_once()
    assert mock_crud.create.call_args[1]["object"].status == "trialing"


@pytest.mark.asyncio
@patch("src.app.modules.billing.webhook.job_tenant_context")
@patch("src.app.modules.billing.webhook.job_bypass_context")
@patch("src.app.modules.billing.webhook.crud_user_subscriptions")
async def test_subscription_deleted_skips_invalid_transition(
    mock_crud_subs, mock_bypass_ctx, mock_tenant_ctx
):  # noqa: E501
    """P2-3: a delayed subscription.deleted must not rewrite a terminal
    EXPIRED subscription back to canceled (nor re-downgrade the user)."""
    bypass_db = AsyncMock()
    tenant_db = AsyncMock()
    mock_bypass_ctx.return_value = AsyncContextManagerMock(bypass_db)
    mock_tenant_ctx.return_value = AsyncContextManagerMock(tenant_db)
    mock_crud_subs.model = UserSubscription

    bypass_result = MagicMock()
    bypass_result.scalar_one_or_none.return_value = _make_mock_sub()
    bypass_db.execute = AsyncMock(return_value=bypass_result)

    expired_sub = _make_mock_sub()
    expired_sub.status = "expired"
    lock_result = MagicMock()
    lock_result.scalar_one_or_none.return_value = expired_sub
    tenant_db.execute = AsyncMock(return_value=lock_result)

    mock_crud_subs.update = AsyncMock()

    event = {
        "id": "evt_sub_deleted",
        "type": "customer.subscription.deleted",
        "data": {"object": {"id": "sub_123", "canceled_at": 1600000000}},
    }
    await handle_subscription_deleted(event)

    mock_crud_subs.update.assert_not_awaited()
    tenant_db.commit.assert_not_awaited()


def _make_charge_refunded_event(**overrides) -> dict:
    charge = {
        "id": "ch_123",
        "invoice": "in_123",
        "payment_intent": "pi_123",
        "currency": "usd",
        "amount_refunded": 500,
    }
    charge.update(overrides)
    return {
        "id": "evt_charge_refunded",
        "type": "charge.refunded",
        "data": {"object": charge},
    }


@pytest.mark.asyncio
@patch("src.app.modules.billing.gateway.get_payment_gateway")
@patch("src.app.modules.billing.webhook.job_tenant_context")
@patch("src.app.modules.billing.webhook.job_bypass_context")
@patch("src.app.modules.billing.webhook.crud_user_subscriptions")
@patch("src.app.modules.billing.webhook.crud_billing_records")
async def test_charge_refunded_delta_never_writes_invoice_id(
    mock_crud_billing, mock_crud_subs, mock_bypass_ctx, mock_tenant_ctx, mock_get_gateway
):
    """P0-2 regression: refund rows must not carry stripe_invoice_id.

    uq_billing_records_stripe_invoice allows exactly one row per invoice
    (owned by the succeeded/failed row). Writing it on the refund row
    raised IntegrityError -> 500 -> Stripe retry storm.
    """
    mock_get_gateway.return_value.retrieve_invoice = AsyncMock(return_value={"subscription": "sub_123"})
    bypass_db = AsyncMock()
    tenant_db = AsyncMock()
    mock_bypass_ctx.return_value = AsyncContextManagerMock(bypass_db)
    mock_tenant_ctx.return_value = AsyncContextManagerMock(tenant_db)
    mock_crud_subs.model = UserSubscription

    bypass_result = MagicMock()
    bypass_result.scalar_one_or_none.return_value = _make_mock_sub()
    bypass_db.execute = AsyncMock(return_value=bypass_result)

    # No refunds recorded yet for this payment_intent.
    sum_result = MagicMock()
    sum_result.scalar_one.return_value = 0
    tenant_db.execute = AsyncMock(return_value=sum_result)

    mock_crud_billing.create = AsyncMock()

    await handle_charge_refunded(_make_charge_refunded_event())

    mock_crud_billing.create.assert_awaited_once()
    created = mock_crud_billing.create.call_args[1]["object"]
    assert created.status == "refunded"
    assert created.amount_cents == -500
    assert created.stripe_payment_intent_id == "pi_123"
    assert created.stripe_invoice_id is None
    tenant_db.commit.assert_awaited_once()


@pytest.mark.asyncio
@patch("src.app.modules.billing.gateway.get_payment_gateway")
@patch("src.app.modules.billing.webhook.job_tenant_context")
@patch("src.app.modules.billing.webhook.job_bypass_context")
@patch("src.app.modules.billing.webhook.crud_user_subscriptions")
@patch("src.app.modules.billing.webhook.crud_billing_records")
async def test_charge_refunded_itemized_never_writes_invoice_id(
    mock_crud_billing, mock_crud_subs, mock_bypass_ctx, mock_tenant_ctx, mock_get_gateway
):
    """P0-2 regression (itemized branch): per-refund rows link via
    stripe_refund_id, never via stripe_invoice_id."""
    mock_get_gateway.return_value.retrieve_invoice = AsyncMock(return_value={"subscription": "sub_123"})
    bypass_db = AsyncMock()
    tenant_db = AsyncMock()
    mock_bypass_ctx.return_value = AsyncContextManagerMock(bypass_db)
    mock_tenant_ctx.return_value = AsyncContextManagerMock(tenant_db)
    mock_crud_subs.model = UserSubscription

    bypass_result = MagicMock()
    bypass_result.scalar_one_or_none.return_value = _make_mock_sub()
    bypass_db.execute = AsyncMock(return_value=bypass_result)

    mock_crud_billing.exists = AsyncMock(return_value=False)
    mock_crud_billing.create = AsyncMock()

    # P1-11 bulk dedup: the IN-query finds nothing recorded yet.
    in_result = MagicMock()
    in_result.scalars.return_value.all.return_value = []
    tenant_db.execute = AsyncMock(return_value=in_result)

    event = _make_charge_refunded_event(refunds={"data": [{"id": "re_1", "amount": 200}]})
    await handle_charge_refunded(event)

    tenant_db.execute.assert_awaited_once()
    tenant_db.add_all.assert_called_once()
    (rows,) = tenant_db.add_all.call_args[0]
    assert len(rows) == 1
    assert rows[0].stripe_refund_id == "re_1"
    assert rows[0].amount_cents == -200
    assert rows[0].stripe_invoice_id is None
    tenant_db.flush.assert_awaited_once()
    # Bulk path never touches per-row exists/create.
    mock_crud_billing.exists.assert_not_awaited()
    mock_crud_billing.create.assert_not_awaited()
    tenant_db.commit.assert_awaited_once()


@pytest.mark.asyncio
@patch("src.app.modules.billing.gateway.get_payment_gateway")
@patch("src.app.modules.billing.webhook.job_tenant_context")
@patch("src.app.modules.billing.webhook.job_bypass_context")
@patch("src.app.modules.billing.webhook.crud_user_subscriptions")
@patch("src.app.modules.billing.webhook.crud_billing_records")
async def test_charge_refunded_uses_gateway_seam_not_raw_sdk(
    mock_crud_billing, mock_crud_subs, mock_bypass_ctx, mock_tenant_ctx, mock_get_gateway
):
    """P1-9: the invoice lookup must route through the PaymentGateway seam
    (tenant key resolution + with_retry), not raw stripe.Invoice.retrieve."""
    mock_gateway = MagicMock()
    mock_gateway.retrieve_invoice = AsyncMock(return_value={"subscription": "sub_123"})
    mock_get_gateway.return_value = mock_gateway

    bypass_db = AsyncMock()
    tenant_db = AsyncMock()
    mock_bypass_ctx.return_value = AsyncContextManagerMock(bypass_db)
    mock_tenant_ctx.return_value = AsyncContextManagerMock(tenant_db)
    mock_crud_subs.model = UserSubscription

    bypass_result = MagicMock()
    bypass_result.scalar_one_or_none.return_value = _make_mock_sub()
    bypass_db.execute = AsyncMock(return_value=bypass_result)

    sum_result = MagicMock()
    sum_result.scalar_one.return_value = 0
    tenant_db.execute = AsyncMock(return_value=sum_result)

    mock_crud_billing.create = AsyncMock()

    await handle_charge_refunded(_make_charge_refunded_event())

    mock_gateway.retrieve_invoice.assert_awaited_once_with("in_123")
    mock_crud_billing.create.assert_awaited_once()


@pytest.mark.asyncio
@patch("src.app.modules.billing.gateway.get_payment_gateway")
@patch("src.app.modules.billing.webhook.job_tenant_context")
@patch("src.app.modules.billing.webhook.job_bypass_context")
@patch("src.app.modules.billing.webhook.crud_user_subscriptions")
@patch("src.app.modules.billing.webhook.crud_billing_records")
async def test_charge_refunded_gateway_failure_still_raises_for_retry(
    mock_crud_billing, mock_crud_subs, mock_bypass_ctx, mock_tenant_ctx, mock_get_gateway
):
    """P1-9: a gateway failure must still surface as ValueError (5xx) so
    Stripe redelivers — the old raw-call contract is preserved."""
    from src.app.shared.exceptions import StripeError

    mock_gateway = MagicMock()
    mock_gateway.retrieve_invoice = AsyncMock(side_effect=StripeError("stripe down"))
    mock_get_gateway.return_value = mock_gateway

    with pytest.raises(ValueError, match="Failed to retrieve invoice in_123"):
        await handle_charge_refunded(_make_charge_refunded_event())


@pytest.mark.asyncio
@patch("src.app.modules.billing.gateway.get_payment_gateway")
@patch("src.app.modules.billing.webhook.job_tenant_context")
@patch("src.app.modules.billing.webhook.job_bypass_context")
@patch("src.app.modules.billing.webhook.crud_user_subscriptions")
@patch("src.app.modules.billing.webhook.crud_billing_records")
async def test_charge_refunded_integrity_error_skips_not_500s(
    mock_crud_billing, mock_crud_subs, mock_bypass_ctx, mock_tenant_ctx, mock_get_gateway
):
    """P0-2 regression: a concurrent redelivery that loses the dedup race
    must skip (rollback + return), not raise into a 500/endpoint-disable."""
    from sqlalchemy.exc import IntegrityError

    mock_get_gateway.return_value.retrieve_invoice = AsyncMock(return_value={"subscription": "sub_123"})
    bypass_db = AsyncMock()
    tenant_db = AsyncMock()
    mock_bypass_ctx.return_value = AsyncContextManagerMock(bypass_db)
    mock_tenant_ctx.return_value = AsyncContextManagerMock(tenant_db)
    mock_crud_subs.model = UserSubscription

    bypass_result = MagicMock()
    bypass_result.scalar_one_or_none.return_value = _make_mock_sub()
    bypass_db.execute = AsyncMock(return_value=bypass_result)

    sum_result = MagicMock()
    sum_result.scalar_one.return_value = 0
    tenant_db.execute = AsyncMock(return_value=sum_result)

    mock_crud_billing.create = AsyncMock(
        side_effect=IntegrityError("INSERT INTO billing_records", {}, Exception("uq_billing_records_stripe_invoice"))
    )

    # Must not raise.
    await handle_charge_refunded(_make_charge_refunded_event())

    tenant_db.rollback.assert_awaited_once()
    tenant_db.commit.assert_not_awaited()


@pytest.mark.asyncio
@patch("src.app.modules.billing.gateway.get_payment_gateway")
@patch("src.app.modules.billing.webhook.job_tenant_context")
@patch("src.app.modules.billing.webhook.job_bypass_context")
@patch("src.app.modules.billing.webhook.crud_user_subscriptions")
@patch("src.app.modules.billing.webhook.crud_billing_records")
async def test_charge_refunded_bulk_skips_already_recorded(
    mock_crud_billing, mock_crud_subs, mock_bypass_ctx, mock_tenant_ctx, mock_get_gateway
):
    """P1-11: one IN-query replaces N exists(); only unrecorded refunds are inserted."""
    mock_get_gateway.return_value.retrieve_invoice = AsyncMock(return_value={"subscription": "sub_123"})
    bypass_db = AsyncMock()
    tenant_db = AsyncMock()
    mock_bypass_ctx.return_value = AsyncContextManagerMock(bypass_db)
    mock_tenant_ctx.return_value = AsyncContextManagerMock(tenant_db)
    mock_crud_subs.model = UserSubscription

    bypass_result = MagicMock()
    bypass_result.scalar_one_or_none.return_value = _make_mock_sub()
    bypass_db.execute = AsyncMock(return_value=bypass_result)

    in_result = MagicMock()
    in_result.scalars.return_value.all.return_value = ["re_1"]
    tenant_db.execute = AsyncMock(return_value=in_result)

    mock_crud_billing.exists = AsyncMock()
    mock_crud_billing.create = AsyncMock()

    event = _make_charge_refunded_event(
        refunds={"data": [{"id": "re_1", "amount": 200}, {"id": "re_2", "amount": 300}]}
    )
    await handle_charge_refunded(event)

    tenant_db.execute.assert_awaited_once()
    (rows,) = tenant_db.add_all.call_args[0]
    assert [r.stripe_refund_id for r in rows] == ["re_2"]
    tenant_db.commit.assert_awaited_once()


@pytest.mark.asyncio
@patch("src.app.modules.billing.gateway.get_payment_gateway")
@patch("src.app.modules.billing.webhook.job_tenant_context")
@patch("src.app.modules.billing.webhook.job_bypass_context")
@patch("src.app.modules.billing.webhook.crud_user_subscriptions")
@patch("src.app.modules.billing.webhook.crud_billing_records")
async def test_charge_refunded_bulk_race_falls_back_to_per_row(
    mock_crud_billing, mock_crud_subs, mock_bypass_ctx, mock_tenant_ctx, mock_get_gateway
):
    """P1-11: a bulk flush that loses the redelivery race rolls back and
    retries per-row (exists + create), skipping instead of 500ing."""
    from sqlalchemy.exc import IntegrityError

    mock_get_gateway.return_value.retrieve_invoice = AsyncMock(return_value={"subscription": "sub_123"})
    bypass_db = AsyncMock()
    tenant_db = AsyncMock()
    mock_bypass_ctx.return_value = AsyncContextManagerMock(bypass_db)
    mock_tenant_ctx.return_value = AsyncContextManagerMock(tenant_db)
    mock_crud_subs.model = UserSubscription

    bypass_result = MagicMock()
    bypass_result.scalar_one_or_none.return_value = _make_mock_sub()
    bypass_db.execute = AsyncMock(return_value=bypass_result)

    in_result = MagicMock()
    in_result.scalars.return_value.all.return_value = []
    tenant_db.execute = AsyncMock(return_value=in_result)
    tenant_db.flush = AsyncMock(
        side_effect=IntegrityError("INSERT INTO billing_records", {}, Exception("uq_billing_records_stripe_refund"))
    )

    # Race winner's row is now visible: re_1 exists, re_2 still new.
    mock_crud_billing.exists = AsyncMock(side_effect=[True, False])
    mock_crud_billing.create = AsyncMock()

    event = _make_charge_refunded_event(
        refunds={"data": [{"id": "re_1", "amount": 200}, {"id": "re_2", "amount": 300}]}
    )
    await handle_charge_refunded(event)

    assert mock_crud_billing.exists.await_count == 2
    mock_crud_billing.create.assert_awaited_once()
    created = mock_crud_billing.create.call_args[1]["object"]
    assert created.stripe_refund_id == "re_2"
    tenant_db.commit.assert_awaited_once()
