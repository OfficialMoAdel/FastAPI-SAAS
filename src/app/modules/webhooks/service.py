"""Webhook Service - Event subscription and delivery.

This module provides:
- Webhook endpoint management
- Event delivery with retry logic
- HMAC-SHA256 signature generation
- Delivery status tracking
"""

import asyncio
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logger import get_logger
from ...core.security.encryption import decrypt_data, encrypt_data
from ...core.security.url_validator import SSRFProtectionError, validate_webhook_url
from .._base import ModelNotFoundError
from .http_client import get_webhook_http_client
from .models import (
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookEndpoint,
)

logger = get_logger(__name__)

# Retry schedule: 1m, 5m, 15m, 30m, 1h (5 retries over 1 hour)
WEBHOOK_RETRY_DELAYS = [60, 300, 900, 1800, 3600]


class WebhookService:
    """Service for managing webhooks and deliveries."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_endpoint(
        self,
        tenant_id: UUID,
        url: str,
        event_type: str,
        secret: str | None = None,
        is_active: bool = True,
    ) -> WebhookEndpoint:
        """Create a new webhook endpoint."""
        # Encrypt the secret if provided
        encrypted_secret = encrypt_data(secret) if secret else None

        endpoint = WebhookEndpoint(
            tenant_id=tenant_id,
            url=url,
            event_type=event_type,
            secret=encrypted_secret,
            is_active=is_active,
        )

        self.db.add(endpoint)
        await self.db.commit()
        await self.db.refresh(endpoint)

        logger.info(f"Created webhook endpoint for tenant {tenant_id}: {event_type} -> {url}")
        return endpoint

    async def get_active_endpoints(self, tenant_id: UUID, event_type: str) -> list[WebhookEndpoint]:
        """Get all active webhook endpoints for a tenant and event type."""
        stmt = select(WebhookEndpoint).where(
            WebhookEndpoint.tenant_id == tenant_id,
            WebhookEndpoint.event_type == event_type,
            WebhookEndpoint.is_active.is_(True),
        )

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def trigger_webhooks(
        self,
        tenant_id: UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> list[WebhookDelivery]:
        """Trigger webhooks for a specific event.

        Creates delivery records and enqueues delivery tasks.
        Returns the created delivery records.
        """
        endpoints = await self.get_active_endpoints(tenant_id, event_type)

        # Parallelize webhook triggering for better performance
        if endpoints:
            results = await asyncio.gather(
                *(self.trigger_endpoint(endpoint, event_type, payload) for endpoint in endpoints),
                return_exceptions=True,
            )
            # Filter out exceptions and log them
            deliveries = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"Webhook trigger failed for endpoint {endpoints[i].id}: {result}")
                else:
                    deliveries.append(result)
        else:
            deliveries = []

        logger.info(f"Triggered {len(deliveries)} webhooks for event {event_type} in tenant {tenant_id}")
        return deliveries

    async def trigger_endpoint(
        self,
        endpoint: WebhookEndpoint,
        event_type: str,
        payload: dict[str, Any],
    ) -> WebhookDelivery:
        """Trigger a specific webhook endpoint (e.g., for testing)."""
        secret = ""
        if endpoint.secret:
            try:
                secret = decrypt_data(endpoint.secret)
            except Exception as e:
                logger.error(
                    "webhook_secret_decryption_failed",
                    endpoint_id=str(endpoint.id),
                    error=str(e),
                )
                raise ValueError(
                    f"Cannot decrypt webhook secret for endpoint {endpoint.id}. The encryption key may have changed."
                )

        signature = self._generate_signature(payload, secret)

        delivery = WebhookDelivery(
            webhook_endpoint_id=endpoint.id,
            tenant_id=endpoint.tenant_id,
            event_type=event_type,
            payload=payload,
            signature=signature,
            status=WebhookDeliveryStatus.PENDING,
            attempt_count=0,
        )

        self.db.add(delivery)
        await self.db.commit()
        await self.db.refresh(delivery)

        await self._enqueue_delivery(delivery.id)

        logger.info(f"Triggered webhook delivery {delivery.id} for endpoint {endpoint.id}")
        return delivery

    def _generate_signature(self, payload: dict[str, Any], secret: str) -> str:
        """Generate HMAC-SHA256 signature for webhook payload.

        CRITICAL: This method MUST use stdlib json, NOT orjson or other JSON libraries.

        Why: The HMAC signature is calculated from the exact byte representation of the
        JSON payload. Different JSON libraries produce different formatting:
        - stdlib json.dumps: {"key": "value"}  (space after colon)
        - orjson.dumps:      {"key":"value"}   (no space)

        Changing the JSON library would produce different bytes → different HMAC signature
        → ALL existing webhook consumers would fail signature validation.

        This is intentional and correct behavior. Do not "optimize" this to orjson.
        See: SECURITY.md for full explanation.
        """
        import json

        payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        signature = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()

        return f"sha256={signature}"

    async def _enqueue_delivery(self, delivery_id: UUID) -> None:
        """Enqueue webhook delivery task."""
        from ...core.utils.queue import pool

        if pool:
            await pool.enqueue_job("deliver_webhook_task", delivery_id)
            logger.debug(f"Enqueued delivery task for {delivery_id}")
        else:
            logger.warning(f"Redis queue pool not initialized. Cannot enqueue delivery {delivery_id}")

    async def deliver_webhook(self, delivery_id: UUID, attempt: int = 0) -> WebhookDelivery:
        """Deliver a webhook (called by background task).

        Implements retry logic with exponential backoff and circuit breaker protection.
        """
        from ...core.utils.circuit_breaker import CircuitBreakerOpen, webhook_circuit

        # Get delivery record
        delivery = await self.db.get(WebhookDelivery, delivery_id)
        if not delivery:
            raise ModelNotFoundError("WebhookDelivery", delivery_id)

        # Get endpoint
        endpoint = await self.db.get(WebhookEndpoint, delivery.webhook_endpoint_id)
        if not endpoint:
            raise ModelNotFoundError("WebhookEndpoint", delivery.webhook_endpoint_id)

        # Check circuit breaker before attempting delivery
        if not webhook_circuit.is_closed:
            logger.warning(
                "Circuit breaker open for webhooks, skipping delivery %s | recovery_in=%.1fs",
                delivery_id,
                webhook_circuit.recovery_timeout - (__import__("time").time() - webhook_circuit._last_failure_time),
            )
            # Mark for retry later when circuit recovers
            await self._handle_delivery_failure(delivery, attempt)
            await self.db.commit()
            await self.db.refresh(delivery)
            return delivery

        # Prepare request
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Signature": delivery.signature,
            "X-Webhook-Event": delivery.event_type,
            "X-Webhook-ID": str(delivery.id),
            "X-Webhook-Timestamp": str(int(delivery.created_at.timestamp())),
        }

        # Validate URL for SSRF protection before making request
        try:
            await validate_webhook_url(endpoint.url)
        except SSRFProtectionError as e:
            logger.error(
                "webhook_ssrf_blocked",
                delivery_id=str(delivery_id),
                endpoint_url=endpoint.url,
                error=str(e),
            )
            delivery.status = WebhookDeliveryStatus.FAILED
            delivery.response_body = f"SSRF protection: {e}"
            await self.db.commit()
            await self.db.refresh(delivery)
            return delivery

        # Attempt delivery with circuit breaker tracking
        try:
            client = get_webhook_http_client()
            response = await client.post(endpoint.url, json=delivery.payload, headers=headers)

            delivery.attempt_count += 1
            delivery.response_status = response.status_code
            delivery.response_body = response.text[:1000]  # Limit to 1KB

            if 200 <= response.status_code < 300:  # HTTP success range
                # Success - record with circuit breaker
                await webhook_circuit.record_success()
                delivery.status = WebhookDeliveryStatus.DELIVERED
                delivery.delivered_at = datetime.now(UTC)
                logger.info(f"Webhook delivered successfully: {delivery_id} (attempt {attempt + 1})")
            else:
                # HTTP error - record failure and retry if attempts remain
                await webhook_circuit.record_failure(Exception(f"HTTP {response.status_code}"))
                await self._handle_delivery_failure(delivery, attempt)

        except CircuitBreakerOpen as e:
            # Circuit opened during delivery attempt
            logger.warning("Webhook circuit breaker opened: %s", e)
            delivery.attempt_count += 1
            delivery.response_body = str(e)[:1000]
            await self._handle_delivery_failure(delivery, attempt)

        except Exception as e:
            # Network/timeout error - record failure and retry
            logger.error(f"Webhook delivery failed: {delivery_id} - {e!s}")
            await webhook_circuit.record_failure(e)
            delivery.attempt_count += 1
            delivery.response_body = str(e)[:1000]
            await self._handle_delivery_failure(delivery, attempt)

        await self.db.commit()
        await self.db.refresh(delivery)
        return delivery

    async def _handle_delivery_failure(self, delivery: WebhookDelivery, attempt: int) -> None:
        """Handle failed delivery with retry logic."""
        if attempt < len(WEBHOOK_RETRY_DELAYS):
            # Schedule retry
            retry_delay = WEBHOOK_RETRY_DELAYS[attempt]
            delivery.next_retry_at = datetime.now(UTC) + timedelta(seconds=retry_delay)
            delivery.status = WebhookDeliveryStatus.PENDING

            # Enqueue retry task
            await self._enqueue_delivery(delivery.id)

            logger.warning(
                f"Webhook delivery failed, will retry in {retry_delay}s: {delivery.id} (attempt {attempt + 1})"
            )
        else:
            # Max retries exhausted
            delivery.status = WebhookDeliveryStatus.FAILED
            logger.error(f"Webhook delivery failed permanently after {attempt + 1} attempts: {delivery.id}")

    async def verify_signature(self, payload: dict[str, Any], signature: str, secret: str) -> bool:
        """Verify webhook signature.

        Used by webhook receivers to validate requests.
        """
        expected_signature = self._generate_signature(payload, secret)
        return hmac.compare_digest(signature, expected_signature)

    async def retry_delivery(self, delivery_id: UUID) -> WebhookDelivery:
        """Retry a failed delivery."""
        delivery = await self.db.get(WebhookDelivery, delivery_id)
        if not delivery:
            raise ModelNotFoundError("WebhookDelivery", delivery_id)

        delivery.status = WebhookDeliveryStatus.PENDING
        delivery.attempt_count = 0
        delivery.next_retry_at = None

        await self.db.commit()
        await self.db.refresh(delivery)

        await self._enqueue_delivery(delivery.id)
        return delivery

    async def get_delivery_history(
        self,
        tenant_id: UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[WebhookDelivery]:
        """Get webhook delivery history for a tenant."""
        stmt = (
            select(WebhookDelivery)
            .join(WebhookEndpoint)
            .where(WebhookEndpoint.tenant_id == tenant_id)
            .order_by(WebhookDelivery.created_at.desc())
            .limit(limit)
            .offset(offset)
        )

        result = await self.db.execute(stmt)
        return list(result.scalars().all())
