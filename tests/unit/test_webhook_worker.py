from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from arq.worker import Worker

from src.app.core.worker.functions import deliver_webhook_task
from src.app.modules.webhooks.service import WebhookService


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock()
    return db


@pytest.fixture
def service(mock_db):
    return WebhookService(mock_db)


@pytest.fixture(autouse=True)
def mock_encryption():
    """Mock encryption to avoid key issues."""
    with patch("src.app.core.encryption.encrypt_value") as mock_encrypt, \
         patch("src.app.core.encryption.decrypt_value") as mock_decrypt:
        mock_encrypt.return_value = "encrypted_secret"
        mock_decrypt.return_value = b"decrypted_secret"
        yield mock_encrypt, mock_decrypt


@pytest.fixture
def mock_endpoint():
    """Create a mock webhook endpoint."""
    from src.app.modules.webhooks.models import WebhookEndpoint
    endpoint = MagicMock(spec=WebhookEndpoint)
    endpoint.id = uuid4()
    endpoint.url = "https://example.com/webhook"
    endpoint.secret = "encrypted_secret"  # Already encrypted
    endpoint.tenant_id = uuid4()
    endpoint.event_type = "user.created"
    endpoint.is_active = True
    return endpoint


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

    # Mock _enqueue_delivery to verify it's called
    with patch.object(service, "_enqueue_delivery", new_callable=AsyncMock) as mock_enqueue:
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
