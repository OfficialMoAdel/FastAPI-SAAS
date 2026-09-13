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
        signature=None,
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

        with (
            patch("src.app.modules.webhooks.service.encrypt_data", return_value="encrypted"),
            patch("src.app.modules.webhooks.utils.validate_url_ssrf"),
        ):
            result = await webhook_service.create_endpoint(
                tenant_id=tenant_id, url="https://test.com", event_type="user.created"
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
    async def test_trigger_webhooks_success(self, webhook_service, mock_webhook_endpoint, mock_db):
        """Test triggering webhooks for an event - creates deliveries and returns them."""
        from src.app.modules.webhooks.models import WebhookDelivery, WebhookDeliveryStatus

        # Mock get_active_endpoints to return our endpoint
        webhook_service.get_active_endpoints = AsyncMock(return_value=[mock_webhook_endpoint])

        # Mock decrypt_data
        with patch("src.app.modules.webhooks.service.decrypt_data", return_value="decrypted_secret"):
            # Mock the DB execute to return deliveries
            mock_delivery = MagicMock(spec=WebhookDelivery)
            mock_delivery.id = uuid4()
            mock_delivery.status = WebhookDeliveryStatus.PENDING

            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_delivery]
            mock_db.execute = AsyncMock(return_value=mock_result)

            # Mock enqueue via the delivery module (what trigger actually calls)
            with patch("src.app.modules.webhooks.delivery.enqueue", new_callable=AsyncMock) as mock_enqueue:
                result = await webhook_service.trigger_webhooks(
                    tenant_id=mock_webhook_endpoint.tenant_id, event_type="user.created", payload={"id": 1}
                )

        assert len(result) == 1
        assert result[0] == mock_delivery
        mock_db.commit.assert_called_once()
        mock_enqueue.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_trigger_webhooks_no_endpoints_returns_empty(self, webhook_service, mock_db):
        """Test triggering webhooks when no active endpoints exist."""
        # Mock get_active_endpoints to return empty
        webhook_service.get_active_endpoints = AsyncMock(return_value=[])

        result = await webhook_service.trigger_webhooks(tenant_id=uuid4(), event_type="user.created", payload={"id": 1})

        assert result == []
        mock_db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_trigger_webhooks_persists_null_signature(
        self, webhook_service, mock_webhook_endpoint, mock_db
    ):
        """Live contract: trigger time does NOT sign — rows persist signature=None.

        The wire signature is computed at SEND time per attempt in delivery.py
        (Stripe-style t=,v1= over the canonical bytes).
        """
        from src.app.modules.webhooks.models import WebhookDelivery, WebhookDeliveryStatus

        webhook_service.get_active_endpoints = AsyncMock(return_value=[mock_webhook_endpoint])

        mock_delivery = MagicMock(spec=WebhookDelivery)
        mock_delivery.id = uuid4()
        mock_delivery.status = WebhookDeliveryStatus.PENDING

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_delivery]
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("src.app.modules.webhooks.delivery.enqueue", new_callable=AsyncMock):
            result = await webhook_service.trigger_webhooks(
                tenant_id=mock_webhook_endpoint.tenant_id, event_type="user.created", payload={"id": 1}
            )

        assert len(result) == 1
        # Bulk insert carries no trigger-time signature value on the wire:
        # the bound signature param is NULL and no legacy sha256= digest
        # appears among the bound values. (No literal_binds: JSONB payloads
        # have no literal renderer.)
        insert_stmt = mock_db.execute.call_args[0][0]
        bound_params = insert_stmt.compile().params
        assert None in bound_params.values()
        assert not any(isinstance(v, str) and "sha256=" in v for v in bound_params.values())

    @pytest.mark.asyncio
    async def test_trigger_webhooks_does_not_decrypt_secrets(
        self, webhook_service, mock_webhook_endpoint, mock_db
    ):
        """Trigger time never touches endpoint secrets — decryption happens at send time.

        Even a failing decrypt must not skip the endpoint or break the trigger.
        """
        from src.app.modules.webhooks.models import WebhookDelivery, WebhookDeliveryStatus

        webhook_service.get_active_endpoints = AsyncMock(return_value=[mock_webhook_endpoint])

        mock_delivery = MagicMock(spec=WebhookDelivery)
        mock_delivery.id = uuid4()
        mock_delivery.status = WebhookDeliveryStatus.PENDING

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_delivery]
        mock_db.execute = AsyncMock(return_value=mock_result)

        with (
            patch(
                "src.app.modules.webhooks.service.decrypt_data",
                side_effect=Exception("Decrypt fail"),
            ),
            patch("src.app.modules.webhooks.delivery.enqueue", new_callable=AsyncMock),
        ):
            result = await webhook_service.trigger_webhooks(
                tenant_id=mock_webhook_endpoint.tenant_id, event_type="user.created", payload={"id": 1}
            )

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_trigger_endpoint_persists_null_signature(
        self, webhook_service, mock_webhook_endpoint, mock_db
    ):
        """Live contract: trigger_endpoint persists signature=None (send-time signing)."""
        from src.app.modules.webhooks.models import WebhookDelivery

        captured: dict = {}

        def _fake_add(delivery: WebhookDelivery) -> None:
            captured["delivery"] = delivery

        mock_db.add.side_effect = _fake_add
        webhook_service._enqueue_delivery = AsyncMock()

        delivery = await webhook_service.trigger_endpoint(
            endpoint=mock_webhook_endpoint, event_type="user.created", payload={"id": 1}
        )

        assert delivery.signature is None
        assert captured["delivery"].signature is None

    @pytest.mark.asyncio
    async def test_trigger_endpoint_undecryptable_secret_still_triggers(
        self, webhook_service, mock_webhook_endpoint
    ):
        """An undecryptable secret must not fail the trigger — send time handles it."""
        mock_webhook_endpoint.secret = "bad_secret"
        webhook_service._enqueue_delivery = AsyncMock()

        with patch("src.app.modules.webhooks.service.decrypt_data", side_effect=Exception("Decrypt fail")):
            delivery = await webhook_service.trigger_endpoint(
                endpoint=mock_webhook_endpoint, event_type="user.created", payload={"id": 1}
            )

        assert delivery.signature is None

    @pytest.mark.asyncio
    async def test_enqueue_delivery_no_pool(self, webhook_service):
        """Test enqueueing when redis pool is not initialized."""
        # Patch the pool import inside the method
        with patch("src.app.core.utils.queue.pool", None):
            await webhook_service._enqueue_delivery(uuid4())
            # Should not raise, just log warning (verified by coverage)


class TestWebhookServiceDelivery:
    """Tests for webhook delivery logic."""

    def _patch_signing(self):
        """W2: signing now happens at send time and decrypts the endpoint
        secret; tests store fake ciphertext, so decryption must be mocked."""
        return patch("src.app.modules.webhooks.delivery.decrypt_data", return_value="decrypted_secret")

    def _setup_execute_mock(self, mock_db, delivery, endpoint=None):
        """Helper to set up db.execute mock for selectinload pattern.

        The deliver_webhook method uses:
            result = await self.db.execute(stmt)
            delivery = result.unique().scalar_one_or_none()
        """
        mock_result = MagicMock()
        mock_unique = MagicMock()
        mock_unique.scalar_one_or_none.return_value = delivery
        mock_result.unique.return_value = mock_unique
        mock_db.execute.return_value = mock_result

        # Attach endpoint to delivery if provided (simulates selectinload)
        if delivery and endpoint:
            delivery.webhook_endpoint = endpoint

    @pytest.mark.asyncio
    async def test_deliver_webhook_not_found(self, webhook_service, mock_db):
        """Test error when delivery record not found."""
        self._setup_execute_mock(mock_db, None)

        with pytest.raises(ModelNotFoundError) as exc:
            await webhook_service.deliver_webhook(uuid4())

        assert "WebhookDelivery" in str(exc.value)

    @pytest.mark.asyncio
    async def test_deliver_webhook_endpoint_not_found(self, webhook_service, mock_db, mock_webhook_delivery):
        """Test error when endpoint not found."""
        # Delivery exists but has no endpoint (selectinload returns None)
        mock_webhook_delivery.webhook_endpoint = None
        self._setup_execute_mock(mock_db, mock_webhook_delivery, endpoint=None)

        with pytest.raises(ModelNotFoundError) as exc:
            await webhook_service.deliver_webhook(mock_webhook_delivery.id)

        assert "WebhookEndpoint" in str(exc.value)

    @pytest.mark.asyncio
    async def test_deliver_webhook_circuit_open(
        self, webhook_service, mock_db, mock_webhook_delivery, mock_webhook_endpoint
    ):
        """W4: circuit-open skip is NOT a delivery attempt — the retry budget
        stays intact and the delivery is merely deferred until recovery."""
        self._setup_execute_mock(mock_db, mock_webhook_delivery, mock_webhook_endpoint)
        # Also mock db.get for the re-fetch after HTTP call
        mock_db.get.return_value = mock_webhook_delivery
        mock_webhook_delivery.next_retry_at = None

        # Mock scoped circuit breaker to be open (P2-B: deliver() uses the
        # per-endpoint breaker from get_webhook_breaker, not the legacy
        # process-global webhook_circuit)
        mock_circuit = MagicMock()
        type(mock_circuit).is_closed = PropertyMock(return_value=False)
        mock_circuit.recovery_timeout = 60
        mock_circuit.last_failure_time = 0  # stale/absent timestamp -> clamped to 0s
        with (
            patch(
                "src.app.core.resilience.registry.get_webhook_breaker",
                return_value=mock_circuit,
            ),
            patch("src.app.modules.webhooks.delivery._handle_failure", new_callable=AsyncMock) as patch_handle,
        ):

            result = await webhook_service.deliver_webhook(mock_webhook_delivery.id)

            # Should return delivery without making request
            assert result == mock_webhook_delivery
            # W4: attempt budget must NOT be consumed by open-circuit skips
            patch_handle.assert_not_called()
            # Delivery deferred until breaker recovery (clamped >= now)
            assert mock_webhook_delivery.next_retry_at is not None

    @pytest.mark.asyncio
    async def test_deliver_webhook_ssrf_error(
        self, webhook_service, mock_db, mock_webhook_delivery, mock_webhook_endpoint
    ):
        """Test delivery fails on SSRF protection."""
        from src.app.core.security.url_validator import SSRFProtectionError
        from src.app.modules.webhooks.models import WebhookDeliveryStatus

        self._setup_execute_mock(mock_db, mock_webhook_delivery, mock_webhook_endpoint)

        # W2: signing now happens before the SSRF check, and it decrypts the
        # endpoint secret — mock that so the fake ciphertext doesn't blow up
        # before we reach the SSRF error under test.
        with (
            self._patch_signing(),
            patch("src.app.modules.webhooks.delivery.validate_webhook_url", side_effect=SSRFProtectionError("Blocked")),
        ):
            result = await webhook_service.deliver_webhook(mock_webhook_delivery.id)

            assert result.status == WebhookDeliveryStatus.FAILED
            assert "SSRF protection" in result.response_body

    @pytest.mark.asyncio
    async def test_deliver_webhook_circuit_exception(
        self, webhook_service, mock_db, mock_webhook_delivery, mock_webhook_endpoint
    ):
        """Test circuit breaker opening during delivery."""
        from src.app.core.utils.circuit_breaker import CircuitBreakerOpen

        self._setup_execute_mock(mock_db, mock_webhook_delivery, mock_webhook_endpoint)
        # Mock db.get for the re-fetch after HTTP call
        mock_db.get.return_value = mock_webhook_delivery

        with (
            self._patch_signing(),
            patch("src.app.modules.webhooks.delivery.validate_webhook_url"),
            patch("src.app.modules.webhooks.delivery._handle_failure", new_callable=AsyncMock) as patch_handle,
        ):
            with patch("src.app.modules.webhooks.delivery.get_webhook_http_client") as mock_client_fn:
                mock_client = AsyncMock()
                mock_client.post.side_effect = CircuitBreakerOpen("Circuit open", 60)
                mock_client_fn.return_value = mock_client

                await webhook_service.deliver_webhook(mock_webhook_delivery.id)

                patch_handle.assert_called_once()
                assert mock_webhook_delivery.attempt_count == 1
                assert "Circuit open" in mock_webhook_delivery.response_body

    @pytest.mark.asyncio
    async def test_deliver_webhook_generic_exception(
        self, webhook_service, mock_db, mock_webhook_delivery, mock_webhook_endpoint
    ):
        """Test generic exception during delivery."""
        self._setup_execute_mock(mock_db, mock_webhook_delivery, mock_webhook_endpoint)
        # Mock db.get for re-fetch after HTTP call
        mock_db.get.return_value = mock_webhook_delivery

        with (
            self._patch_signing(),
            patch("src.app.modules.webhooks.delivery.validate_webhook_url"),
            patch("src.app.modules.webhooks.delivery._handle_failure", new_callable=AsyncMock) as patch_handle,
        ):
            with patch("src.app.modules.webhooks.delivery.get_webhook_http_client") as mock_client_fn:
                mock_client = AsyncMock()
                mock_client.post.side_effect = Exception("Network error")
                mock_client_fn.return_value = mock_client

                # Mock scoped circuit breaker (P2-B: network failures record
                # on the per-endpoint breaker from get_webhook_breaker)
                mock_circuit = MagicMock()
                mock_circuit.is_closed = True
                mock_circuit.record_failure = AsyncMock()
                with patch(
                    "src.app.core.resilience.registry.get_webhook_breaker",
                    return_value=mock_circuit,
                ):
                    await webhook_service.deliver_webhook(mock_webhook_delivery.id)

                    mock_circuit.record_failure.assert_called_once()
                    patch_handle.assert_called_once()

    @pytest.mark.asyncio
    async def test_deliver_webhook_success(
        self, webhook_service, mock_db, mock_webhook_delivery, mock_webhook_endpoint
    ):
        """Test successful webhook delivery."""
        from src.app.modules.webhooks.models import WebhookDeliveryStatus

        # Mock DB lookups with selectinload pattern
        self._setup_execute_mock(mock_db, mock_webhook_delivery, mock_webhook_endpoint)
        # Mock db.get for re-fetch after HTTP call
        mock_db.get.return_value = mock_webhook_delivery

        # Mock validation
        with self._patch_signing(), patch("src.app.modules.webhooks.delivery.validate_webhook_url"):
            # Mock HTTP client (service uses get_webhook_http_client, not context manager)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "OK"

            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response

            with patch("src.app.modules.webhooks.delivery.get_webhook_http_client", return_value=mock_client):
                # Also mock the scoped circuit breaker (P2-B: per-endpoint
                # breaker from get_webhook_breaker)
                mock_circuit = MagicMock()
                mock_circuit.is_closed = True
                mock_circuit.record_success = AsyncMock()
                with patch(
                    "src.app.core.resilience.registry.get_webhook_breaker",
                    return_value=mock_circuit,
                ):
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

        self._setup_execute_mock(mock_db, mock_webhook_delivery, mock_webhook_endpoint)
        # Mock db.get for re-fetch after HTTP call
        mock_db.get.return_value = mock_webhook_delivery
        # Retry path now enqueues via the delivery module's spawner.

        with (
            self._patch_signing(),
            patch("src.app.modules.webhooks.delivery.validate_webhook_url"),
            patch("src.app.modules.webhooks.delivery._spawn_enqueue") as mock_spawn,
        ):
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"

            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response

            with patch("src.app.modules.webhooks.delivery.get_webhook_http_client", return_value=mock_client):
                mock_circuit = MagicMock()
                mock_circuit.is_closed = True
                mock_circuit.record_failure = AsyncMock()
                with patch(
                    "src.app.core.resilience.registry.get_webhook_breaker",
                    return_value=mock_circuit,
                ):
                    mock_webhook_delivery.attempt_count = 0

                    result = await webhook_service.deliver_webhook(mock_webhook_delivery.id)

                    assert result.status == WebhookDeliveryStatus.PENDING
                    assert result.next_retry_at is not None
                    mock_spawn.assert_called_once()

    @pytest.mark.asyncio
    async def test_deliver_webhook_max_retries_exceeded(
        self, webhook_service, mock_db, mock_webhook_delivery, mock_webhook_endpoint
    ):
        """Test delivery failure after max retries."""
        from src.app.modules.webhooks.models import WebhookDeliveryStatus

        self._setup_execute_mock(mock_db, mock_webhook_delivery, mock_webhook_endpoint)
        # Mock db.get for re-fetch after HTTP call
        mock_db.get.return_value = mock_webhook_delivery

        with self._patch_signing(), patch("src.app.modules.webhooks.delivery.validate_webhook_url"):
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"

            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response

            # Attempt 5 (max is 5, index 4)
            with patch("src.app.modules.webhooks.delivery.get_webhook_http_client", return_value=mock_client):
                mock_circuit = MagicMock()
                mock_circuit.is_closed = True
                mock_circuit.record_failure = AsyncMock()
                with patch(
                    "src.app.core.resilience.registry.get_webhook_breaker",
                    return_value=mock_circuit,
                ):
                    mock_webhook_delivery.attempt_count = 5
                    result = await webhook_service.deliver_webhook(mock_webhook_delivery.id)

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
        """Test service has all expected methods (dead signing surface removed)."""
        assert hasattr(webhook_service, "create_endpoint")
        assert hasattr(webhook_service, "get_active_endpoints")
        assert hasattr(webhook_service, "trigger_webhooks")
        assert hasattr(webhook_service, "deliver_webhook")
        assert hasattr(webhook_service, "get_delivery_history")
        # Fix #6: trigger-time legacy signing is dead — these must stay gone.
        assert not hasattr(webhook_service, "_generate_signature")
        assert not hasattr(webhook_service, "verify_signature")

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
    """Live-contract tests for webhook signing (fix #6).

    The wire signature is the send-time Stripe-style ``t=,v1=`` header
    produced by ``signer.sign_payload`` in delivery.py — NOT a persisted
    trigger-time value. These tests pin that contract:
    - canonical bytes use stdlib json with sort_keys (signer-owned)
    - the send-time header verifies via the signer
    - trigger paths persist signature=None
    """

    def test_canonical_contract_uses_stdlib_json(self) -> None:
        """The signer canonical bytes use stdlib json, NOT orjson.

        HMAC is byte-sensitive: stdlib produces '{"key": "value"}' (spaces)
        while orjson produces '{"key":"value"}'. Changing libraries would
        break every consumer. The contract lives in signer.py now.
        """
        import ast
        import inspect

        from src.app.modules.webhooks import signer as signer_module

        tree = ast.parse(inspect.getsource(signer_module))

        imports: list[str] = []
        calls: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                    calls.append(f"{node.func.value.id}.{node.func.attr}")

        assert "json" in imports, "Must import stdlib json"
        assert "orjson" not in imports, "Must NOT import orjson for webhook signatures"
        assert "json.dumps" in calls, "Must use json.dumps for the canonical form"
        assert "orjson.dumps" not in calls, "Must NOT use orjson.dumps"


    def test_canonical_bytes_have_spaces_after_colons(self) -> None:
        """The exact byte format consumers verify: stdlib json with spaces.

        Different formatting = different bytes = different HMAC = broken webhooks.
        See: SECURITY.md for full explanation.
        """
        import json

        from src.app.modules.webhooks.signer import canonical_payload_bytes

        payload = {"user_id": "123", "event": "created"}

        assert canonical_payload_bytes(payload) == json.dumps(payload, sort_keys=True).encode("utf-8")

        json_str = json.dumps(payload, sort_keys=True)
        assert ": " in json_str, "stdlib json must have spaces after colons"

    def test_send_time_header_format(self) -> None:
        """The live wire header is Stripe-style t=<unix>,v1=<hex>."""
        import time

        from src.app.modules.webhooks.signer import sign_payload

        payload = {"user_id": "123", "event": "created"}
        secret = "test-secret"

        header = sign_payload(payload, secret, int(time.time()))

        assert header.startswith("t=")
        assert ",v1=" in header
        hex_part = header.split("v1=", 1)[1].split(",", 1)[0]
        assert len(hex_part) == 64
        assert all(c in "0123456789abcdef" for c in hex_part)

    def test_send_time_header_deterministic(self) -> None:
        """Same payload + secret + timestamp produces the same header."""
        from src.app.modules.webhooks.signer import sign_payload

        payload = {"user_id": "123", "event": "created"}
        secret = "test-secret"
        timestamp = 1757328000

        assert sign_payload(payload, secret, timestamp) == sign_payload(payload, secret, timestamp)

    def test_send_time_header_differs_with_different_secrets(self) -> None:
        """Different secrets produce different wire headers."""
        from src.app.modules.webhooks.signer import sign_payload

        payload = {"user_id": "123", "event": "created"}

        assert sign_payload(payload, "secret1", 1757328000) != sign_payload(payload, "secret2", 1757328000)

    def test_send_time_header_differs_with_different_payloads(self) -> None:
        """Different payloads produce different wire headers."""
        from src.app.modules.webhooks.signer import sign_payload

        secret = "test-secret"

        assert sign_payload({"user_id": "123"}, secret, 1757328000) != sign_payload(
            {"user_id": "456"}, secret, 1757328000
        )

    def test_send_time_header_verifies(self) -> None:
        """A send-time header verifies via the signer (round trip)."""
        import time

        from src.app.modules.webhooks.signer import sign_payload, verify_signature

        payload = {"user_id": "123", "event": "created"}
        secret = "test-secret"
        header = sign_payload(payload, secret, int(time.time()))

        assert verify_signature(payload, secret, header) is True
        assert verify_signature(payload, "wrong-secret", header) is False

    def test_canonical_bytes_use_sorted_keys(self) -> None:
        """Same payload in different key order signs identically."""
        from src.app.modules.webhooks.signer import sign_payload

        secret = "test-secret"

        assert sign_payload({"b": 2, "a": 1}, secret, 1757328000) == sign_payload(
            {"a": 1, "b": 2}, secret, 1757328000
        )


class TestWebhookServiceRetry:
    """Tests for webhook retry logic."""

    def test_retry_schedule_is_canonical_delivery_schedule(self) -> None:
        """Fix #6: the schedule lives in delivery.py; service only re-exports it."""
        from src.app.modules.webhooks import delivery as delivery_module
        from src.app.modules.webhooks import service as service_module

        assert service_module.WEBHOOK_RETRY_DELAYS is delivery_module.WEBHOOK_RETRY_DELAYS

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
