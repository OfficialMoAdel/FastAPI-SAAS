from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from pydantic import SecretStr
from sqlalchemy import select

from src.app.modules.billing.models import BillingRecord, UserSubscription
from tests.conftest import patch_setting


@pytest.fixture
def mock_redis_lifespan():
    """Mock Redis lifespan events to prevent connection errors during tests."""
    mock_redis = AsyncMock()
    mock_redis.exists.return_value = 0  # Not blacklisted
    mock_redis.get.return_value = None
    
    fake_pipeline = AsyncMock()
    fake_pipeline.__aenter__.return_value = fake_pipeline
    fake_pipeline.execute = AsyncMock(return_value=[False, False])
    mock_redis.pipeline = MagicMock(return_value=fake_pipeline)

    with (
        patch("src.app.core.setup.create_redis_queue_pool", new_callable=AsyncMock),
        patch("src.app.core.setup.close_redis_cache_pool", new_callable=AsyncMock),
        patch("src.app.core.setup.close_redis_queue_pool", new_callable=AsyncMock),
        patch("src.app.core.setup.create_redis_cache_pool", new_callable=AsyncMock),
        patch("src.app.core.utils.cache.client", mock_redis),
        patch_setting("STRIPE_WEBHOOK_SECRET", SecretStr("whsec_test")),
    ):
        yield


@pytest.mark.asyncio
async def test_webhook_checkout_flow(mock_redis_lifespan, client, db_session, tenant, create_test_user):
    """
    E2E test for the checkout.session.completed webhook flow.
    Verifies that a UserSubscription is created in the database.
    """
    # Create a user to associate with the subscription
    user = await create_test_user(email="webhook_test@example.com")

    import uuid

    unique_event_id = f"evt_test_checkout_{uuid.uuid4().hex}"

    # Mock Stripe event payload
    event_payload = {
        "id": unique_event_id,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_123",
                "metadata": {"tenant_id": str(tenant.id), "user_id": str(user.id), "plan_id": "1"},
                "subscription": "sub_test_123",
                "customer": "cus_test_123",
            }
        },
    }

    # Mock Stripe Subscription retrieval
    mock_stripe_sub = MagicMock()
    # Support dict and get access for timestamps
    mock_stripe_data = {
        "current_period_start": 1600000000,
        "current_period_end": 1600000000 + 31536000,  # 1 year
        "status": "active",
        "items": {"data": [{"price": {"recurring": {"interval": "year"}}}]},
    }
    mock_stripe_sub.get.side_effect = lambda k, default=None: mock_stripe_data.get(k, default)
    mock_stripe_sub.__getitem__.side_effect = lambda k: mock_stripe_data[k]

    # Mock RLS contexts to use the test session and skip SET LOCAL commands (unsupported in SQLite)
    # We yield the test db_session so the handler uses the same transaction as the test
    @pytest.fixture
    def mock_rls_context():
        class MockContext:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return db_session

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        return MockContext

    # We need to patch where they are imported/used
    # router.py imports verify_webhook_signature, so we must patch it there
    with (
        patch("src.app.modules.billing.router.verify_webhook_signature") as mock_verify,
        patch("src.app.modules.billing.webhook.job_tenant_context") as mock_tenant_ctx,
        patch("stripe.Subscription.retrieve", return_value=mock_stripe_sub),
        patch("src.app.core.db.tenant_crud.get_tenant_context") as mock_get_tenant,
    ):
        # Mock tenant context
        from src.app.core.tenancy.context import TenantContext

        mock_ctx = TenantContext(tenant_id=tenant.id, tenant_slug=tenant.slug, tenant_name=tenant.name, is_active=True)
        mock_get_tenant.return_value = mock_ctx

        # Bypass signature verification
        mock_verify.return_value = event_payload

        # Setup context manager mocks to return the test session
        mock_ctx_instance = AsyncMock()
        mock_ctx_instance.__aenter__.return_value = db_session
        mock_tenant_ctx.return_value = mock_ctx_instance

        # Simulate Webhook Request
        # Note: We send dummy data because we mocked signature verification
        response = client.post("/api/v1/billing/webhook", json=event_payload, headers={"Stripe-Signature": "dummy_sig"})

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "success", "message": None}

        # Verify UserSubscription was created
        # We need to refresh/query the db_session
        stmt = select(UserSubscription).where(UserSubscription.stripe_subscription_id == "sub_test_123")
        result = await db_session.execute(stmt)
        subscription = result.scalar_one_or_none()

        assert subscription is not None
        assert subscription.user_id == user.id
        assert subscription.tenant_id == tenant.id
        assert subscription.billing_interval == "yearly"
        assert subscription.status == "active"


@pytest.mark.asyncio
async def test_webhook_invoice_payment_succeeded_flow(
    mock_redis_lifespan, client, db_session, tenant, create_test_user
):  # noqa: E501
    """
    E2E test for invoice.payment_succeeded flow.
    Verifies that a BillingRecord is created.
    """
    user = await create_test_user(email="invoice_test@example.com")

    # Pre-create a subscription so the handler can find it
    subscription = UserSubscription(
        tenant_id=tenant.id,
        user_id=user.id,
        subscription_plan_id=1,
        stripe_subscription_id="sub_invoice_test",
        status="active",
        billing_interval="monthly",
        current_period_start=datetime.now(UTC),
        current_period_end=datetime.now(UTC),
    )
    db_session.add(subscription)
    await db_session.commit()
    await db_session.refresh(subscription)

    import uuid

    unique_event_id = f"evt_test_invoice_{uuid.uuid4().hex}"

    event_payload = {
        "id": unique_event_id,
        "type": "invoice.payment_succeeded",
        "data": {
            "object": {
                "id": "in_test_123",
                "subscription": "sub_invoice_test",
                "payment_intent": "pi_test_123",
                "amount_paid": 2900,
                "currency": "usd",
            }
        },
    }

    with (
        patch("src.app.modules.billing.router.verify_webhook_signature") as mock_verify,
        patch("src.app.modules.billing.webhook.job_bypass_context") as mock_bypass_ctx,
        patch("src.app.modules.billing.webhook.job_tenant_context") as mock_tenant_ctx,
        patch("src.app.core.db.tenant_crud.get_tenant_context") as mock_get_tenant,
    ):
        mock_verify.return_value = event_payload

        from src.app.core.tenancy.context import TenantContext

        mock_ctx = TenantContext(tenant_id=tenant.id, tenant_slug=tenant.slug, tenant_name=tenant.name, is_active=True)
        mock_get_tenant.return_value = mock_ctx

        # Both contexts use the same test session
        mock_ctx_instance = AsyncMock()
        mock_ctx_instance.__aenter__.return_value = db_session
        mock_bypass_ctx.return_value = mock_ctx_instance
        mock_tenant_ctx.return_value = mock_ctx_instance

        response = client.post("/api/v1/billing/webhook", json=event_payload, headers={"Stripe-Signature": "dummy_sig"})

        assert response.status_code == status.HTTP_200_OK

        # Verify BillingRecord created
        stmt = select(BillingRecord).where(BillingRecord.stripe_invoice_id == "in_test_123")
        result = await db_session.execute(stmt)
        record = result.scalar_one_or_none()

        assert record is not None
        assert record.user_id == user.id
        assert record.amount_cents == 2900
        assert record.status == "succeeded"
