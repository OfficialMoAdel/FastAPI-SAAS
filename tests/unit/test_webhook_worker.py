import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4, UUID
from arq.worker import Worker

from src.app.modules.webhooks.service import WebhookService
from src.app.core.worker.functions import deliver_webhook_task

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

@pytest.mark.asyncio
async def test_trigger_webhooks_enqueues_job(service, mock_db):
    """Test that trigger_webhooks eventually calls _enqueue_delivery."""
    tenant_id = uuid4()
    event_type = "user.created"
    payload = {"id": 123}
    
    # Mock endpoints
    mock_endpoint = MagicMock()
    mock_endpoint.id = uuid4()
    mock_endpoint.secret = "secret"
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
        # We need to patch the module-level import inside the function or the module it comes from
        # Since 'from src.app.core.utils.queue import pool' is inside the method, 
        # we patch 'src.app.core.utils.queue.pool' BEFORE calling the method? 
        # No, the method imports it. We must patch 'src.app.core.utils.queue.pool'.
        
        with patch("src.app.core.utils.queue.pool", mock_pool):
             await service._enqueue_delivery(delivery_id)
             
             mock_pool.enqueue_job.assert_called_once_with("deliver_webhook_task", delivery_id)

@pytest.mark.asyncio
async def test_deliver_webhook_task_execution():
    """Test that the worker task calls the service method."""
    delivery_id = uuid4()
    ctx = MagicMock(spec=Worker)
    
    # Mock local_session to return a mock DB
    mock_db_session = AsyncMock()
    mock_db_session.__aenter__.return_value = mock_db_session
    mock_db_session.__aexit__.return_value = None
    
    with patch("src.app.core.worker.functions.local_session", return_value=mock_db_session):
        # Mock WebhookService
        with patch("src.app.core.worker.functions.WebhookService") as MockServiceClass:
            mock_service_instance = MockServiceClass.return_value
            mock_service_instance.deliver_webhook = AsyncMock()
            
            await deliver_webhook_task(ctx, delivery_id)
            
            MockServiceClass.assert_called_once_with(mock_db_session)
            mock_service_instance.deliver_webhook.assert_called_once_with(delivery_id)
