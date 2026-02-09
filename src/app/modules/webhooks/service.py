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
from uuid import UUID, uuid4

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
        tenant_id: int,
        url: str,
        event_type: str,
        secret: str | None = None,
        signing_secret: str | None = None,
        is_active: bool = True,
    ) -> WebhookEndpoint:
        """Create a new webhook endpoint."""
        # Encrypt the secret if provided, or generate a random one
        import secrets as secrets_module

        actual_secret = secret if secret else secrets_module.token_urlsafe(32)
        encrypted_secret = encrypt_data(actual_secret)

        # Generate signing_secret if not provided (64 chars = 256 bits)
        actual_signing_secret = signing_secret if signing_secret else secrets_module.token_hex(32)

        endpoint = WebhookEndpoint(
            tenant_id=tenant_id,
            url=url,
            event_type=event_type,
            secret=encrypted_secret,
            signing_secret=actual_signing_secret,
            is_active=is_active,
        )

        self.db.add(endpoint)
        await self.db.commit()
        await self.db.refresh(endpoint)

        logger.info(f"Created webhook endpoint for tenant {tenant_id}: {event_type} -> {url}")
        return endpoint

    async def get_active_endpoints(self, tenant_id: int, event_type: str) -> list[WebhookEndpoint]:
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
        tenant_id: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> list[WebhookDelivery]:
        """Trigger webhooks for a specific event.

        Creates delivery records and enqueues delivery tasks.
        Returns the created delivery records.
        """
        endpoints = await self.get_active_endpoints(tenant_id, event_type)
        if not endpoints:
            return []

        # Prepare deliveries for bulk insert
        deliveries_data = []

        for endpoint in endpoints:
            # Prefer signing_secret (per-endpoint) over legacy encrypted secret
            secret = endpoint.signing_secret or ""
            if not secret and endpoint.secret:
                try:
                    secret = decrypt_data(endpoint.secret)
                except Exception as e:
                    logger.error(
                        "webhook_secret_decryption_failed",
                        endpoint_id=str(endpoint.id),
                        error=str(e),
                    )
                    # Skip this endpoint but continue with others
                    continue

            signature = self._generate_signature(payload, secret)
            now = datetime.now(UTC)

            deliveries_data.append(
                {
                    "id": uuid4(),
                    "webhook_endpoint_id": endpoint.id,
                    "tenant_id": endpoint.tenant_id,
                    "event_type": event_type,
                    "payload": payload,
                    "signature": signature,
                    "status": WebhookDeliveryStatus.PENDING,
                    "attempt_count": 0,
                    "created_at": now,
                    "updated_at": now,
                }
            )

        if not deliveries_data:
            return []

        # Perform Bulk Insert (Single Transaction)
        stmt = insert(WebhookDelivery).values(deliveries_data).returning(WebhookDelivery)
        result = await self.db.execute(stmt)
        deliveries = list(result.scalars().all())

        await self.db.commit()

        # Enqueue tasks concurrently (Redis is safe for concurrent ops)
        if deliveries:
            await asyncio.gather(
                *(self._enqueue_delivery(d.id) for d in deliveries),
                return_exceptions=True,
            )

        logger.info(f"Triggered {len(deliveries)} webhooks for event {event_type} in tenant {tenant_id}")
        return deliveries

    async def trigger_endpoint(
        self,
        endpoint: WebhookEndpoint,
        event_type: str,
        payload: dict[str, Any],
    ) -> WebhookDelivery:
        """Trigger a specific webhook endpoint (e.g., for testing)."""
        # Prefer signing_secret (per-endpoint) over legacy encrypted secret
        secret = endpoint.signing_secret or ""
        if not secret and endpoint.secret:
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

        CRITICAL: Uses content= instead of json= to prevent httpx double serialization.
        The payload is serialized ONCE with sort_keys=True to match the HMAC signature.
        """
        import json

        from ...core.utils.circuit_breaker import CircuitBreakerOpen, webhook_circuit

        # Get delivery record with endpoint eagerly loaded (fixes N+1 query)
        stmt = (
            select(WebhookDelivery)
            .options(selectinload(WebhookDelivery.webhook_endpoint))
            .where(WebhookDelivery.id == delivery_id)
        )
        result = await self.db.execute(stmt)
        delivery = result.unique().scalar_one_or_none()
        if not delivery:
            raise ModelNotFoundError("WebhookDelivery", delivery_id)

        # Access eagerly loaded endpoint
        endpoint = delivery.webhook_endpoint
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

        # CRIT-2 FIX: Serialize payload ONCE with sort_keys=True to match HMAC signature.
        # Using content= instead of json= prevents httpx from re-serializing with
        # different formatting, which would break HMAC verification on the receiver side.
        payload_bytes = json.dumps(delivery.payload, sort_keys=True).encode("utf-8")

        # Prepare request headers
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

        # Extract data needed for HTTP call before releasing DB connection
        endpoint_url = endpoint.url

        # Release DB connection before making HTTP call to avoid holding
        # connection during network I/O (can take 100ms-30s)
        await self.db.commit()

        # Attempt delivery with circuit breaker tracking (DB connection released)
        response = None
        http_error: Exception | None = None
        circuit_error: Exception | None = None

        try:
            client = get_webhook_http_client()
            # CRIT-2 FIX: Use content= with pre-serialized bytes instead of json=
            # This ensures the transmitted bytes match what was signed in _generate_signature
            response = await client.post(endpoint_url, content=payload_bytes, headers=headers)
        except CircuitBreakerOpen as e:
            circuit_error = e
        except Exception as e:
            http_error = e

        # Re-fetch delivery to re-attach to session after HTTP call
        delivery = await self.db.get(WebhookDelivery, delivery_id)
        if not delivery:
            raise ModelNotFoundError("WebhookDelivery", delivery_id)

        # Process HTTP result
        if response is not None:
            delivery.attempt_count += 1
            delivery.response_status = response.status_code
            delivery.response_body = response.text[:1000]  # Limit to 1KB

            if 200 <= response.status_code < 300:  # HTTP success range
                # Success - record with circuit breaker
                await webhook_circuit.record_success()
                delivery.status = WebhookDeliveryStatus.DELIVERED
                # Note: delivered_at tracked via updated_at timestamp
                logger.info(f"Webhook delivered successfully: {delivery_id} (attempt {attempt + 1})")
            else:
                # HTTP error - record failure and retry if attempts remain
                await webhook_circuit.record_failure(Exception(f"HTTP {response.status_code}"))
                await self._handle_delivery_failure(delivery, attempt)

        elif circuit_error is not None:
            # Circuit opened during delivery attempt
            logger.warning("Webhook circuit breaker opened: %s", circuit_error)
            delivery.attempt_count += 1
            delivery.response_body = str(circuit_error)[:1000]
            await self._handle_delivery_failure(delivery, attempt)

        elif http_error is not None:
            # Network/timeout error - record failure and retry
            logger.error(f"Webhook delivery failed: {delivery_id} - {http_error!s}")
            await webhook_circuit.record_failure(http_error)
            delivery.attempt_count += 1
            delivery.response_body = str(http_error)[:1000]
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
        tenant_id: int,
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

    async def get_signing_secret(
        self,
        endpoint_id: UUID,
        tenant_id: int,
    ) -> dict[str, Any]:
        """Get the signing secret for a webhook endpoint (admin only).

        Parameters
        ----------
        endpoint_id : UUID
            ID of the webhook endpoint
        tenant_id : int
            ID of the tenant

        Returns
        -------
        dict
            Dictionary with webhook_endpoint_id, signing_secret, and created_at

        Raises
        ------
        ModelNotFoundError
            If endpoint not found
        """
        stmt = select(WebhookEndpoint).where(
            WebhookEndpoint.id == endpoint_id,
            WebhookEndpoint.tenant_id == tenant_id,
        )
        result = await self.db.execute(stmt)
        endpoint = result.scalar_one_or_none()

        if not endpoint:
            raise ModelNotFoundError("WebhookEndpoint", endpoint_id)

        return {
            "webhook_endpoint_id": endpoint.id,
            "signing_secret": endpoint.signing_secret,
            "created_at": endpoint.created_at,
        }

    async def rotate_signing_secret(
        self,
        endpoint_id: UUID,
        tenant_id: int,
        grace_period_seconds: int = 300,
    ) -> dict[str, Any]:
        """Rotate the signing secret for a webhook endpoint.

        Generates a new signing secret that takes effect immediately.
        The old secret is invalidated upon rotation.

        NOTE: The grace_period_seconds parameter is included for future
        grace period support but is not currently implemented. The old
        secret is replaced immediately. Update your webhook consumers
        before calling this endpoint.

        Parameters
        ----------
        endpoint_id : UUID
            ID of the webhook endpoint
        tenant_id : int
            ID of the tenant
        grace_period_seconds : int
            Reserved for future grace period support (default: 300/5min)

        Returns
        -------
        dict
            Dictionary with new_signing_secret and rotation timestamp

        Raises
        ------
        ModelNotFoundError
            If endpoint not found
        """
        from .utils import generate_signing_secret

        stmt = select(WebhookEndpoint).where(
            WebhookEndpoint.id == endpoint_id,
            WebhookEndpoint.tenant_id == tenant_id,
        )
        result = await self.db.execute(stmt)
        endpoint = result.scalar_one_or_none()

        if not endpoint:
            raise ModelNotFoundError("WebhookEndpoint", endpoint_id)

        # Generate new signing secret
        new_secret = generate_signing_secret()

        # Record rotation timestamp
        rotated_at = datetime.now(UTC)

        # Update endpoint with new secret (immediate effect, no grace period)
        endpoint.signing_secret = new_secret

        await self.db.commit()
        await self.db.refresh(endpoint)

        return {
            "webhook_endpoint_id": endpoint.id,
            "new_signing_secret": new_secret,
            "rotated_at": rotated_at,
            "old_secret_expires_at": rotated_at,  # Immediate - no grace period
            "grace_period_seconds": grace_period_seconds,
        }
