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
async def test_trigger_webhooks_persists_null_signature_during_grace_period(webhook_service, mock_db):
    """Fix #6: trigger time never signs — rows persist NULL even within rotation grace.

    The rotation dual-signature (t=,v1=<active>,v1=<old>) is produced at SEND
    time in delivery.py; this test pins that live header against the consumer
    verifier for both secrets.
    """
    from src.app.modules.webhooks.signer import sign_payload

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

    # Trigger-time INSERT carries no signature value on the wire: the bound
    # signature param is NULL and no legacy sha256= digest appears among the
    # bound values. (No literal_binds: JSONB payloads have no literal renderer.)
    insert_stmt = mock_db.execute.call_args[0][0]
    bound_params = insert_stmt.compile().params
    assert None in bound_params.values()
    assert not any(isinstance(v, str) and "sha256=" in v for v in bound_params.values())

    # Live rotation contract: send-time header t=,v1=<active>,v1=<old> verifies
    # under BOTH secrets via the consumer adapter, and fails under a wrong one.
    import json

    timestamp = int(time.time())
    active_header = sign_payload(payload, active_secret, timestamp)
    old_digest = sign_payload(payload, old_secret, timestamp).split("v1=", 1)[1]
    rotation_header = f"{active_header},v1={old_digest}"
    payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")

    assert verify_webhook_signature(payload_bytes, rotation_header, timestamp, active_secret) is True
    assert verify_webhook_signature(payload_bytes, rotation_header, timestamp, old_secret) is True
    assert verify_webhook_signature(payload_bytes, rotation_header, timestamp, "wrong_secret") is False


@pytest.mark.asyncio
async def test_trigger_webhooks_persists_null_signature_after_expiration(webhook_service, mock_db):
    """Fix #6: expired rotation changes nothing at trigger time (still NULL).

    At send time only the active secret signs; the resulting single-v1 header
    verifies under the active secret and fails under the expired old secret.
    """
    from src.app.modules.webhooks.signer import sign_payload

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

    insert_stmt = mock_db.execute.call_args[0][0]
    bound_params = insert_stmt.compile().params
    assert None in bound_params.values()
    assert not any(isinstance(v, str) and "sha256=" in v for v in bound_params.values())

    # Live single-secret contract: active-only header.
    import json

    timestamp = int(time.time())
    single_header = sign_payload(payload, active_secret, timestamp)
    payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")

    assert verify_webhook_signature(payload_bytes, single_header, timestamp, active_secret) is True
    assert verify_webhook_signature(payload_bytes, single_header, timestamp, old_secret) is False
