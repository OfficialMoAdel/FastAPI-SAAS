from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.billing.webhook import (
    _try_acquire_event_lock,
    handle_checkout_session_completed,
    handle_invoice_payment_succeeded,
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
@patch("src.app.modules.billing.webhook.redis_client")
async def test_try_acquire_event_lock_success(mock_redis):
    mock_redis.set = AsyncMock(return_value=True)
    result = await _try_acquire_event_lock("evt_123")
    assert result is True
    mock_redis.set.assert_awaited_once()


@pytest.mark.asyncio
@patch("src.app.modules.billing.webhook.redis_client")
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
@patch("src.app.modules.billing.webhook._try_acquire_event_lock", return_value=True)
@patch("src.app.modules.billing.webhook.handle_checkout_session_completed")
async def test_handle_webhook_event_routing(mock_handler, mock_lock, mock_stripe_event):
    result = await handle_webhook_event(mock_stripe_event)
    assert result == {"status": "success"}
    mock_handler.assert_awaited_once_with(mock_stripe_event)


@pytest.mark.asyncio
@patch("src.app.modules.billing.webhook._try_acquire_event_lock", return_value=True)
@patch("src.app.modules.billing.webhook.redis_client")
async def test_handle_webhook_event_error_releases_lock(mock_redis, mock_lock, mock_stripe_event):
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
@patch("src.app.modules.billing.webhook.job_tenant_context")
@patch("src.app.modules.billing.webhook.crud_user_subscriptions")
@patch("asyncio.to_thread")
async def test_handle_checkout_session_completed(
    mock_to_thread, mock_crud, mock_tenant_context, mock_stripe_event, mock_db
):  # noqa: E501
    mock_tenant_context.return_value = AsyncContextManagerMock(mock_db)

    # Mock stripe.Subscription.retrieve
    mock_to_thread.return_value = {
        "status": "active",
        "current_period_start": 1600000000,
        "current_period_end": 1602592000,
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"recurring": {"interval": "month"}}}]},
    }

    mock_crud.exists = AsyncMock(return_value=False)
    mock_crud.create = AsyncMock()

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

    await handle_invoice_payment_succeeded(event)

    mock_crud_billing.create.assert_awaited_once()
    created_record = mock_crud_billing.create.call_args[1]["object"]
    assert created_record.status == "succeeded"
    assert created_record.amount_cents == 1000
    assert created_record.stripe_invoice_id == "in_123"
