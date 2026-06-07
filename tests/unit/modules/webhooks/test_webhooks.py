"""Unit tests for webhooks module.

This file consolidates tests from:
- test_webhook_concurrency.py
- test_webhook_models.py
- test_webhook_worker.py
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from arq.worker import Worker

from src.app.core.worker.functions import deliver_webhook_task
from src.app.modules.webhooks.models import WebhookDelivery, WebhookDeliveryStatus, WebhookEndpoint
from src.app.modules.webhooks.service import WebhookService

# =============================================================================
# Fixtures (combined from all files)
# =============================================================================


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def webhook_service(mock_db):
    return WebhookService(mock_db)


@pytest.fixture(autouse=True)
def mock_encryption():
    """Mock encryption to avoid key issues."""
    with (
        patch("src.app.core.security.encryption.encrypt_data") as mock_encrypt,
        patch("src.app.core.security.encryption.decrypt_data") as mock_decrypt,
    ):
        mock_encrypt.return_value = "encrypted_secret"
        mock_decrypt.return_value = b"decrypted_secret"
        yield mock_encrypt, mock_decrypt


@pytest.fixture
def mock_endpoint():
    """Create a mock webhook endpoint."""
    endpoint = MagicMock(spec=WebhookEndpoint)
    endpoint.id = uuid4()
    endpoint.url = "https://example.com/webhook"
    endpoint.secret = "encrypted_secret"  # Already encrypted
    endpoint.tenant_id = uuid4()
    endpoint.event_type = "user.created"
    endpoint.is_active = True
    return endpoint


# =============================================================================
# Tests from test_webhook_concurrency.py
# =============================================================================


@pytest.mark.asyncio
async def test_trigger_webhooks_bulk_insert_concurrency(webhook_service, mock_db):
    """Test that multiple endpoints trigger a SINGLE bulk insert and SINGLE commit.

    This ensures we don't have N+1 transactions and avoids AsyncSession concurrency bugs.
    """
    # Setup 3 active endpoints
    tenant_id = uuid4()
    endpoints = []
    for i in range(3):
        ep = MagicMock(spec=WebhookEndpoint)
        ep.id = uuid4()
        ep.tenant_id = tenant_id
        ep.url = f"https://example.com/{i}"
        ep.event_type = "test.event"
        ep.secret = "enc_secret"
        ep.is_active = True
        endpoints.append(ep)

    # Mock retrieval
    webhook_service.get_active_endpoints = AsyncMock(return_value=endpoints)

    # Mock internal helpers
    webhook_service._generate_signature = MagicMock(return_value="sha256=sig")
    webhook_service._enqueue_delivery = AsyncMock()

    # Mock decrypt to work
    with patch("src.app.modules.webhooks.service.decrypt_data", return_value="secret"):
        # Mock DB execute to return "created" deliveries with IDs
        # We need to simulate the RETURNING clause result
        mock_result = MagicMock()
        mock_deliveries = [MagicMock(id=uuid4()) for _ in range(3)]
        mock_result.scalars.return_value.all.return_value = mock_deliveries
        mock_db.execute.return_value = mock_result

        # EXECUTE
        deliveries = await webhook_service.trigger_webhooks(
            tenant_id=tenant_id, event_type="test.event", payload={"data": 123}
        )

        # ASSERTIONS

        # 1. Should have called get_active_endpoints
        webhook_service.get_active_endpoints.assert_called_once()

        # 2. Should have called db.execute ONCE (the bulk insert)
        assert mock_db.execute.call_count == 1
        call_args = mock_db.execute.call_args[0][0]
        assert "INSERT" in str(call_args)

        # 3. Should have called db.commit ONCE
        mock_db.commit.assert_called_once()

        # 4. Should have enqueued 3 jobs
        assert webhook_service._enqueue_delivery.call_count == 3

        # 5. Should return the 3 deliveries
        assert len(deliveries) == 3


# =============================================================================
# Tests from test_webhook_models.py
# =============================================================================


def test_webhook_delivery_status_enum():
    """Test WebhookDeliveryStatus enum values."""
    assert WebhookDeliveryStatus.PENDING == "pending"
    assert WebhookDeliveryStatus.DELIVERED == "delivered"
    assert WebhookDeliveryStatus.FAILED == "failed"


def test_webhook_endpoint_repr():
    """Test WebhookEndpoint string representation."""
    endpoint_id = uuid4()
    endpoint = WebhookEndpoint(
        tenant_id=1, url="https://example.com", event_type="test.event", secret="secret", is_active=True
    )
    endpoint.id = endpoint_id

    repr_str = repr(endpoint)
    assert str(endpoint_id) in repr_str
    assert "https://example.com" in repr_str
    assert "test.event" in repr_str
    assert "active=True" in repr_str


def test_webhook_delivery_repr():
    """Test WebhookDelivery string representation."""
    delivery_id = uuid4()
    delivery = WebhookDelivery(
        webhook_endpoint_id=uuid4(),
        tenant_id=1,
        payload={},
        signature="sig",
        event_type="test.event",
        status=WebhookDeliveryStatus.PENDING,
        attempt_count=0,
    )
    delivery.id = delivery_id

    repr_str = repr(delivery)
    assert str(delivery_id) in repr_str
    assert "test.event" in repr_str
    assert "pending" in repr_str
    assert "attempts=0" in repr_str


# =============================================================================
# Tests from test_webhook_worker.py
# =============================================================================


@pytest.fixture
def service(mock_db):
    return WebhookService(mock_db)


@pytest.mark.asyncio
async def test_trigger_webhooks_enqueues_job(service, mock_db):
    """Test that trigger_webhooks eventually calls _enqueue_delivery."""
    from src.app.core.security.encryption import encrypt_data

    tenant_id = uuid4()
    event_type = "user.created"
    payload = {"id": 123}

    # Mock endpoints - secret must be encrypted
    encrypted_secret = encrypt_data("test_secret")
    mock_endpoint = MagicMock()
    mock_endpoint.id = uuid4()
    mock_endpoint.secret = encrypted_secret
    mock_endpoint.signing_secret = "test_signing_secret"  # Add signing_secret
    mock_endpoint.tenant_id = tenant_id
    mock_endpoint_result = MagicMock()
    mock_endpoint_result.scalars.return_value.all.return_value = [mock_endpoint]
    mock_db.execute.return_value = mock_endpoint_result

    # Mock _enqueue_delivery and decrypt_data to verify it's called
    with (
        patch.object(service, "_enqueue_delivery", new_callable=AsyncMock) as mock_enqueue,
        patch("src.app.modules.webhooks.service.decrypt_data", return_value="decrypted_secret"),
    ):
        await service.trigger_webhooks(tenant_id, event_type, payload)

        assert mock_enqueue.called
        # Should be called once per endpoint
        assert mock_enqueue.call_count == 1


@pytest.mark.asyncio
async def test_enqueue_delivery_uses_arq(service):
    """Test that _enqueue_delivery uses the global queue pool."""
    delivery_id = uuid4()

    # Mock the pool in src.app.core.utils.queue
    mock_pool = AsyncMock()
    with patch("src.app.core.utils.queue.pool", mock_pool):
        await service._enqueue_delivery(delivery_id)

        mock_pool.enqueue_job.assert_called_once_with("deliver_webhook_task", delivery_id)


@pytest.mark.asyncio
async def test_deliver_webhook_task_execution():
    """Test that the worker task calls the service method."""

    delivery_id = uuid4()
    ctx = MagicMock(spec=Worker)

    # Mock job_bypass_context to return a db that can query tenant_id
    mock_bypass_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (uuid4(),)  # tenant_id tuple
    mock_bypass_db.execute = AsyncMock(return_value=mock_result)

    # Mock job_tenant_context to return a db for webhook delivery
    mock_tenant_db = AsyncMock()

    # Mock WebhookService.deliver_webhook
    mock_service_instance = MagicMock()
    mock_service_instance.deliver_webhook = AsyncMock()

    with patch("src.app.core.worker.functions.job_bypass_context") as mock_bypass_ctx:
        with patch("src.app.core.worker.functions.job_tenant_context") as mock_tenant_ctx:
            with patch("src.app.core.worker.functions.WebhookService") as MockServiceClass:
                # Setup bypass context manager
                mock_bypass_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_bypass_db)
                mock_bypass_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

                # Setup tenant context manager
                mock_tenant_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_tenant_db)
                mock_tenant_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

                # Setup service
                MockServiceClass.return_value = mock_service_instance

                await deliver_webhook_task(ctx, delivery_id)

                # Service should be instantiated
                MockServiceClass.assert_called_once_with(mock_tenant_db)
                # deliver_webhook should be called with the delivery_id
                mock_service_instance.deliver_webhook.assert_called_once_with(delivery_id)
