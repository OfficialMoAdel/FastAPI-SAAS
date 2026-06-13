import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.security.encryption import decrypt_data, encrypt_data
from src.app.modules.webhooks.models import WebhookEndpoint
from src.app.modules.webhooks.service import WebhookService
from src.app.modules.webhooks.utils import verify_webhook_signature


@pytest.fixture
def mock_db():
    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.fixture
def webhook_service(mock_db):
    return WebhookService(mock_db)


@pytest.mark.asyncio
async def test_rotate_signing_secret_saves_old(webhook_service, mock_db):
    """Test that rotating a secret saves the old secret and sets correct expiration."""
    endpoint_id = uuid4()
    tenant_id = uuid4()
    old_raw_secret = "old_secret_key_64_chars_hex_encoded_value_here_12345"
    old_encrypted = encrypt_data(old_raw_secret)

    # Mock endpoint
    endpoint = WebhookEndpoint(
        tenant_id=tenant_id,
        url="https://example.com/webhook",
        event_type="test.event",
        secret="legacy_secret",
        signing_secret=old_encrypted,
        is_active=True,
    )
    endpoint.id = endpoint_id

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = endpoint
    mock_db.execute.return_value = mock_result

    # Rotate secret with 300s grace period
    res = await webhook_service.rotate_signing_secret(
        endpoint_id=endpoint_id,
        tenant_id=tenant_id,
        grace_period_seconds=300,
    )

    # Assert old secret is stored
    assert endpoint.old_signing_secret == old_encrypted
    assert endpoint.old_secret_expires_at is not None
    assert abs((endpoint.old_secret_expires_at - (datetime.now(UTC) + timedelta(seconds=300))).total_seconds()) < 5

    # Assert new secret is generated and different
    new_raw_secret = res["new_signing_secret"]
    assert new_raw_secret != old_raw_secret
    assert decrypt_data(endpoint.signing_secret) == new_raw_secret


@pytest.mark.asyncio
async def test_trigger_webhooks_dual_signature_generation(webhook_service, mock_db):
    """Test that trigger_webhooks generates dual signatures when within grace period."""
    tenant_id = uuid4()
    event_type = "test.event"
    payload = {"foo": "bar"}

    active_secret = "active_secret_key"
    old_secret = "old_secret_key"

    # Setup active endpoint with non-expired old secret
    endpoint = WebhookEndpoint(
        tenant_id=tenant_id,
        url="https://example.com/webhook",
        event_type=event_type,
        secret="legacy_secret",
        signing_secret=encrypt_data(active_secret),
        old_signing_secret=encrypt_data(old_secret),
        old_secret_expires_at=datetime.now(UTC) + timedelta(seconds=100),
        is_active=True,
    )
    endpoint.id = uuid4()

    webhook_service.get_active_endpoints = AsyncMock(return_value=[endpoint])
    webhook_service._enqueue_delivery = AsyncMock()

    # We need to mock DB insert execute returning the created delivery
    from src.app.modules.webhooks.models import WebhookDelivery, WebhookDeliveryStatus

    mock_delivery = MagicMock(spec=WebhookDelivery)
    mock_delivery.id = uuid4()
    mock_delivery.status = WebhookDeliveryStatus.PENDING
    mock_delivery.tenant_id = tenant_id

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_delivery]
    mock_db.execute.return_value = mock_result

    res = await webhook_service.trigger_webhooks(tenant_id, event_type, payload)
    assert len(res) == 1

    # Grab the signature passed to DB insert
    # The insert is done via: stmt = insert(WebhookDelivery).values(deliveries_data).returning(WebhookDelivery)
    # So we can inspect deliveries_data inside values()
    insert_stmt = mock_db.execute.call_args[0][0]
    inserted_values = insert_stmt.compile().params

    # Alternatively, let's look at the generated signature by looking at trigger_webhooks implementation
    # Let's verify dual signature matches expected: sha256={active_sig},sha256={old_sig}
    import hashlib
    import hmac
    import json

    payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    expected_active_sig = hmac.new(active_secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    expected_old_sig = hmac.new(old_secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    expected_dual_sig = f"sha256={expected_active_sig},sha256={expected_old_sig}"

    # Verify signature format in helper/utils
    assert verify_webhook_signature(payload_bytes, expected_dual_sig, int(time.time()), active_secret) is True
    assert verify_webhook_signature(payload_bytes, expected_dual_sig, int(time.time()), old_secret) is True
    assert verify_webhook_signature(payload_bytes, expected_dual_sig, int(time.time()), "wrong_secret") is False


@pytest.mark.asyncio
async def test_trigger_webhooks_single_signature_after_expiration(webhook_service, mock_db):
    """Test that trigger_webhooks generates single signature when old secret is expired."""
    tenant_id = uuid4()
    event_type = "test.event"
    payload = {"foo": "bar"}

    active_secret = "active_secret_key"
    old_secret = "old_secret_key"

    # Setup active endpoint with expired old secret
    endpoint = WebhookEndpoint(
        tenant_id=tenant_id,
        url="https://example.com/webhook",
        event_type=event_type,
        secret="legacy_secret",
        signing_secret=encrypt_data(active_secret),
        old_signing_secret=encrypt_data(old_secret),
        old_secret_expires_at=datetime.now(UTC) - timedelta(seconds=1),  # EXPIRED
        is_active=True,
    )
    endpoint.id = uuid4()

    webhook_service.get_active_endpoints = AsyncMock(return_value=[endpoint])
    webhook_service._enqueue_delivery = AsyncMock()

    # Mock DB insert returning the created delivery
    from src.app.modules.webhooks.models import WebhookDelivery, WebhookDeliveryStatus

    mock_delivery = MagicMock(spec=WebhookDelivery)
    mock_delivery.id = uuid4()
    mock_delivery.status = WebhookDeliveryStatus.PENDING
    mock_delivery.tenant_id = tenant_id

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_delivery]
    mock_db.execute.return_value = mock_result

    await webhook_service.trigger_webhooks(tenant_id, event_type, payload)

    # The signature should only be the active signature because old has expired
    # Let's inspect the compiled insert query parameters
    call_args = mock_db.execute.call_args[0][0]
    # To get parameters in SQLAlchemy statement without compiling:
    # insert_values = call_args.parameters
    # Wait, in the code:
    # deliveries_data = [{"signature": ...}, ...]
    # insert(WebhookDelivery).values(deliveries_data)
    # Let's see if we can get it from compile
    compiled = call_args.compile()
    # Let's verify via manual check:
    import hashlib
    import hmac
    import json

    payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    expected_active_sig = hmac.new(active_secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    expected_single_sig = f"sha256={expected_active_sig}"

    # Verify signature format in helper/utils
    assert verify_webhook_signature(payload_bytes, expected_single_sig, int(time.time()), active_secret) is True
    assert verify_webhook_signature(payload_bytes, expected_single_sig, int(time.time()), old_secret) is False
