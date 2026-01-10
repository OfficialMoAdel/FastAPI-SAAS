"""Unit tests for WebhookService business logic.

Tests cover:
- HMAC-SHA256 signature generation and verification
- Retry logic constants
- Service initialization
"""

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch
from uuid import uuid4

import pytest

from src.app.modules._base import ModelNotFoundError
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
    """WebhookService instance."""
    return WebhookService(mock_db)


@pytest.fixture
def mock_webhook_endpoint():
    """Mock WebhookEndpoint object."""
    from uuid import uuid4

    from src.app.modules.webhooks.models import WebhookEndpoint

    endpoint = WebhookEndpoint(
        tenant_id=uuid4(),
        url="https://example.com/webhook",
        event_type="user.created",
        secret="encrypted_secret",
        is_active=True,
    )
    endpoint.id = uuid4()
    return endpoint


@pytest.fixture
def mock_webhook_delivery():
    """Mock WebhookDelivery object."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from src.app.modules.webhooks.models import WebhookDelivery, WebhookDeliveryStatus

    delivery = WebhookDelivery(
        webhook_endpoint_id=uuid4(),
        tenant_id=uuid4(),
        event_type="user.created",
        payload={"user_id": 123},
        signature="sha256=sig",
        status=WebhookDeliveryStatus.PENDING,
        attempt_count=0,
    )
    delivery.id = uuid4()
    delivery.created_at = datetime.now(UTC)
    return delivery


class TestWebhookServiceEndpoints:
    """Tests for webhook endpoint management."""

    @pytest.mark.asyncio
    async def test_create_endpoint_success(self, webhook_service, mock_db):
        """Test creating a new webhook endpoint."""
        tenant_id = "tenant-uuid"

        with patch("src.app.modules.webhooks.service.encrypt_data", return_value="encrypted"):
            result = await webhook_service.create_endpoint(
                tenant_id=tenant_id, url="https://test.com", event_type="user.created", secret="mysecret"
            )

            assert result.url == "https://test.com"
            assert result.secret == "encrypted"
            mock_db.add.assert_called_once()
            mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_active_endpoints(self, webhook_service, mock_db, mock_webhook_endpoint):
        """Test retrieving active endpoints."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_webhook_endpoint]
        mock_db.execute.return_value = mock_result

        result = await webhook_service.get_active_endpoints(
            tenant_id=mock_webhook_endpoint.tenant_id, event_type="user.created"
        )

        assert len(result) == 1
        assert result[0].url == mock_webhook_endpoint.url


class TestWebhookServiceTrigger:
    """Tests for triggering webhooks."""

    @pytest.mark.asyncio
    async def test_trigger_webhooks_success(self, webhook_service, mock_webhook_endpoint):
        """Test triggering webhooks for an event."""
        # Mock get_active_endpoints
        webhook_service.get_active_endpoints = AsyncMock(return_value=[mock_webhook_endpoint])

        # Mock trigger_endpoint
        mock_delivery = MagicMock()
        webhook_service.trigger_endpoint = AsyncMock(return_value=mock_delivery)

        result = await webhook_service.trigger_webhooks(
            tenant_id=mock_webhook_endpoint.tenant_id, event_type="user.created", payload={"id": 1}
        )

        assert len(result) == 1
        webhook_service.trigger_endpoint.assert_called_once()

    @pytest.mark.asyncio
    async def test_trigger_webhooks_partial_failure(self, webhook_service, mock_webhook_endpoint):
        """Test triggering webhooks where some fail."""
        webhook_service.get_active_endpoints = AsyncMock(return_value=[mock_webhook_endpoint, mock_webhook_endpoint])

        # One success, one failure
        mock_delivery = MagicMock()
        webhook_service.trigger_endpoint = AsyncMock(side_effect=[mock_delivery, ValueError("Trigger failed")])

        result = await webhook_service.trigger_webhooks(
            tenant_id=mock_webhook_endpoint.tenant_id, event_type="user.created", payload={"id": 1}
        )

        # Should return only successful deliveries
        assert len(result) == 1
        assert result[0] == mock_delivery

    @pytest.mark.asyncio
    async def test_trigger_endpoint_decryption_error(self, webhook_service, mock_webhook_endpoint):
        """Test trigger fails if secret decryption fails."""
        mock_webhook_endpoint.secret = "bad_secret"

        with patch("src.app.modules.webhooks.service.decrypt_data", side_effect=Exception("Decrypt fail")):
            with pytest.raises(ValueError) as exc:
                await webhook_service.trigger_endpoint(
                    endpoint=mock_webhook_endpoint, event_type="user.created", payload={"id": 1}
                )

            assert "Cannot decrypt webhook secret" in str(exc.value)

    @pytest.mark.asyncio
    async def test_enqueue_delivery_no_pool(self, webhook_service):
        """Test enqueueing when redis pool is not initialized."""
        # Patch the pool import inside the method
        with patch("src.app.core.utils.queue.pool", None):
            await webhook_service._enqueue_delivery(uuid4())
            # Should not raise, just log warning (verified by coverage)


class TestWebhookServiceDelivery:
    """Tests for webhook delivery logic."""

    @pytest.mark.asyncio
    async def test_deliver_webhook_not_found(self, webhook_service, mock_db):
        """Test error when delivery record not found."""
        mock_db.get.return_value = None

        with pytest.raises(ModelNotFoundError) as exc:
            await webhook_service.deliver_webhook(uuid4())

        assert "WebhookDelivery" in str(exc.value)

    @pytest.mark.asyncio
    async def test_deliver_webhook_endpoint_not_found(self, webhook_service, mock_db, mock_webhook_delivery):
        """Test error when endpoint not found."""
        mock_db.get.side_effect = [mock_webhook_delivery, None]

        with pytest.raises(ModelNotFoundError) as exc:
            await webhook_service.deliver_webhook(mock_webhook_delivery.id)

        assert "WebhookEndpoint" in str(exc.value)

    @pytest.mark.asyncio
    async def test_deliver_webhook_circuit_open(
        self, webhook_service, mock_db, mock_webhook_delivery, mock_webhook_endpoint
    ):
        """Test delivery skipped when circuit breaker is open."""
        mock_db.get.side_effect = [mock_webhook_delivery, mock_webhook_endpoint]
        webhook_service._handle_delivery_failure = AsyncMock()

        # Mock circuit breaker to be open
        with patch("src.app.core.utils.circuit_breaker.webhook_circuit") as mock_circuit:
            type(mock_circuit).is_closed = PropertyMock(return_value=False)
            mock_circuit.recovery_timeout = 60
            mock_circuit._last_failure_time = 0

            result = await webhook_service.deliver_webhook(mock_webhook_delivery.id)

            # Should return delivery without making request
            assert result == mock_webhook_delivery
            webhook_service._handle_delivery_failure.assert_called_once()

    @pytest.mark.asyncio
    async def test_deliver_webhook_ssrf_error(
        self, webhook_service, mock_db, mock_webhook_delivery, mock_webhook_endpoint
    ):
        """Test delivery fails on SSRF protection."""
        from src.app.core.security.url_validator import SSRFProtectionError
        from src.app.modules.webhooks.models import WebhookDeliveryStatus

        mock_db.get.side_effect = [mock_webhook_delivery, mock_webhook_endpoint]

        with patch("src.app.modules.webhooks.service.validate_webhook_url", side_effect=SSRFProtectionError("Blocked")):
            result = await webhook_service.deliver_webhook(mock_webhook_delivery.id)

            assert result.status == WebhookDeliveryStatus.FAILED
            assert "SSRF protection" in result.response_body

    @pytest.mark.asyncio
    async def test_deliver_webhook_circuit_exception(
        self, webhook_service, mock_db, mock_webhook_delivery, mock_webhook_endpoint
    ):
        """Test circuit breaker opening during delivery."""
        from src.app.core.utils.circuit_breaker import CircuitBreakerOpen

        mock_db.get.side_effect = [mock_webhook_delivery, mock_webhook_endpoint]
        webhook_service._handle_delivery_failure = AsyncMock()

        with patch("src.app.modules.webhooks.service.validate_webhook_url"):
            # Fix: Provide recovery_in argument (e.g. 60 seconds)
            with patch("httpx.AsyncClient", side_effect=CircuitBreakerOpen("Circuit open", 60)):
                await webhook_service.deliver_webhook(mock_webhook_delivery.id)

                webhook_service._handle_delivery_failure.assert_called_once()
                assert mock_webhook_delivery.attempt_count == 1
                assert "Circuit open" in mock_webhook_delivery.response_body

    @pytest.mark.asyncio
    async def test_deliver_webhook_generic_exception(
        self, webhook_service, mock_db, mock_webhook_delivery, mock_webhook_endpoint
    ):
        """Test generic exception during delivery."""
        mock_db.get.side_effect = [mock_webhook_delivery, mock_webhook_endpoint]
        webhook_service._handle_delivery_failure = AsyncMock()

        with patch("src.app.modules.webhooks.service.validate_webhook_url"):
            with patch("httpx.AsyncClient", side_effect=Exception("Network error")):
                # Mock circuit breaker record_failure
                with patch(
                    "src.app.core.utils.circuit_breaker.webhook_circuit.record_failure", new_callable=AsyncMock
                ) as mock_record:
                    await webhook_service.deliver_webhook(mock_webhook_delivery.id)

                    mock_record.assert_called_once()
                    webhook_service._handle_delivery_failure.assert_called_once()

    @pytest.mark.asyncio
    async def test_deliver_webhook_success(
        self, webhook_service, mock_db, mock_webhook_delivery, mock_webhook_endpoint
    ):
        """Test successful webhook delivery."""
        from src.app.modules.webhooks.models import WebhookDeliveryStatus

        # Mock DB lookups
        mock_db.get.side_effect = [mock_webhook_delivery, mock_webhook_endpoint]

        # Mock validation
        with patch("src.app.modules.webhooks.service.validate_webhook_url"):
            # Mock HTTP request
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "OK"

            mock_client = AsyncMock()
            mock_client.__aenter__.return_value.post.return_value = mock_response

            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await webhook_service.deliver_webhook(mock_webhook_delivery.id)

                assert result.status == WebhookDeliveryStatus.DELIVERED
                assert result.response_status == 200
                mock_db.commit.assert_called()

    @pytest.mark.asyncio
    async def test_deliver_webhook_http_error_retries(
        self, webhook_service, mock_db, mock_webhook_delivery, mock_webhook_endpoint
    ):
        """Test delivery failure with retry scheduling."""
        from src.app.modules.webhooks.models import WebhookDeliveryStatus

        mock_db.get.side_effect = [mock_webhook_delivery, mock_webhook_endpoint]
        webhook_service._enqueue_delivery = AsyncMock()

        with patch("src.app.modules.webhooks.service.validate_webhook_url"):
            mock_response = MagicMock()
            mock_response.status_code = 500

            mock_client = AsyncMock()
            mock_client.__aenter__.return_value.post.return_value = mock_response

            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await webhook_service.deliver_webhook(mock_webhook_delivery.id, attempt=0)

                assert result.status == WebhookDeliveryStatus.PENDING
                assert result.next_retry_at is not None
                webhook_service._enqueue_delivery.assert_called()

    @pytest.mark.asyncio
    async def test_deliver_webhook_max_retries_exceeded(
        self, webhook_service, mock_db, mock_webhook_delivery, mock_webhook_endpoint
    ):
        """Test delivery failure after max retries."""
        from src.app.modules.webhooks.models import WebhookDeliveryStatus

        mock_db.get.side_effect = [mock_webhook_delivery, mock_webhook_endpoint]

        with patch("src.app.modules.webhooks.service.validate_webhook_url"):
            mock_response = MagicMock()
            mock_response.status_code = 500

            mock_client = AsyncMock()
            mock_client.__aenter__.return_value.post.return_value = mock_response

            # Attempt 5 (max is 5, index 4)
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await webhook_service.deliver_webhook(mock_webhook_delivery.id, attempt=5)

                assert result.status == WebhookDeliveryStatus.FAILED

    @pytest.mark.asyncio
    async def test_retry_delivery_not_found(self, webhook_service, mock_db):
        """Test error when retrying non-existent delivery."""
        mock_db.get.return_value = None

        with pytest.raises(ModelNotFoundError) as exc:
            await webhook_service.retry_delivery(uuid4())

        assert "WebhookDelivery" in str(exc.value)

    @pytest.mark.asyncio
    async def test_retry_delivery(self, webhook_service, mock_db, mock_webhook_delivery):
        """Test manual retry of a delivery."""
        from src.app.modules.webhooks.models import WebhookDeliveryStatus

        mock_db.get.return_value = mock_webhook_delivery
        webhook_service._enqueue_delivery = AsyncMock()

        result = await webhook_service.retry_delivery(mock_webhook_delivery.id)

        assert result.status == WebhookDeliveryStatus.PENDING
        assert result.attempt_count == 0
        webhook_service._enqueue_delivery.assert_called_once()

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

    @pytest.mark.asyncio
    async def test_get_delivery_history(self, webhook_service, mock_db, mock_webhook_delivery):
        """Test retrieving delivery history."""
        # Mock the execution result
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_webhook_delivery]
        mock_db.execute.return_value = mock_result

        tenant_id = uuid4()
        result = await webhook_service.get_delivery_history(tenant_id=tenant_id, limit=10, offset=0)

        assert len(result) == 1
        assert result[0] == mock_webhook_delivery
        mock_db.execute.assert_called_once()


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
