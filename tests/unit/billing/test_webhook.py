from unittest.mock import patch

import pytest
import stripe
from fastapi import HTTPException

from src.app.modules.billing.webhook import handle_webhook_event, verify_webhook_signature


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
    event = {"id": "evt_test", "type": "checkout.session.completed", "data": {"object": {"id": "cs_test"}}}

    # We haven't implemented specific handlers yet, just verifying it runs without error
    # and logs/returns success
    result = await handle_webhook_event(event)
    assert result == {"status": "success"}


@pytest.mark.asyncio
async def test_handle_webhook_event_unhandled():
    """Test routing for unhandled event type."""
    event = {"id": "evt_test", "type": "unknown.event", "data": {"object": {}}}

    result = await handle_webhook_event(event)
    assert result == {"status": "success"}
