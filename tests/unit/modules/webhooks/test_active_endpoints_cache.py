"""Unit tests for P2-4 active endpoints caching and P2-5 pre-resolved delivery."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch
from uuid import uuid4

import pytest

from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.utils.circuit_breaker import webhook_circuit
from src.app.modules.webhooks.delivery import deliver
from src.app.modules.webhooks.models import WebhookDelivery, WebhookEndpoint
from src.app.modules.webhooks.service import (
    _SERIALIZED_ENDPOINT_KEYS,
    WebhookService,
    _deserialize_endpoint,
    _serialize_endpoint,
)


class TestActiveEndpointsCache:
    """Tests for P2-4 active webhook endpoints Redis caching and invalidation."""

    @pytest.mark.asyncio
    async def test_get_active_endpoints_cache_miss_then_hit(self) -> None:
        """Test cache miss populates Redis and second call reads from Redis without SQL."""
        mock_db = AsyncMock()
        service = WebhookService(mock_db)

        tenant_id = uuid4()
        event_type = "user.created"

        endpoint = WebhookEndpoint(
            tenant_id=tenant_id,
            url="https://example.com/hook",
            event_type="user.created",
            secret=b"secret_bytes",
            signing_secret="sign_secret",
            is_active=True,
        )
        endpoint.id = uuid4()

        mock_db_result = MagicMock()
        mock_db_result.scalars.return_value.all.return_value = [endpoint]
        mock_db.execute.return_value = mock_db_result

        # Mock redis
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None  # Cache miss first time
        mock_pipe = AsyncMock()
        mock_pipe.__aenter__.return_value = mock_pipe
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        with patch("src.app.core.redis_provider.get_redis", return_value=mock_redis):
            # First call (miss)
            res1 = await service.get_active_endpoints(tenant_id, event_type)
            assert len(res1) == 1
            assert res1[0].url == "https://example.com/hook"
            assert mock_db.execute.call_count == 1
            mock_pipe.setex.assert_called_once()

            # Second call (hit)
            mock_redis.get.return_value = (
                '[{"id":"'
                + str(endpoint.id)
                + '","tenant_id":"'
                + str(tenant_id)
                + '","url":"https://example.com/hook","event_type":"user.created","secret":"7365637265745f6279746573","signing_secret":"sign_secret","is_active":true,"old_signing_secret":null,"old_secret_expires_at":null,"description":null}]'
            )

            res2 = await service.get_active_endpoints(tenant_id, event_type)
            assert len(res2) == 1
            assert res2[0].url == "https://example.com/hook"
            # DB should NOT be called again
            assert mock_db.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_invalidate_endpoints_cache_on_crud(self) -> None:
        """Test that _invalidate_endpoints_cache deletes all tenant endpoint keys in Redis."""
        mock_db = AsyncMock()
        service = WebhookService(mock_db)
        tenant_id = uuid4()

        mock_redis = AsyncMock()
        mock_redis.smembers.return_value = {b"webhook:active_endpoints:t:user.created"}
        mock_pipe = AsyncMock()
        mock_pipe.__aenter__.return_value = mock_pipe
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        with patch("src.app.core.redis_provider.get_redis", return_value=mock_redis):
            await service._invalidate_endpoints_cache(tenant_id)

            mock_pipe.delete.assert_any_call("webhook:active_endpoints:t:user.created")
            mock_pipe.delete.assert_any_call(f"webhook:tenant_endpoints:{tenant_id}")
            mock_pipe.execute.assert_called_once()


class TestEndpointCacheRoundTrip:
    """R1-BUG-1: a cache HIT must reconstruct exactly what a cache MISS returned.

    ``_serialize_endpoint`` / ``_deserialize_endpoint`` are one contract in two
    halves. When they drift, the cache hit returns a partially-built model, and
    the only reason that stayed invisible was that no test asserted the model
    rather than the payload.
    """

    @staticmethod
    def _persisted_endpoint() -> WebhookEndpoint:
        """An endpoint shaped the way the database loader produces one."""
        endpoint = WebhookEndpoint(
            tenant_id=uuid4(),
            url="https://example.com/hook",
            event_type="user.created",
            secret="plain-secret",
            signing_secret="sign-secret",
            is_active=True,
        )
        # init=False columns: the loader sets these on the instance, not via
        # the constructor — which is exactly why the deserializer must too.
        endpoint.id = uuid4()
        endpoint.description = "primary CRM hook"
        endpoint.old_signing_secret = "previous-secret"
        endpoint.old_secret_expires_at = datetime(2030, 1, 1, tzinfo=UTC)
        return endpoint

    def test_serializer_emits_exactly_the_declared_key_set(self) -> None:
        payload = _serialize_endpoint(self._persisted_endpoint())
        assert set(payload) == _SERIALIZED_ENDPOINT_KEYS, (
            "a key was added to one half of the cache contract without the other"
        )

    def test_every_serialized_field_survives_the_round_trip(self) -> None:
        original = self._persisted_endpoint()
        wire = json_dumps(_serialize_endpoint(original), as_str=True)
        restored = _deserialize_endpoint(json_loads(wire))

        assert restored.id == original.id
        assert restored.tenant_id == original.tenant_id
        assert restored.url == original.url
        assert restored.event_type == original.event_type
        assert restored.secret == original.secret
        assert restored.signing_secret == original.signing_secret
        assert restored.is_active == original.is_active
        assert restored.old_signing_secret == original.old_signing_secret
        assert restored.old_secret_expires_at == original.old_secret_expires_at
        assert restored.description == original.description

    def test_a_transient_endpoint_serializes_an_absent_id(self) -> None:
        """``str(None)`` would be the truthy string "None" and fail UUID parsing."""
        transient = WebhookEndpoint(
            tenant_id=uuid4(),
            url="https://example.com/hook",
            event_type="user.created",
            secret="s",
            signing_secret=None,
            is_active=True,
        )
        assert transient.id is None
        payload = _serialize_endpoint(transient)
        assert payload["id"] is None
        assert _deserialize_endpoint(payload).id is None

    @pytest.mark.asyncio
    async def test_a_cache_hit_creates_a_delivery_with_the_cached_endpoint_id(self) -> None:
        """The end-to-end reason this matters: the delivery FK is NOT NULL."""
        mock_db = AsyncMock()
        service = WebhookService(mock_db)
        tenant_id = uuid4()
        endpoint_id = uuid4()

        endpoint = WebhookEndpoint(
            tenant_id=tenant_id,
            url="https://example.com/hook",
            event_type="user.created",
            secret="s",
            signing_secret="ss",
            is_active=True,
        )
        endpoint.id = endpoint_id

        mock_redis = AsyncMock()
        # A real cache payload, produced by the real serializer.
        mock_redis.get.return_value = json_dumps([_serialize_endpoint(endpoint)], as_str=True)

        stored = MagicMock()
        stored.scalars.return_value.all.return_value = [MagicMock(id=uuid4(), tenant_id=tenant_id)]
        mock_db.execute.return_value = stored

        with (
            patch("src.app.core.redis_provider.get_redis", return_value=mock_redis),
            patch("src.app.modules.webhooks.delivery.enqueue", new=AsyncMock()),
        ):
            await service.trigger_webhooks(tenant_id, "user.created", {"k": "v"})

        # The only SQL on a cache hit is the delivery INSERT — no endpoint SELECT.
        insert_stmt = mock_db.execute.await_args_list[0].args[0]
        params = insert_stmt.compile().params
        bound_endpoint_id = next(
            value for key, value in params.items() if key.startswith("webhook_endpoint_id")
        )
        assert bound_endpoint_id == endpoint_id, "a cache hit must still bind the real endpoint id"


class TestPreResolvedDelivery:
    """Tests for P2-5 pre-resolved endpoint delivery optimization."""

    @pytest.mark.asyncio
    async def test_deliver_uses_pre_resolved_endpoint(self) -> None:
        """Test deliver uses pre-resolved endpoint without selectinload query."""
        mock_db = AsyncMock()
        delivery_id = uuid4()
        tenant_id = uuid4()

        endpoint = WebhookEndpoint(
            tenant_id=tenant_id,
            url="https://example.com/webhook",
            event_type="user.created",
            secret=b"test_secret",
            signing_secret="test_signing_secret",
            is_active=True,
        )
        endpoint.id = uuid4()

        delivery = WebhookDelivery(
            webhook_endpoint_id=endpoint.id,
            tenant_id=tenant_id,
            event_type="user.created",
            payload={"msg": "hello"},
            signature="sig",
        )
        delivery.id = delivery_id

        mock_result = MagicMock()
        mock_result.unique.return_value.scalar_one_or_none.return_value = delivery
        mock_db.execute.return_value = mock_result
        mock_db.get.return_value = delivery

        mock_http_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "OK"
        mock_http_client.post.return_value = mock_resp

        with (
            patch("src.app.core.redis_provider.get_redis", return_value=None),
            patch.object(type(webhook_circuit), "is_closed", new_callable=PropertyMock, return_value=True),
            patch(
                "src.app.modules.webhooks.delivery.get_webhook_http_client",
                AsyncMock(return_value=mock_http_client),
            ),
            patch("src.app.modules.webhooks.delivery.validate_webhook_url", AsyncMock(return_value="1.2.3.4")),
            patch("src.app.modules.webhooks.delivery.decrypt_data", return_value="decrypted_secret"),
        ):
            res = await deliver(mock_db, delivery_id, endpoint=endpoint)
            assert res.id == delivery_id
            assert res.status.value == "delivered"
