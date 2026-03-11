"""Unit tests for WebhookService concurrency and bulk operations.

Tests cover:
- Bulk insertion of delivery records (single transaction)
- Concurrency safety (no shared session race conditions)
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.webhooks.models import WebhookEndpoint
from src.app.modules.webhooks.service import WebhookService


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def webhook_service(mock_db):
    return WebhookService(mock_db)


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
        # We verify it was an INSERT by checking the string representation of the call arg
        assert mock_db.execute.call_count == 1
        call_args = mock_db.execute.call_args[0][0]
        assert "INSERT" in str(call_args)

        # 3. Should have called db.commit ONCE
        mock_db.commit.assert_called_once()

        # 4. Should have enqueued 3 jobs
        assert webhook_service._enqueue_delivery.call_count == 3

        # 5. Should return the 3 deliveries
        assert len(deliveries) == 3
