import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe
from fastapi import HTTPException

import src.app.modules.billing.webhook as webhook_module

# Global mock for redis_client, as it might be uninitialized (None) in isolated unit tests
webhook_module.redis_client = AsyncMock()
webhook_module.redis_client.get.return_value = None

from src.app.modules.billing.webhook import (
    handle_webhook_event,
    verify_webhook_signature,
)


def test_verify_webhook_signature_success():
    """Test successful webhook signature verification."""
    payload = b'{"id": "evt_test"}'
    sig_header = "t=123,v1=signature"
    secret = "whsec_test"

    with patch("stripe.Webhook.construct_event") as mock_construct:
        mock_event = {"id": "evt_test", "type": "test.event"}
        mock_construct.return_value = mock_event

        event = verify_webhook_signature(payload, sig_header, secret)

        mock_construct.assert_called_once_with(payload, sig_header, secret)
        assert event == mock_event


def test_verify_webhook_signature_invalid_payload():
    """Test verification with invalid payload."""
    payload = b"invalid-json"
    sig_header = "t=123,v1=signature"
    secret = "whsec_test"

    with patch("stripe.Webhook.construct_event") as mock_construct:
        mock_construct.side_effect = ValueError("Invalid payload")

        with pytest.raises(HTTPException) as exc:
            verify_webhook_signature(payload, sig_header, secret)

        assert exc.value.status_code == 400
        assert "Invalid payload" in exc.value.detail


def test_verify_webhook_signature_invalid_signature():
    """Test verification with invalid signature."""
    payload = b'{"id": "evt_test"}'
    sig_header = "invalid-header"
    secret = "whsec_test"

    with patch("stripe.Webhook.construct_event") as mock_construct:
        mock_construct.side_effect = stripe.error.SignatureVerificationError("Invalid signature", "sig_header")

        with pytest.raises(HTTPException) as exc:
            verify_webhook_signature(payload, sig_header, secret)

        assert exc.value.status_code == 400
        assert "Invalid signature" in exc.value.detail


@pytest.mark.asyncio
async def test_handle_webhook_event_checkout_completed():
    """Test routing for checkout.session.completed."""
    tenant_id = uuid.uuid4()
    user_id = 123  # user_id is expected to be an integer in webhook handler
    plan_id = 456  # plan_id is also expected to be an integer

    event = {
        "id": "evt_test",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test",
                "metadata": {
                    "tenant_id": str(tenant_id),
                    "user_id": str(user_id),  # Integer as string
                    "plan_id": str(plan_id),  # Integer as string
                },
                "subscription": "sub_123",
            }
        },
    }

    # Mock Stripe subscription
    mock_stripe_sub = MagicMock()
    mock_stripe_sub.__getitem__.side_effect = lambda k: {
        "current_period_start": 1600000000,
        "current_period_end": 1600000000 + 3600,
    }[k]
    # Support .get() method
    mock_stripe_sub.get = lambda k, d=None: {
        "current_period_start": 1600000000,
        "current_period_end": 1600000000 + 3600,
        "status": "active",
        "cancel_at_period_end": False,
    }.get(k, d)
    mock_item = MagicMock()
    mock_item.price.recurring.interval = "month"
    mock_stripe_sub.items.data = [mock_item]

    with (
        patch("src.app.modules.billing.webhook.job_tenant_context") as mock_ctx,
        patch("src.app.modules.billing.webhook.crud_user_subscriptions.create", new_callable=AsyncMock) as mock_create,
        patch(
            "src.app.modules.billing.webhook.crud_user_subscriptions.exists", new_callable=AsyncMock, return_value=False
        ) as mock_exists,
        patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread,
    ):  # noqa: E501
        mock_to_thread.return_value = mock_stripe_sub

        # Mock tenant db session
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_ctx.return_value.__aenter__.return_value = mock_db

        result = await handle_webhook_event(event)

        assert result == {"status": "success"}
        mock_ctx.assert_called_once_with(tenant_id)
        mock_exists.assert_called_once()
        mock_create.assert_called_once()
        mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_handle_webhook_event_invoice_payment_succeeded():
    """Test routing and bypass context for invoice.payment_succeeded."""
    tenant_id = uuid.uuid4()
    user_id = 123  # user_id is expected to be an integer

    event = {
        "id": "evt_invoice",
        "type": "invoice.payment_succeeded",
        "data": {
            "object": {
                "id": "in_test",
                "subscription": "sub_123",
                "payment_intent": "pi_123",
                "amount_paid": 1000,
                "currency": "usd",
            }
        },
    }

    with (
        patch("src.app.modules.billing.webhook.job_bypass_context") as mock_bypass_ctx,
        patch("src.app.modules.billing.webhook.job_tenant_context") as mock_tenant_ctx,
        patch("src.app.modules.billing.webhook.crud_billing_records.create", new_callable=AsyncMock) as mock_create,
        patch("src.app.modules.billing.webhook._db_idempotency_check", new_callable=AsyncMock, return_value=None),
    ):
        # Mock bypass db session and subscription finding
        mock_bypass_db = AsyncMock()
        mock_bypass_ctx.return_value.__aenter__.return_value = mock_bypass_db

        mock_result = MagicMock()
        mock_subscription = MagicMock(tenant_id=tenant_id, user_id=user_id, id=100)
        mock_result.scalar_one_or_none.return_value = mock_subscription
        mock_bypass_db.execute.return_value = mock_result

        # Mock tenant db session
        mock_tenant_db = AsyncMock()
        mock_tenant_ctx.return_value.__aenter__.return_value = mock_tenant_db

        result = await handle_webhook_event(event)

        assert result == {"status": "success"}

        # Verify bypass context used for lookup
        mock_bypass_ctx.assert_called_once_with("Webhook subscription lookup")
        mock_bypass_db.execute.assert_called_once()

        # Verify tenant context used for creation
        mock_tenant_ctx.assert_called_once_with(tenant_id)
        mock_create.assert_called_once()
        mock_tenant_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_handle_webhook_event_unhandled():
    """Test routing for unhandled event type."""
    event = {"id": "evt_test", "type": "unknown.event", "data": {"object": {}}}

    result = await handle_webhook_event(event)
    assert result["status"] == "unhandled"  # Unhandled events return "unhandled" status


@pytest.mark.asyncio
async def test_handle_webhook_event_invoice_payment_failed():
    """Test invoice.payment_failed handler."""
    tenant_id = uuid.uuid4()
    user_id = 123  # user_id is expected to be an integer

    event = {
        "id": "evt_failed",
        "type": "invoice.payment_failed",
        "data": {
            "object": {
                "id": "in_fail",
                "subscription": "sub_123",
                "payment_intent": "pi_fail",
                "amount_due": 1000,
                "currency": "usd",
                "last_finalization_error": {"message": "Card declined"},
            }
        },
    }

    with (
        patch("src.app.modules.billing.webhook.job_bypass_context") as mock_bypass_ctx,
        patch("src.app.modules.billing.webhook.job_tenant_context") as mock_tenant_ctx,
        patch("src.app.modules.billing.webhook.crud_billing_records.create", new_callable=AsyncMock) as mock_create,
        patch("src.app.modules.billing.webhook.crud_user_subscriptions.update", new_callable=AsyncMock) as mock_update,
        patch("src.app.modules.billing.webhook._db_idempotency_check", new_callable=AsyncMock, return_value=None),
    ):
        # Mock bypass db
        mock_bypass_db = AsyncMock()
        mock_bypass_ctx.return_value.__aenter__.return_value = mock_bypass_db
        mock_result = MagicMock()
        mock_subscription = MagicMock(tenant_id=tenant_id, user_id=user_id, id=100)
        mock_result.scalar_one_or_none.return_value = mock_subscription
        mock_bypass_db.execute.return_value = mock_result

        # Mock tenant db
        mock_tenant_db = AsyncMock()
        mock_tenant_ctx.return_value.__aenter__.return_value = mock_tenant_db

        result = await handle_webhook_event(event)

        assert result == {"status": "success"}
        mock_create.assert_called_once()  # Billing record
        mock_update.assert_called_once()  # Subscription update to past_due
        mock_tenant_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_handle_webhook_event_subscription_updated():
    """Test customer.subscription.updated handler."""
    tenant_id = uuid.uuid4()
    user_id = 123  # user_id is expected to be an integer

    event = {
        "id": "evt_update",
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": "sub_123",
                "status": "past_due",
                "current_period_start": 1600000000,
                "current_period_end": 1600000000 + 3600,
                "cancel_at_period_end": True,
            }
        },
    }

    with (
        patch("src.app.modules.billing.webhook.job_bypass_context") as mock_bypass_ctx,
        patch("src.app.modules.billing.webhook.job_tenant_context") as mock_tenant_ctx,
        patch("src.app.modules.billing.webhook.crud_user_subscriptions.update", new_callable=AsyncMock) as mock_update,
        patch("src.app.modules.billing.webhook._db_idempotency_check", new_callable=AsyncMock, return_value=None),
    ):
        # Mock bypass db
        mock_bypass_db = AsyncMock()
        mock_bypass_ctx.return_value.__aenter__.return_value = mock_bypass_db
        mock_result = MagicMock()
        mock_subscription = MagicMock(tenant_id=tenant_id, user_id=user_id, id=100)
        mock_result.scalar_one_or_none.return_value = mock_subscription
        mock_bypass_db.execute.return_value = mock_result

        # Mock tenant db
        mock_tenant_db = AsyncMock()
        mock_tenant_ctx.return_value.__aenter__.return_value = mock_tenant_db

        result = await handle_webhook_event(event)

        assert result == {"status": "success"}
        mock_update.assert_called_once()
        mock_tenant_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_handle_webhook_event_subscription_deleted():
    """Test customer.subscription.deleted handler."""
    tenant_id = uuid.uuid4()
    user_id = 123  # user_id is expected to be an integer

    event = {
        "id": "evt_delete",
        "type": "customer.subscription.deleted",
        "data": {"object": {"id": "sub_123", "status": "canceled"}},
    }

    with (
        patch("src.app.modules.billing.webhook.job_bypass_context") as mock_bypass_ctx,
        patch("src.app.modules.billing.webhook.job_tenant_context") as mock_tenant_ctx,
        patch("src.app.modules.billing.webhook.crud_user_subscriptions.update", new_callable=AsyncMock) as mock_update,
        patch("src.app.modules.tenants.crud.crud_tenants.update", new_callable=AsyncMock),
        patch("src.app.modules.billing.webhook._db_idempotency_check", new_callable=AsyncMock, return_value=None),
    ):  # noqa: E501
        # Mock bypass db
        mock_bypass_db = AsyncMock()
        mock_bypass_ctx.return_value.__aenter__.return_value = mock_bypass_db
        mock_result = MagicMock()
        mock_subscription = MagicMock(tenant_id=tenant_id, user_id=user_id, id=100)
        mock_result.scalar_one_or_none.return_value = mock_subscription
        mock_bypass_db.execute.return_value = mock_result

        # Mock tenant db
        mock_tenant_db = AsyncMock()
        mock_tenant_ctx.return_value.__aenter__.return_value = mock_tenant_db
        mock_tenant_result = MagicMock()
        mock_tenant_result.scalar_one_or_none.return_value = MagicMock(id=99)
        mock_tenant_db.execute.return_value = mock_tenant_result

        result = await handle_webhook_event(event)

        assert result == {"status": "success"}
        mock_update.assert_called_once()
        # Verify status set to canceled
        args, kwargs = mock_update.call_args
        assert kwargs["object"].status == "canceled"
        mock_tenant_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_handle_webhook_event_charge_refunded():
    """Test charge.refunded handler."""
    tenant_id = uuid.uuid4()
    user_id = 123  # user_id is expected to be an integer

    event = {
        "id": "evt_refund",
        "type": "charge.refunded",
        "data": {"object": {"id": "ch_123", "invoice": "in_123", "amount_refunded": 500, "currency": "usd"}},
    }

    # Mock invoice retrieval via asyncio.to_thread
    mock_invoice = {"id": "in_123", "subscription": "sub_123"}

    with (
        patch("src.app.modules.billing.webhook.job_bypass_context") as mock_bypass_ctx,
        patch("src.app.modules.billing.webhook.job_tenant_context") as mock_tenant_ctx,
        patch("src.app.modules.billing.webhook.crud_billing_records.create", new_callable=AsyncMock) as mock_create,
        patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread,
        patch("src.app.modules.billing.webhook._db_idempotency_check", new_callable=AsyncMock, return_value=None),
    ):
        mock_to_thread.return_value = mock_invoice

        # Mock bypass db
        mock_bypass_db = AsyncMock()
        mock_bypass_ctx.return_value.__aenter__.return_value = mock_bypass_db
        mock_result = MagicMock()
        mock_subscription = MagicMock(tenant_id=tenant_id, user_id=user_id, id=100)
        mock_result.scalar_one_or_none.return_value = mock_subscription
        mock_bypass_db.execute.return_value = mock_result

        # Mock tenant db
        mock_tenant_db = AsyncMock()
        mock_tenant_ctx.return_value.__aenter__.return_value = mock_tenant_db

        result = await handle_webhook_event(event)

        assert result == {"status": "success"}
        mock_create.assert_called_once()
        # Verify negative amount
        args, kwargs = mock_create.call_args
        assert kwargs["object"].amount_cents == -500
        mock_tenant_db.commit.assert_called_once()
