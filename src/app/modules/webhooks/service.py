"""Webhook Service - Event subscription and delivery.

This module provides:
- Webhook endpoint management
- Event delivery with retry logic
- HMAC-SHA256 signature generation
- Delivery status tracking
"""

import hashlib
import hmac
import logging
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.webhooks.models import (
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookEndpoint,
)

logger = logging.getLogger(__name__)

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
        endpoint = WebhookEndpoint(
            tenant_id=tenant_id,
            url=url,
            event_type=event_type,
            secret=secret,
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
            WebhookEndpoint.is_active == True,  # noqa: E712
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

        deliveries = []
        for endpoint in endpoints:
            # Generate signature
            signature = self._generate_signature(payload, endpoint.secret or "")

            # Create delivery record
            delivery = WebhookDelivery(
                webhook_endpoint_id=endpoint.id,
                event_type=event_type,
                payload=payload,
                signature=signature,
                status=WebhookDeliveryStatus.PENDING,
                attempt_count=0,
            )

            self.db.add(delivery)
            deliveries.append(delivery)

        await self.db.commit()

        # Enqueue delivery tasks (will be implemented with ARQ)
        for delivery in deliveries:
            await self._enqueue_delivery(delivery.id)

        logger.info(f"Triggered {len(deliveries)} webhooks for event {event_type} in tenant {tenant_id}")
        return deliveries

    def _generate_signature(self, payload: dict[str, Any], secret: str) -> str:
        """Generate HMAC-SHA256 signature for webhook payload."""
        import json

        payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        signature = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()

        return f"sha256={signature}"

    async def _enqueue_delivery(self, delivery_id: UUID) -> None:
        """Enqueue webhook delivery task.

        TODO: Integrate with ARQ queue when background tasks are set up.
        For now, this is a placeholder.
        """
        # This will be implemented in Phase 4 with ARQ integration
        logger.debug(f"Enqueuing delivery task for {delivery_id}")
        pass

    async def deliver_webhook(self, delivery_id: UUID, attempt: int = 0) -> WebhookDelivery:
        """Deliver a webhook (called by background task).

        Implements retry logic with exponential backoff.
        """
        # Get delivery record
        delivery = await self.db.get(WebhookDelivery, delivery_id)
        if not delivery:
            raise ValueError(f"Delivery {delivery_id} not found")

        # Get endpoint
        endpoint = await self.db.get(WebhookEndpoint, delivery.webhook_endpoint_id)
        if not endpoint:
            raise ValueError(f"Endpoint {delivery.webhook_endpoint_id} not found")

        # Prepare request
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Signature": delivery.signature,
            "X-Webhook-Event": delivery.event_type,
            "X-Webhook-ID": str(delivery.id),
            "X-Webhook-Timestamp": str(int(delivery.created_at.timestamp())),
        }

        # Attempt delivery
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(endpoint.url, json=delivery.payload, headers=headers)

                delivery.attempt_count += 1
                delivery.response_status = response.status_code
                delivery.response_body = response.text[:1000]  # Limit to 1KB

                if 200 <= response.status_code < 300:
                    # Success
                    delivery.status = WebhookDeliveryStatus.DELIVERED
                    delivery.delivered_at = datetime.utcnow()
                    logger.info(f"Webhook delivered successfully: {delivery_id} (attempt {attempt + 1})")
                else:
                    # HTTP error - retry if attempts remain
                    await self._handle_delivery_failure(delivery, attempt)

        except Exception as e:
            # Network/timeout error - retry if attempts remain
            logger.error(f"Webhook delivery failed: {delivery_id} - {str(e)}")
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
            delivery.next_retry_at = datetime.utcnow() + timedelta(seconds=retry_delay)
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
