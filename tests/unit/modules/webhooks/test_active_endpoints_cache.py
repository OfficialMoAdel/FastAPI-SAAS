"""Unit tests for P2-4 active endpoints caching and P2-5 pre-resolved delivery."""

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch
from uuid import uuid4

import pytest

from src.app.core.utils.circuit_breaker import webhook_circuit
from src.app.modules.webhooks.delivery import deliver
from src.app.modules.webhooks.models import WebhookDelivery, WebhookEndpoint
from src.app.modules.webhooks.service import WebhookService


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
