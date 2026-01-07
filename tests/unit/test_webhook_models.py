import pytest
from uuid import uuid4
from datetime import datetime, UTC
from src.app.modules.webhooks.models import WebhookEndpoint, WebhookDelivery, WebhookDeliveryStatus


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
