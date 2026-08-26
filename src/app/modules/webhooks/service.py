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
import inspect
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.core.events import event_bus
from src.app.core.logger import get_logger
from src.app.core.security.encryption import decrypt_data, encrypt_data
from src.app.core.security.url_validator import SSRFProtectionError, validate_webhook_url
from src.app.modules._base import ModelNotFoundError
from src.app.modules.webhooks.http_client import get_webhook_http_client
from src.app.modules.webhooks.models import (
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookEndpoint,
)

logger = get_logger(__name__)

# Pre-compiled glob -> regex cache (H6: avoid O(N) fnmatch per delivery)
import fnmatch as _fnmatch
import re as _re

# W10 FIX: the cache is keyed by tenant-supplied patterns, so it must be
# bounded — an unbounded dict lets tenants grow worker memory indefinitely
# with unique patterns. Simple FIFO eviction at a generous cap.
_FN_REGEX_CACHE: dict[str, _re.Pattern] = {}
_FN_REGEX_CACHE_MAX = 1024


def _glob_match(text: str, pattern: str) -> bool:
    pat = _FN_REGEX_CACHE.get(pattern)
    if pat is None:
        pat = _re.compile(_fnmatch.translate(pattern))
        if len(_FN_REGEX_CACHE) >= _FN_REGEX_CACHE_MAX:
            # Evict oldest entries (insertion order) down to half capacity
            # to amortize the eviction cost.
            for old_key in list(_FN_REGEX_CACHE.keys())[: _FN_REGEX_CACHE_MAX // 2]:
                del _FN_REGEX_CACHE[old_key]
        _FN_REGEX_CACHE[pattern] = pat
    return bool(pat.match(text))


# Retry schedule in seconds: 1m, 5m, 15m, 30m, 1h (5 retries over 1 hour)
RETRY_DELAY_1_MINUTE = 60
RETRY_DELAY_5_MINUTES = 300
RETRY_DELAY_15_MINUTES = 900
RETRY_DELAY_30_MINUTES = 1800
RETRY_DELAY_1_HOUR = 3600

# INFO-W-config FIX: schedule now comes from settings.webhooks.WEBHOOK_RETRY_DELAYS
# (previously this module-level list was hardcoded and the config field was dead).
from src.app.core.config import settings as _settings  # noqa: E402

WEBHOOK_RETRY_DELAYS: list[int] = list(
    getattr(_settings.webhooks, "WEBHOOK_RETRY_DELAYS", None)
    or [RETRY_DELAY_1_MINUTE, RETRY_DELAY_5_MINUTES, RETRY_DELAY_15_MINUTES, RETRY_DELAY_30_MINUTES, RETRY_DELAY_1_HOUR]
)


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
        signing_secret: str | None = None,
        is_active: bool = True,
    ) -> WebhookEndpoint:
        """Create a new webhook endpoint."""
        from src.app.modules.webhooks.utils import validate_url_ssrf

        # SECURITY FIX: Validate SSRF at create time to prevent persisting bad URLs
        await validate_url_ssrf(url)

        # W7 FIX: honor a caller-provided secret; previously this method had
        # no `secret` parameter, so the router's request.secret was silently
        # discarded and a random one was always generated.
        import secrets as secrets_module

        actual_secret = secret if secret else secrets_module.token_urlsafe(32)
        encrypted_secret = encrypt_data(actual_secret)

        # Generate signing_secret if not provided (64 chars = 256 bits)
        actual_signing_secret = signing_secret if signing_secret else secrets_module.token_hex(32)
        encrypted_signing_secret = encrypt_data(actual_signing_secret)

        endpoint = WebhookEndpoint(
            tenant_id=tenant_id,
            url=url,
            event_type=event_type,
            secret=encrypted_secret,
            signing_secret=encrypted_signing_secret,
            is_active=is_active,
        )

        self.db.add(endpoint)
        await self.db.commit()
        await self.db.refresh(endpoint)

        # C2 FIX: Attach plaintext secrets as transient attributes for the creation response
        setattr(endpoint, "_plaintext_secret", actual_secret)
        setattr(endpoint, "_plaintext_signing_secret", actual_signing_secret)

        logger.info("webhook_endpoint_created", tenant_id=tenant_id, event_type=event_type, url=url)
        return endpoint

    async def get_endpoints(
        self,
        tenant_id: UUID,
        is_active: bool | None = None,
        event_type: str | None = None,
        page: int = 1,
        per_page: int = 100,
    ) -> tuple[list[WebhookEndpoint], int]:
        """Get paginated webhook endpoints for a tenant."""
        from sqlalchemy import func, select

        query = select(WebhookEndpoint).where(WebhookEndpoint.tenant_id == tenant_id)

        if is_active is not None:
            query = query.where(WebhookEndpoint.is_active == is_active)
        if event_type:
            query = query.where(WebhookEndpoint.event_type == event_type)

        query = query.order_by(WebhookEndpoint.created_at.desc())

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await self.db.execute(count_query)
        total = total_result.scalar_one()

        # Apply pagination
        offset = (page - 1) * per_page
        query = query.limit(per_page).offset(offset)

        result = await self.db.execute(query)
        endpoints = list(result.scalars().all())

        return endpoints, total

    async def get_endpoint_by_id(self, endpoint_id: UUID, tenant_id: UUID) -> WebhookEndpoint | None:
        """Get a webhook endpoint by ID and tenant ID."""
        stmt = select(WebhookEndpoint).where(
            WebhookEndpoint.id == endpoint_id,
            WebhookEndpoint.tenant_id == tenant_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def update_endpoint(self, endpoint: WebhookEndpoint, update_data: dict[str, Any]) -> WebhookEndpoint:
        """Update a webhook endpoint."""
        from src.app.core.security.encryption import encrypt_data
        from src.app.modules.webhooks.utils import validate_url_ssrf

        for field, value in update_data.items():
            # Handle HttpUrl from Pydantic
            if hasattr(value, "scheme") and hasattr(value, "host"):
                value = str(value)

            if field == "url" and value:
                value = str(value)
                await validate_url_ssrf(value)

            if field in ("secret", "signing_secret") and value is not None:
                value = encrypt_data(value)
            setattr(endpoint, field, value)

        await self.db.commit()
        await self.db.refresh(endpoint)
        return endpoint

    async def delete_endpoint(self, endpoint: WebhookEndpoint) -> None:
        """Delete a webhook endpoint."""
        await self.db.delete(endpoint)
        await self.db.commit()

    async def get_delivery_by_id(self, delivery_id: UUID, tenant_id: UUID) -> WebhookDelivery | None:
        """Get a delivery by ID for a specific tenant."""
        result = await self.db.execute(
            select(WebhookDelivery)
            .join(WebhookEndpoint)
            .where(
                WebhookDelivery.id == delivery_id,
                WebhookEndpoint.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_deliveries(
        self,
        tenant_id: UUID,
        endpoint_id: UUID | None = None,
        delivery_status: WebhookDeliveryStatus | None = None,
        event_type: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        page: int = 1,
        per_page: int = 100,
    ) -> tuple[list[WebhookDelivery], int]:
        """Get paginated webhook deliveries for a tenant."""
        from sqlalchemy import func, select

        query = select(WebhookDelivery).where(WebhookDelivery.tenant_id == tenant_id)

        if endpoint_id:
            query = query.where(WebhookDelivery.webhook_endpoint_id == endpoint_id)
        if delivery_status:
            query = query.where(WebhookDelivery.status == delivery_status)
        if event_type:
            query = query.where(WebhookDelivery.event_type == event_type)
        if start_date:
            query = query.where(WebhookDelivery.created_at >= start_date)
        if end_date:
            query = query.where(WebhookDelivery.created_at <= end_date)

        query = query.order_by(WebhookDelivery.created_at.desc())

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await self.db.execute(count_query)
        total = total_result.scalar_one()

        # Apply pagination
        offset = (page - 1) * per_page
        query = query.limit(per_page).offset(offset)

        result = await self.db.execute(query)
        deliveries = list(result.scalars().all())

        return deliveries, total

    async def get_active_endpoints(self, tenant_id: UUID, event_type: str) -> list[WebhookEndpoint]:
        """Get active endpoints for an event type with glob matching support."""
        stmt = select(WebhookEndpoint).where(
            WebhookEndpoint.tenant_id == tenant_id,
            WebhookEndpoint.is_active.is_(True),
        )
        result = await self.db.execute(stmt)
        all_endpoints = list(result.scalars().all())

        return [ep for ep in all_endpoints if ep.event_type == event_type or _glob_match(event_type, ep.event_type)]

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
        if not endpoints:
            return []

        # Prepare deliveries for bulk insert
        deliveries_data = []

        for endpoint in endpoints:
            # Prefer signing_secret (per-endpoint) over legacy encrypted secret
            secret = ""
            if endpoint.signing_secret:
                try:
                    secret = decrypt_data(endpoint.signing_secret)
                except Exception as e:
                    logger.error(
                        "webhook_signing_secret_decryption_failed",
                        endpoint_id=str(endpoint.id),
                        error=str(e),
                    )
                    continue
            elif endpoint.secret:
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
            if (
                endpoint.old_signing_secret
                and endpoint.old_secret_expires_at
                and isinstance(endpoint.old_secret_expires_at, datetime)
            ):
                now_utc = datetime.now(UTC)
                expires_at = endpoint.old_secret_expires_at
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
                if expires_at > now_utc:
                    try:
                        old_secret = decrypt_data(endpoint.old_signing_secret)
                        active_sig_full = self._generate_signature(payload, secret)
                        old_sig_full = self._generate_signature(payload, old_secret)
                        signature = f"{active_sig_full},{old_sig_full}"
                    except Exception as old_sig_err:
                        logger.error(
                            "webhook_old_signing_secret_decryption_failed",
                            endpoint_id=str(endpoint.id),
                            error=str(old_sig_err),
                        )
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
        # FIX: Log exceptions instead of silently swallowing them
        if deliveries:
            enqueue_results = await asyncio.gather(
                *(self._enqueue_delivery(d.id, tenant_id=d.tenant_id) for d in deliveries),
                return_exceptions=True,
            )
            # Log any exceptions that occurred during enqueue
            for i, enqueue_result in enumerate(enqueue_results):
                if isinstance(enqueue_result, Exception):
                    logger.error(
                        "webhook_enqueue_failed",
                        delivery_id=str(deliveries[i].id),
                        error=str(enqueue_result),
                        error_type=type(enqueue_result).__name__,
                    )

        logger.info("webhooks_triggered", deliveries_count=len(deliveries), event_type=event_type, tenant_id=tenant_id)
        return deliveries

    async def trigger_endpoint(
        self,
        endpoint: WebhookEndpoint,
        event_type: str,
        payload: dict[str, Any],
    ) -> WebhookDelivery:
        """Trigger a specific webhook endpoint (e.g., for testing)."""
        # Prefer signing_secret (per-endpoint) over legacy encrypted secret
        secret = ""
        if endpoint.signing_secret:
            try:
                secret = decrypt_data(endpoint.signing_secret)
            except Exception as e:
                logger.error(
                    "webhook_signing_secret_decryption_failed",
                    endpoint_id=str(endpoint.id),
                    error=str(e),
                )
                raise ValueError(f"Cannot decrypt webhook signing_secret for endpoint {endpoint.id}.") from e
        elif endpoint.secret:
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
                ) from e

        signature = self._generate_signature(payload, secret)
        if (
            endpoint.old_signing_secret
            and endpoint.old_secret_expires_at
            and isinstance(endpoint.old_secret_expires_at, datetime)
        ):
            now_utc = datetime.now(UTC)
            expires_at = endpoint.old_secret_expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at > now_utc:
                try:
                    old_secret = decrypt_data(endpoint.old_signing_secret)
                    active_sig_full = self._generate_signature(payload, secret)
                    old_sig_full = self._generate_signature(payload, old_secret)
                    signature = f"{active_sig_full},{old_sig_full}"
                except Exception as old_sig_err:
                    logger.error(
                        "webhook_old_signing_secret_decryption_failed",
                        endpoint_id=str(endpoint.id),
                        error=str(old_sig_err),
                    )

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

        await self._enqueue_delivery(delivery.id, tenant_id=delivery.tenant_id)

        logger.info("Triggered webhook delivery for endpoint", delivery_id=delivery.id, endpoint_id=endpoint.id)
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

    async def _enqueue_delivery(self, delivery_id: UUID, defer_by: int = 0, tenant_id: UUID | None = None) -> None:
        """Enqueue webhook delivery task.

        Args:
            delivery_id: ID of the delivery record.
            defer_by: Seconds to defer the job.
            tenant_id: Tenant ID passed as job arg to avoid a DB lookup in the worker.
        """
        from src.app.core.utils.queue import pool

        if pool:
            # Pass tenant_id as a job arg so the worker can skip the "lookup" DB session
            args = [delivery_id, str(tenant_id)] if tenant_id else [delivery_id]
            if defer_by > 0:
                enqueue_result = pool.enqueue_job("deliver_webhook_task", *args, _defer_by=defer_by)
            else:
                enqueue_result = pool.enqueue_job("deliver_webhook_task", *args)

            if inspect.isawaitable(enqueue_result):
                await enqueue_result
            else:
                logger.warning(
                    "webhook_queue_enqueue_non_awaitable",
                    delivery_id=str(delivery_id),
                    result_type=type(enqueue_result).__name__,
                )
            logger.debug("Enqueued delivery task for", delivery_id=delivery_id)
        else:
            logger.warning("Redis queue pool not initialized. Cannot enqueue delivery", delivery_id=delivery_id)

    async def deliver_webhook(self, delivery_id: UUID) -> WebhookDelivery:  # noqa: C901
        """Deliver a webhook (called by background task).

        Implements retry logic with exponential backoff and circuit breaker protection.

        CRITICAL: Uses content= instead of json= to prevent httpx double serialization.
        The payload is serialized ONCE with sort_keys=True to match the HMAC signature.
        """
        import json

        # P2.17 FIX: serialize delivery by id to prevent double delivery when the
        # same delivery_id is enqueued twice (e.g. a retry scheduled while the
        # original is still in flight). A short-lived Redis lock ensures only one
        # worker performs the HTTP call; others skip and return the current state.
        from src.app.core.utils import cache as _cache
        from src.app.core.utils.circuit_breaker import CircuitBreakerOpen, webhook_circuit

        _redis = _cache.client
        _lock_key = f"webhook:deliver_lock:{delivery_id}"
        _lock_held = False
        if _redis is not None:
            _lock_held = bool(await _redis.set(_lock_key, "1", nx=True, ex=60))
            if not _lock_held:
                logger.info("Webhook delivery already in progress, skipping", delivery_id=delivery_id)
                existing = await self.db.get(WebhookDelivery, delivery_id)
                if not existing:
                    raise ModelNotFoundError("WebhookDelivery", delivery_id)
                return existing

        # Get delivery record with endpoint eagerly loaded (fixes N+1 query)
        stmt = (
            select(WebhookDelivery)
            .options(selectinload(WebhookDelivery.webhook_endpoint))
            .where(WebhookDelivery.id == delivery_id)
        )
        result = await self.db.execute(stmt)
        delivery = result.unique().scalar_one_or_none()
        if not delivery:
            if _lock_held and _redis is not None:
                try:
                    await _redis.delete(_lock_key)
                except Exception:  # noqa: BLE001
                    pass
            raise ModelNotFoundError("WebhookDelivery", delivery_id)

        # Access eagerly loaded endpoint
        endpoint = delivery.webhook_endpoint
        if not endpoint:
            raise ModelNotFoundError("WebhookEndpoint", delivery.webhook_endpoint_id)

        # Check circuit breaker before attempting delivery
        if not webhook_circuit.is_closed:
            import time

            current_time = time.time()
            # Clamp at 0: a breaker opened without a recorded _last_failure_time
            # (or clock adjustments) otherwise yields a hugely negative window,
            # scheduling next_retry_at in the past.
            recovery_time = max(
                0.0, webhook_circuit.recovery_timeout - (current_time - webhook_circuit._last_failure_time)
            )
            logger.warning(
                "Circuit breaker open for webhooks, skipping delivery %s | recovery_in=%.1fs",
                delivery_id,
                recovery_time,
            )
            # W4 FIX: a circuit-open skip is NOT a delivery attempt. Previously
            # attempt_count was incremented here, so ~6 consecutive open ticks
            # exhausted WEBHOOK_RETRY_DELAYS and pushed the delivery to
            # permanent FAILED with zero HTTP attempts ever made. Now we only
            # defer until the breaker recovers, keeping the retry budget intact.
            recovery_delay = max(1, int(recovery_time))
            delivery.next_retry_at = datetime.now(UTC) + timedelta(seconds=recovery_delay)
            delivery.response_body = f"Circuit breaker open. Skipped. Recovery in {recovery_time:.1f}s"
            await self.db.commit()
            await self.db.refresh(delivery)
            try:
                from src.app.core.utils.background_task import create_safe_task

                create_safe_task(
                    self._enqueue_delivery(delivery_id, defer_by=recovery_delay, tenant_id=delivery.tenant_id)
                )
            except RuntimeError:
                logger.warning("webhook_circuit_skip_reenqueue_failed", delivery_id=str(delivery_id))
            if _lock_held and _redis is not None:
                try:
                    await _redis.delete(_lock_key)
                except Exception:  # noqa: BLE001
                    pass
            return delivery

        # W1+W2+S-4 FIX: serialize and sign AT SEND TIME, per attempt.
        #
        # Previously the signature was computed at trigger time over a
        # pre-persistence dict while transmitted bytes were re-serialized after
        # a JSONB round-trip - any value/type normalization silently broke
        # consumer-side HMAC. The timestamp header came from delivery.created_at,
        # so retries older than the 300s replay tolerance were always rejected.
        #
        # Now: one serialization of stored payload, signed with a fresh timestamp
        # (Stripe-style `t=...,v1=...`), so sign/send share identical bytes and
        # retries carry a valid stamp.
        import time as _time

        send_timestamp = int(_time.time())
        payload_bytes = json.dumps(delivery.payload, sort_keys=True).encode("utf-8")

        def _sign_with_secret(secret: str) -> str:
            signed = f"{send_timestamp}.".encode() + payload_bytes
            digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
            return f"t={send_timestamp},v1={digest}"

        signature_header = _sign_with_secret(decrypt_data(endpoint.secret))
        if (
            endpoint.old_signing_secret
            and endpoint.old_secret_expires_at
            and isinstance(endpoint.old_secret_expires_at, datetime)
        ):
            expires_at = endpoint.old_secret_expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at > datetime.now(UTC):
                try:
                    signature_header += f",{_sign_with_secret(decrypt_data(endpoint.old_signing_secret))}"
                except Exception as old_sig_err:
                    logger.error(
                        "webhook_old_signing_secret_decryption_failed",
                        endpoint_id=str(endpoint.id),
                        error=str(old_sig_err),
                    )

        # Prepare request headers
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Signature": signature_header,
            "X-Webhook-Event": delivery.event_type,
            "X-Webhook-ID": str(delivery.id),
            "X-Webhook-Timestamp": str(send_timestamp),
        }

        # Validate URL for SSRF protection before making request.
        # W8 FIX: the HTTPS scheme check is always enforced; the
        # WEBHOOK_ALLOW_INTERNAL_URLS kill switch only relaxes the
        # private-IP/hostname checks, it no longer disables validation.
        try:
            from src.app.core.config import settings

            validated_ip = await validate_webhook_url(
                endpoint.url,
                allow_internal=settings.app.WEBHOOK_ALLOW_INTERNAL_URLS,
            )  # noqa: E501 - pinned IP, use extensions to prevent DNS rebinding
        except SSRFProtectionError as e:
            logger.error(
                "webhook_ssrf_blocked",
                delivery_id=str(delivery_id),
                endpoint_url=endpoint.url,
                error=str(e),
            )

            from src.app.core.events import EventPriority

            await event_bus.publish(
                "audit.log",
                {
                    "action": "webhook.ssrf_blocked",
                    "tenant_id": str(endpoint.tenant_id),
                    "entity_type": "webhook_delivery",
                    "entity_id": str(delivery_id),
                    "details": {"endpoint_url": endpoint.url, "error": str(e)},
                },
                priority=EventPriority.NORMAL,
            )

            delivery.status = WebhookDeliveryStatus.FAILED
            delivery.response_body = f"SSRF protection: {e}"
            await self.db.commit()
            await self.db.refresh(delivery)
            if _lock_held and _redis is not None:
                try:
                    await _redis.delete(_lock_key)
                except Exception:  # noqa: BLE001
                    pass
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
            client = await get_webhook_http_client()
            # CRIT-2 FIX: Use content= with pre-serialized bytes instead of json=
            # This ensures the transmitted bytes match what was signed in _generate_signature
            response = await client.post(
                endpoint_url,
                content=payload_bytes,
                headers=headers,
                extensions={"resolved_ip": validated_ip},
            )
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
            delivery.response_body = response.text[:10000]  # Limit to 10KB

            if 200 <= response.status_code < 300:  # HTTP success range
                # Success - record with circuit breaker
                await webhook_circuit.record_success()
                delivery.status = WebhookDeliveryStatus.DELIVERED
                # Note: delivered_at tracked via updated_at timestamp
                logger.info(
                    "Webhook delivered successfully: (attempt )",
                    delivery_id=delivery_id,
                    attempt_count=delivery.attempt_count,
                )
            else:
                # HTTP error - record failure and retry if attempts remain
                await webhook_circuit.record_failure(Exception(f"HTTP {response.status_code}"))

                # SECURITY FIX: Do not retry non-transient 4xx errors (e.g. 400, 422)
                if 400 <= response.status_code < 500 and response.status_code not in (408, 429):
                    delivery.status = WebhookDeliveryStatus.FAILED
                    logger.error(
                        "Webhook failed with permanent 4xx error, skipping retries",
                        delivery_id=delivery_id,
                        status_code=response.status_code,
                    )
                else:
                    await self._handle_delivery_failure(delivery)

        elif circuit_error is not None:
            # Circuit opened during delivery attempt
            logger.warning("Webhook circuit breaker opened: %s", circuit_error)
            delivery.attempt_count += 1
            delivery.response_body = str(circuit_error)[:1000]
            await self._handle_delivery_failure(delivery, override_delay=getattr(circuit_error, "recovery_in", None))

        elif http_error is not None:
            # Network/timeout error - record failure and retry
            logger.error("Webhook delivery failed", delivery_id=delivery_id, http_error=http_error)
            await webhook_circuit.record_failure(http_error)
            delivery.attempt_count += 1
            delivery.response_body = str(http_error)[:1000]
            await self._handle_delivery_failure(delivery)

        await self.db.commit()
        await self.db.refresh(delivery)
        if _lock_held and _redis is not None:
            try:
                await _redis.delete(_lock_key)
            except Exception:  # noqa: BLE001
                pass
        return delivery

    async def _handle_delivery_failure(self, delivery: WebhookDelivery, override_delay: float | None = None) -> None:
        """Handle failed delivery with retry logic."""
        attempt_idx = delivery.attempt_count - 1
        if attempt_idx >= 0 and attempt_idx < len(WEBHOOK_RETRY_DELAYS):
            # Schedule retry
            retry_delay = int(override_delay if override_delay is not None else WEBHOOK_RETRY_DELAYS[attempt_idx])
            delivery.next_retry_at = datetime.now(UTC) + timedelta(seconds=retry_delay)
            delivery.status = WebhookDeliveryStatus.PENDING

            # Enqueue retry task. Delay is large enough that commit will finish first.
            # Avoid SQLAlchemy event.listen to prevent memory leak accumulation on rollback.
            delivery_id = delivery.id
            import asyncio

            try:
                from src.app.core.utils.background_task import create_safe_task

                create_safe_task(
                    self._enqueue_delivery(delivery_id, defer_by=retry_delay, tenant_id=delivery.tenant_id)
                )
            except RuntimeError:
                try:
                    loop = asyncio.get_event_loop()
                    asyncio.run_coroutine_threadsafe(
                        self._enqueue_delivery(delivery_id, defer_by=retry_delay, tenant_id=delivery.tenant_id),
                        loop,
                    )
                except Exception as loop_err:
                    logger.error(
                        "webhook_retry_enqueue_failed",
                        delivery_id=str(delivery_id),
                        error=str(loop_err),
                    )

            logger.warning(
                f"Webhook delivery failed, will retry in {retry_delay}s: "
                f"{delivery.id} (attempt {delivery.attempt_count})"
            )
        else:
            # Max retries exhausted
            delivery.status = WebhookDeliveryStatus.FAILED
            logger.error(
                "Webhook delivery failed permanently after attempts:",
                attempt_count=delivery.attempt_count,
                id=delivery.id,
            )

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

    async def get_signing_secret(
        self,
        endpoint_id: UUID,
        tenant_id: UUID,
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

        # Use module-level decrypt_data
        secret = ""
        if endpoint.signing_secret:
            try:
                secret = decrypt_data(endpoint.signing_secret)
            except Exception as e:
                logger.error(
                    "webhook_signing_secret_decryption_failed",
                    endpoint_id=str(endpoint.id),
                    error=str(e),
                )
                # signing_secret is ALWAYS stored encrypted via encrypt_data.
                # On any decryption failure we must NOT fall back to returning the
                # ciphertext blob (it would leak the encrypted secret to the caller).
                # Raise so the caller gets a clean error instead of sensitive data.
                raise ValueError(f"Cannot decrypt webhook signing_secret for endpoint {endpoint.id}.") from e

        if not secret:
            return {
                "webhook_endpoint_id": endpoint.id,
                "signing_secret": "",
                "created_at": endpoint.created_at,
            }

        return {
            "webhook_endpoint_id": endpoint.id,
            "signing_secret": secret,
            "created_at": endpoint.created_at,
        }

    async def rotate_signing_secret(
        self,
        endpoint_id: UUID,
        tenant_id: UUID,
        grace_period_seconds: int = 300,
    ) -> dict[str, Any]:
        """Rotate the signing secret for a webhook endpoint.

        Generates a new signing secret that takes effect immediately. The
        previous secret is preserved in ``old_signing_secret`` and remains
        valid until ``old_secret_expires_at`` (``grace_period_seconds`` after
        rotation), so in-flight deliveries signed with the old secret keep
        working during the grace window. Update your webhook consumers
        before the grace period elapses.

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
        from src.app.modules.webhooks.utils import generate_signing_secret

        stmt = select(WebhookEndpoint).where(
            WebhookEndpoint.id == endpoint_id,
            WebhookEndpoint.tenant_id == tenant_id,
        )
        result = await self.db.execute(stmt)
        endpoint = result.scalar_one_or_none()

        if not endpoint:
            raise ModelNotFoundError("WebhookEndpoint", endpoint_id)

        # Preserve the old signing secret
        endpoint.old_signing_secret = endpoint.signing_secret

        # Generate new signing secret
        new_secret = generate_signing_secret()

        # Record rotation timestamp
        rotated_at = datetime.now(UTC)
        # Calculate expiration with grace period
        expires_at = rotated_at + timedelta(seconds=grace_period_seconds)
        endpoint.old_secret_expires_at = expires_at

        # Update endpoint with new secret
        endpoint.signing_secret = encrypt_data(new_secret)

        await self.db.commit()
        await self.db.refresh(endpoint)

        return {
            "webhook_endpoint_id": endpoint.id,
            "new_signing_secret": new_secret,
            "rotated_at": rotated_at,
            "old_secret_expires_at": expires_at,
            "grace_period_seconds": grace_period_seconds,
        }


# Event handler for webhook.trigger events
async def handle_webhook_trigger_event(data: dict[str, Any]) -> None:
    """Handle webhook trigger events by enqueuing deliveries.

    Args:
        data: Event payload with tenant_id, event_type, and payload.
    """
    tenant_id_str = data.get("tenant_id")
    event_type = data.get("event_type")
    payload = data.get("payload")

    # W9 FIX: defense-in-depth — internal event metadata must never reach
    # the externally signed webhook payload, even if a publisher embeds it
    # inside the inner payload directly.
    if isinstance(payload, dict) and "_event_meta" in payload:
        payload = {k: v for k, v in payload.items() if k != "_event_meta"}

    if not all([tenant_id_str, event_type]) or payload is None:
        logger.warning(
            "webhook_trigger_event_missing_data",
            has_tenant=bool(tenant_id_str),
            has_event_type=bool(event_type),
            has_payload=bool(payload),
        )
        return

    from src.app.core.db.database import async_session_maker

    try:
        tenant_id = UUID(tenant_id_str)
        async with async_session_maker() as db:
            service = WebhookService(db)
            await service.trigger_webhooks(tenant_id, str(event_type), cast(dict[str, Any], payload))
    except Exception as e:
        logger.error(
            "failed_to_process_webhook_trigger",
            tenant_id=tenant_id_str,
            event_type=event_type,
            error=str(e),
        )
        # W6 FIX: swallow-and-return defeated the event bus's arq Retry/DLQ
        # routing - transient DB failures killed the event permanently.
        raise


# R1-H9: Route common business events to the webhook dispatcher.
# MUST be a module-level function (not a closure): the async event path
# enqueues handler.__module__ + handler.__name__ and the ARQ worker
# re-imports it dynamically. A closure fails getattr() in the worker and
# every business-event webhook lands in the DLQ as a "Permanent Error".
async def webhook_event_adapter(data: dict[str, Any]) -> None:
    """Adapt a business event payload to handle_webhook_trigger_event.

    The event type is read from the canonical ``_event_meta.event_name``
    injected by EventBus.publish, so one function serves every registered
    business event.
    """
    meta = data.get("_event_meta") or {}
    event_type = meta.get("event_name")
    if not event_type:
        logger.warning("webhook_event_adapter_missing_event_name")
        return

    # Prefer top-level tenant_id, fall back to canonical publish metadata
    tenant_id = data.get("tenant_id")
    if not tenant_id:
        tenant = meta.get("tenant") or {}
        tenant_id = tenant.get("id")

    # W9 FIX: internal publish metadata (sequence, event ids, tenant record
    # info) must never be forwarded as part of the externally signed webhook
    # payload.
    external_payload = {k: v for k, v in data.items() if k != "_event_meta"}
    if tenant_id:
        await handle_webhook_trigger_event(
            {"tenant_id": str(tenant_id), "event_type": event_type, "payload": external_payload}
        )
