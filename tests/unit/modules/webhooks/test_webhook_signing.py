"""Unit tests for webhook signing functionality.

Tests cover:
- Service-level signature generation with per-endpoint secrets
- Per-endpoint vs global secret selection
- Auto-generation of signing secrets on endpoint creation
- Signature format and verification
- Timestamp-based replay protection
"""

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from src.app.modules.billing.models import StripeSettings
from src.app.modules.ai.models import AIChatSession

from src.app.modules.webhooks.models import WebhookEndpoint
from src.app.modules.webhooks.service import WebhookService
from src.app.modules.webhooks.utils import generate_signing_secret, verify_webhook_signature


class TestWebhookSigningUtils:
    """Tests for webhook signing utility functions."""

    def test_verify_valid_signature(self) -> None:
        """Test verifying a valid webhook signature with known secret."""
        secret = "test_secret_123"
        payload_dict = {"user_id": "123", "event": "created"}
        payload_bytes = json.dumps(payload_dict, sort_keys=True).encode("utf-8")

        expected_sig = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
        signature = f"sha256={expected_sig}"
        timestamp = int(time.time())

        result = verify_webhook_signature(payload_bytes, signature, timestamp, secret)

        assert result is True

    def test_verify_invalid_signature(self) -> None:
        """Test verifying an invalid webhook signature with wrong secret."""
        correct_secret = "correct_secret"
        wrong_secret = "wrong_secret"
        payload_dict = {"user_id": "123"}
        payload_bytes = json.dumps(payload_dict, sort_keys=True).encode("utf-8")

        expected_sig = hmac.new(correct_secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
        signature = f"sha256={expected_sig}"
        timestamp = int(time.time())

        result = verify_webhook_signature(payload_bytes, signature, timestamp, wrong_secret)

        assert result is False

    def test_verify_expired_timestamp(self) -> None:
        """Test that old timestamps are rejected (replay protection)."""
        secret = "test_secret"
        payload = b'{"user_id": "123"}'

        expected_sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        signature = f"sha256={expected_sig}"

        old_timestamp = int(time.time()) - 360

        result = verify_webhook_signature(payload, signature, old_timestamp, secret)

        assert result is False

    def test_verify_future_timestamp(self) -> None:
        """Test that future timestamps are rejected."""
        secret = "test_secret"
        payload = b'{"user_id": "123"}'

        expected_sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        signature = f"sha256={expected_sig}"

        future_timestamp = int(time.time()) + 360

        result = verify_webhook_signature(payload, signature, future_timestamp, secret)

        assert result is False

    def test_signature_format_sha256_prefix(self) -> None:
        """Verify 'sha256=' prefix is handled correctly."""
        secret = "test_secret"
        payload_dict = {"user_id": "123"}
        payload_bytes = json.dumps(payload_dict, sort_keys=True).encode("utf-8")

        expected_sig = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()

        timestamp = int(time.time())

        with_prefix = f"sha256={expected_sig}"
        result_with = verify_webhook_signature(payload_bytes, with_prefix, timestamp, secret)
        assert result_with is True

        without_prefix = expected_sig
        result_without = verify_webhook_signature(payload_bytes, without_prefix, timestamp, secret)
        assert result_without is True


class TestWebhookServiceSigning:
    """Tests for WebhookService signature generation with per-endpoint secrets."""

    @pytest.fixture
    def sample_payload(self) -> dict:
        """Provide a sample webhook payload for testing."""
        return {
            "event": "user.created",
            "user_id": "12345",
            "email": "test@example.com",
            "timestamp": int(time.time()),
        }

    @pytest.fixture
    def sample_secret(self) -> str:
        """Provide a sample signing secret for testing."""
        return "test_signing_secret_64_chars_hex_encoded_value_here"

    @pytest.fixture
    def mock_db_session(self) -> AsyncMock:
        """Provide a mock database session."""
        return AsyncMock(spec=AsyncSession)

    @pytest.fixture
    def webhook_service(self, mock_db_session: AsyncMock) -> WebhookService:
        """Provide a WebhookService instance with mocked dependencies."""
        return WebhookService(db=mock_db_session)

    def test_generate_signature_format(
        self, webhook_service: WebhookService, sample_payload: dict, sample_secret: str
    ) -> None:
        """Test that _generate_signature produces correct format."""
        signature = webhook_service._generate_signature(sample_payload, sample_secret)

        assert signature.startswith("sha256=")
        assert len(signature) == 71

        hex_part = signature[7:]
        assert len(hex_part) == 64
        assert all(c in "0123456789abcdef" for c in hex_part)

    def test_generate_signature_correctness(
        self, webhook_service: WebhookService, sample_payload: dict, sample_secret: str
    ) -> None:
        """Test that _generate_signature produces correct HMAC-SHA256."""
        signature = webhook_service._generate_signature(sample_payload, sample_secret)

        expected_payload = json.dumps(sample_payload, sort_keys=True).encode("utf-8")
        expected_sig = hmac.new(sample_secret.encode("utf-8"), expected_payload, hashlib.sha256).hexdigest()

        assert signature == f"sha256={expected_sig}"

    def test_generate_signature_consistency(
        self, webhook_service: WebhookService, sample_payload: dict, sample_secret: str
    ) -> None:
        """Test that _generate_signature produces consistent results."""
        sig1 = webhook_service._generate_signature(sample_payload, sample_secret)
        sig2 = webhook_service._generate_signature(sample_payload, sample_secret)

        assert sig1 == sig2

    def test_generate_signature_key_order_independence(
        self, webhook_service: WebhookService, sample_secret: str
    ) -> None:
        """Test that payload key order doesn't affect signature."""
        payload1 = {"a": 1, "b": 2, "c": 3}
        payload2 = {"c": 3, "a": 1, "b": 2}

        sig1 = webhook_service._generate_signature(payload1, sample_secret)
        sig2 = webhook_service._generate_signature(payload2, sample_secret)

        assert sig1 == sig2


class TestPerEndpointSecret:
    """Tests for per-endpoint secret selection and fallback."""

    @pytest.fixture
    def mock_db_session(self) -> AsyncMock:
        """Provide a mock database session."""
        return AsyncMock(spec=AsyncSession)

    @pytest.fixture
    def webhook_service(self, mock_db_session: AsyncMock) -> WebhookService:
        """Provide a WebhookService instance."""
        return WebhookService(db=mock_db_session)

    def test_new_endpoint_has_auto_generated_secret(self) -> None:
        """Test that new endpoint can have auto-generated 64-char hex secret."""
        from uuid6 import uuid7

        endpoint = WebhookEndpoint(
            tenant_id=uuid7(),  # Use UUID, not int
            url="https://example.com/webhook",
            event_type="user.created",
            secret="encrypted_secret_value",
            signing_secret=generate_signing_secret(),  # Explicitly generate
            is_active=True,
        )

        assert endpoint.signing_secret is not None
        assert len(endpoint.signing_secret) == 64
        assert all(c in "0123456789abcdef" for c in endpoint.signing_secret)

    def test_custom_secret_preserved(self) -> None:
        """Test that custom signing secret is preserved when provided."""
        from uuid6 import uuid7

        custom_secret = "custom_secret_value_64_chars_hex_encoded_for_testing"

        endpoint = WebhookEndpoint(
            tenant_id=uuid7(),  # Use UUID, not int
            url="https://example.com/webhook",
            event_type="user.created",
            secret="encrypted_secret_value",
            signing_secret=custom_secret,
            is_active=True,
        )

        assert endpoint.signing_secret == custom_secret

    def test_endpoint_without_signing_secret_uses_provided(self) -> None:
        """Test endpoint can be created with explicit None for signing_secret."""
        from uuid6 import uuid7

        endpoint = WebhookEndpoint(
            tenant_id=uuid7(),  # Use UUID, not int
            url="https://example.com/webhook",
            event_type="user.created",
            secret="encrypted_secret_value",
            signing_secret=None,
            is_active=True,
        )

        assert endpoint.signing_secret is None


class TestSigningSecretGeneration:
    """Tests for signing secret generation."""

    def test_generate_signing_secret_length(self) -> None:
        """Test generated secret is 64 characters."""
        secret = generate_signing_secret()
        assert len(secret) == 64

    def test_generate_signing_secret_is_hex(self) -> None:
        """Test generated secret contains only hex characters."""
        secret = generate_signing_secret()
        assert all(c in "0123456789abcdef" for c in secret)

    def test_generate_signing_secret_uniqueness(self) -> None:
        """Test that multiple generated secrets are unique."""
        secrets = [generate_signing_secret() for _ in range(10)]
        assert len(set(secrets)) == 10

    def test_generate_signing_secret_entropy(self) -> None:
        """Test that secrets have 256 bits of entropy."""
        import binascii

        secret = generate_signing_secret()
        decoded = binascii.unhexlify(secret)
        assert len(decoded) == 32


class TestSignatureVerificationFlow:
    """Integration tests for complete sign-verify flow."""

    @pytest.fixture
    def mock_db_session(self) -> AsyncMock:
        """Provide a mock database session."""
        return AsyncMock(spec=AsyncSession)

    @pytest.fixture
    def webhook_service(self, mock_db_session: AsyncMock) -> WebhookService:
        """Provide a WebhookService instance."""
        return WebhookService(db=mock_db_session)

    def test_end_to_end_sign_and_verify(self, webhook_service: WebhookService) -> None:
        """Test complete flow: generate secret, sign payload, verify signature."""
        secret = generate_signing_secret()
        payload = {"event": "test", "data": "value"}

        signature = webhook_service._generate_signature(payload, secret)

        payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        timestamp = int(time.time())

        result = verify_webhook_signature(payload_bytes, signature, timestamp, secret)

        assert result is True

    def test_tampered_payload_fails(self, webhook_service: WebhookService) -> None:
        """Test that tampered payload fails verification."""
        secret = generate_signing_secret()
        original_payload = {"amount": 100}
        tampered_payload = {"amount": 999}

        signature = webhook_service._generate_signature(original_payload, secret)

        tampered_bytes = json.dumps(tampered_payload, sort_keys=True).encode("utf-8")
        timestamp = int(time.time())

        result = verify_webhook_signature(tampered_bytes, signature, timestamp, secret)

        assert result is False

    def test_wrong_secret_fails_verification(self, webhook_service: WebhookService) -> None:
        """Test that verification with wrong secret fails."""
        correct_secret = generate_signing_secret()
        wrong_secret = generate_signing_secret()
        payload = {"event": "test"}

        signature = webhook_service._generate_signature(payload, correct_secret)

        payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        timestamp = int(time.time())

        result = verify_webhook_signature(payload_bytes, signature, timestamp, wrong_secret)

        assert result is False

    def test_replay_protection_old_timestamp(self, webhook_service: WebhookService) -> None:
        """Test replay protection with old timestamp."""
        secret = generate_signing_secret()
        payload = {"event": "test"}

        signature = webhook_service._generate_signature(payload, secret)
        payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")

        old_timestamp = int(time.time()) - 360

        result = verify_webhook_signature(payload_bytes, signature, old_timestamp, secret)

        assert result is False

    def test_replay_protection_future_timestamp(self, webhook_service: WebhookService) -> None:
        """Test replay protection with future timestamp."""
        secret = generate_signing_secret()
        payload = {"event": "test"}

        signature = webhook_service._generate_signature(payload, secret)
        payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")

        future_timestamp = int(time.time()) + 360

        result = verify_webhook_signature(payload_bytes, signature, future_timestamp, secret)

        assert result is False

    def test_timestamp_tolerance_edge_cases(self, webhook_service: WebhookService) -> None:
        """Test timestamp tolerance boundaries."""
        secret = generate_signing_secret()
        payload = {"event": "test"}

        signature = webhook_service._generate_signature(payload, secret)
        payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        current_time = int(time.time())

        assert verify_webhook_signature(payload_bytes, signature, current_time, secret) is True

        assert verify_webhook_signature(payload_bytes, signature, current_time - 240, secret) is True

        assert verify_webhook_signature(payload_bytes, signature, current_time + 240, secret) is True

        assert verify_webhook_signature(payload_bytes, signature, current_time - 360, secret) is False

        assert verify_webhook_signature(payload_bytes, signature, current_time + 360, secret) is False


class TestWebhookModelSigningSecret:
    """Tests for WebhookEndpoint model signing_secret field."""

    def test_signing_secret_field_exists(self) -> None:
        """Test that signing_secret field exists on WebhookEndpoint model."""
        from uuid6 import uuid7

        endpoint = WebhookEndpoint(
            tenant_id=uuid7(),  # Use UUID, not int
            url="https://test.com",
            event_type="test",
            secret="encrypted_secret_value",
        )

        assert hasattr(endpoint, "signing_secret")

    def test_signing_secret_defaults_to_none(self) -> None:
        """Test that signing_secret defaults to None when not auto-generated."""
        from uuid6 import uuid7

        endpoint = WebhookEndpoint(
            tenant_id=uuid7(),  # Use UUID, not int
            url="https://test.com",
            event_type="test",
            secret="encrypted_secret_value",
            signing_secret=None,
        )

        assert endpoint.signing_secret is None

    def test_signing_secret_can_be_set(self) -> None:
        """Test that signing_secret can be explicitly set."""
        from uuid6 import uuid7

        secret = "a" * 64

        endpoint = WebhookEndpoint(
            tenant_id=uuid7(),  # Use UUID, not int
            url="https://test.com",
            event_type="test",
            secret="encrypted_secret_value",
            signing_secret=secret,
        )

        assert endpoint.signing_secret == secret

    def test_signing_secret_excluded_from_read_schema(self) -> None:
        """Test that signing_secret is excluded from read schema."""
        from src.app.modules.webhooks.schemas import WebhookEndpointRead

        schema_fields = WebhookEndpointRead.model_fields.keys()
        assert "signing_secret" not in schema_fields

    def test_signing_secret_included_in_admin_read(self) -> None:
        """Test that signing_secret is included in admin read schema."""
        from src.app.modules.webhooks.schemas import WebhookEndpointAdminRead

        schema_fields = WebhookEndpointAdminRead.model_fields.keys()
        assert "signing_secret" in schema_fields
