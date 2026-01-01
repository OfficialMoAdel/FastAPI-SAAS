"""Unit tests for WebhookService business logic.

Tests cover:
- HMAC-SHA256 signature generation and verification
- Retry logic constants
- Service initialization
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.modules.webhooks.service import WEBHOOK_RETRY_DELAYS, WebhookService


@pytest.fixture
def mock_db():
    """Mock database session."""
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.get = AsyncMock()
    db.execute = AsyncMock()
    return db


@pytest.fixture
def webhook_service(mock_db):
    """WebhookService instance with mock database."""
    return WebhookService(mock_db)


class TestWebhookServiceInit:
    """Tests for webhook service initialization."""

    def test_service_initialization(self, mock_db) -> None:
        """Test WebhookService initializes with database."""
        service = WebhookService(mock_db)
        assert service.db == mock_db

    def test_service_has_required_methods(self, webhook_service) -> None:
        """Test service has all expected methods."""
        assert hasattr(webhook_service, "create_endpoint")
        assert hasattr(webhook_service, "get_active_endpoints")
        assert hasattr(webhook_service, "trigger_webhooks")
        assert hasattr(webhook_service, "deliver_webhook")
        assert hasattr(webhook_service, "verify_signature")
        assert hasattr(webhook_service, "get_delivery_history")


class TestWebhookServiceSignatures:
    """Tests for HMAC signature generation and verification."""

    def test_generate_signature_format(self, webhook_service) -> None:
        """Test that signature has correct format (sha256=...)."""
        payload = {"user_id": "123", "event": "created"}
        secret = "test-secret"

        signature = webhook_service._generate_signature(payload, secret)

        assert signature.startswith("sha256=")
        assert len(signature) > 10  # sha256= + hexdigest

    def test_generate_signature_consistency(self, webhook_service) -> None:
        """Test that same payload + secret produces same signature."""
        payload = {"user_id": "123", "event": "created"}
        secret = "test-secret"

        sig1 = webhook_service._generate_signature(payload, secret)
        sig2 = webhook_service._generate_signature(payload, secret)

        assert sig1 == sig2

    def test_generate_signature_different_secrets(self, webhook_service) -> None:
        """Test that different secrets produce different signatures."""
        payload = {"user_id": "123", "event": "created"}

        sig1 = webhook_service._generate_signature(payload, "secret1")
        sig2 = webhook_service._generate_signature(payload, "secret2")

        assert sig1 != sig2

    def test_generate_signature_different_payloads(self, webhook_service) -> None:
        """Test that different payloads produce different signatures."""
        secret = "test-secret"

        sig1 = webhook_service._generate_signature({"user_id": "123"}, secret)
        sig2 = webhook_service._generate_signature({"user_id": "456"}, secret)

        assert sig1 != sig2

    @pytest.mark.asyncio
    async def test_verify_signature_valid(self, webhook_service) -> None:
        """Test verifying a valid signature."""
        payload = {"user_id": "123", "event": "created"}
        secret = "test-secret"
        signature = webhook_service._generate_signature(payload, secret)

        result = await webhook_service.verify_signature(payload, signature, secret)

        assert result is True

    @pytest.mark.asyncio
    async def test_verify_signature_invalid(self, webhook_service) -> None:
        """Test verifying an invalid signature."""
        payload = {"user_id": "123", "event": "created"}
        secret = "test-secret"
        wrong_signature = "sha256=invalid"

        result = await webhook_service.verify_signature(payload, wrong_signature, secret)

        assert result is False

    def test_signature_uses_sorted_keys(self, webhook_service) -> None:
        """Test that signature uses sorted keys for consistency."""
        secret = "test-secret"

        # Same payload, different key order
        sig1 = webhook_service._generate_signature({"b": 2, "a": 1}, secret)
        sig2 = webhook_service._generate_signature({"a": 1, "b": 2}, secret)

        assert sig1 == sig2


class TestWebhookServiceRetry:
    """Tests for webhook retry logic."""

    def test_retry_schedule_values(self) -> None:
        """Test retry schedule has correct values (1m, 5m, 15m, 30m, 1h)."""
        expected = [60, 300, 900, 1800, 3600]
        assert expected == WEBHOOK_RETRY_DELAYS

    def test_retry_schedule_count(self) -> None:
        """Test retry schedule has 5 attempts."""
        assert len(WEBHOOK_RETRY_DELAYS) == 5

    def test_total_retry_time(self) -> None:
        """Test total retry time is about 1.5 hours."""
        total_seconds = sum(WEBHOOK_RETRY_DELAYS)
        assert total_seconds == 6660  # 111 minutes total wait

    def test_retry_delays_are_increasing(self) -> None:
        """Test that retry delays increase (exponential backoff)."""
        for i in range(1, len(WEBHOOK_RETRY_DELAYS)):
            assert WEBHOOK_RETRY_DELAYS[i] > WEBHOOK_RETRY_DELAYS[i - 1]
