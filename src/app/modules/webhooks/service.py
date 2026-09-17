"""Webhook Service - Event subscription and delivery.

This module provides:
- Webhook endpoint management
- Event delivery with retry logic
- HMAC-SHA256 signature generation
- Delivery status tracking
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.tenant_crud import tenant_scope
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.principal import AuthenticatedUser
from src.app.core.security.encryption import decrypt_data, encrypt_data
from src.app.modules._base import ModelNotFoundError
from src.app.modules.webhooks import delivery as delivery_module
from src.app.modules.webhooks.delivery import (
    RETRY_DELAY_1_HOUR as RETRY_DELAY_1_HOUR,
)
from src.app.modules.webhooks.delivery import (
    RETRY_DELAY_1_MINUTE as RETRY_DELAY_1_MINUTE,
)
from src.app.modules.webhooks.delivery import (
    RETRY_DELAY_5_MINUTES as RETRY_DELAY_5_MINUTES,
)
from src.app.modules.webhooks.delivery import (
    RETRY_DELAY_15_MINUTES as RETRY_DELAY_15_MINUTES,
)
from src.app.modules.webhooks.delivery import (
    RETRY_DELAY_30_MINUTES as RETRY_DELAY_30_MINUTES,
)
from src.app.modules.webhooks.delivery import (
    WEBHOOK_RETRY_DELAYS as WEBHOOK_RETRY_DELAYS,
)
from src.app.modules.webhooks.models import (
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookEndpoint,
)

# Fix #6: the retry schedule canonically lives in delivery.py (the single
# place that decides DEAD vs PENDING). These names are re-exported here for
# backward compatibility with existing importers.
__all__ = [
    "RETRY_DELAY_1_HOUR",
    "RETRY_DELAY_1_MINUTE",
    "RETRY_DELAY_5_MINUTES",
    "RETRY_DELAY_15_MINUTES",
    "RETRY_DELAY_30_MINUTES",
    "WEBHOOK_RETRY_DELAYS",
    "WebhookService",
    "handle_webhook_trigger_event",
    "webhook_event_adapter",
]

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


def _serialize_endpoint(ep: WebhookEndpoint) -> dict[str, Any]:
    secret_hex = None
    secret = getattr(ep, "secret", None)
    if secret:
        secret_hex = secret.hex() if isinstance(secret, bytes | bytearray) else str(secret)
    old_expires = getattr(ep, "old_secret_expires_at", None)
    return {
        "id": str(ep.id),
        "tenant_id": str(ep.tenant_id),
        "url": str(ep.url),
        "event_type": ep.event_type,
        "secret": secret_hex,
        "signing_secret": getattr(ep, "signing_secret", None),
        "is_active": getattr(ep, "is_active", True),
        "old_signing_secret": getattr(ep, "old_signing_secret", None),
        "old_secret_expires_at": old_expires.isoformat() if old_expires else None,
        "description": getattr(ep, "description", None),
    }


def _deserialize_endpoint(data: dict[str, Any]) -> WebhookEndpoint:
    secret_val = data.get("secret")
    if isinstance(secret_val, bytes):
        secret_str = secret_val.decode("utf-8", errors="replace")
    elif secret_val is not None:
        secret_str = str(secret_val)
    else:
        secret_str = ""
    ep = WebhookEndpoint(
        tenant_id=UUID(data["tenant_id"]),
        url=data["url"],
        event_type=data["event_type"],
        secret=secret_str,
        signing_secret=data.get("signing_secret"),
        is_active=data.get("is_active", True),
    )
    if data.get("old_signing_secret"):
        ep.old_signing_secret = data["old_signing_secret"]
    if data.get("old_secret_expires_at"):
        ep.old_secret_expires_at = datetime.fromisoformat(data["old_secret_expires_at"])
    return ep


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
        await self._invalidate_endpoints_cache(tenant_id)
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

        total_col = func.count().over().label("total")
        query = select(WebhookEndpoint, total_col).where(tenant_scope(WebhookEndpoint, tenant_id))

        if is_active is not None:
            query = query.where(WebhookEndpoint.is_active == is_active)
        if event_type:
            query = query.where(WebhookEndpoint.event_type == event_type)

        offset = (page - 1) * per_page
        query = query.order_by(WebhookEndpoint.created_at.desc()).limit(per_page).offset(offset)

        result = await self.db.execute(query)
        rows = result.unique().all()
        if rows:
            total = int(rows[0][1])
            endpoints = [row[0] for row in rows]
            return endpoints, total

        if offset == 0:
            return [], 0

        # Out-of-range page fallback
        count_query = select(func.count()).select_from(WebhookEndpoint).where(tenant_scope(WebhookEndpoint, tenant_id))
        if is_active is not None:
            count_query = count_query.where(WebhookEndpoint.is_active == is_active)
        if event_type:
            count_query = count_query.where(WebhookEndpoint.event_type == event_type)
        total_result = await self.db.execute(count_query)
        return [], total_result.scalar_one()

    async def get_endpoint_by_id(self, endpoint_id: UUID, tenant_id: UUID) -> WebhookEndpoint | None:
        """Get a webhook endpoint by ID and tenant ID."""
        stmt = select(WebhookEndpoint).where(
            WebhookEndpoint.id == endpoint_id,
            tenant_scope(WebhookEndpoint, tenant_id),
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
        await self._invalidate_endpoints_cache(endpoint.tenant_id)
        return endpoint

    async def delete_endpoint(self, endpoint: WebhookEndpoint) -> None:
        """Delete a webhook endpoint."""
        tenant_id = endpoint.tenant_id
        await self.db.delete(endpoint)
        await self.db.commit()
        await self._invalidate_endpoints_cache(tenant_id)

    async def get_delivery_by_id(self, delivery_id: UUID, tenant_id: UUID) -> WebhookDelivery | None:
        """Get a delivery by ID for a specific tenant."""
        result = await self.db.execute(
            select(WebhookDelivery)
            .join(WebhookEndpoint)
            .where(
                WebhookDelivery.id == delivery_id,
                tenant_scope(WebhookEndpoint, tenant_id),
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

        total_col = func.count().over().label("total")
        query = select(WebhookDelivery, total_col).where(tenant_scope(WebhookDelivery, tenant_id))

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

        offset = (page - 1) * per_page
        query = query.order_by(WebhookDelivery.created_at.desc()).limit(per_page).offset(offset)

        result = await self.db.execute(query)
        rows = result.unique().all()
        if rows:
            total = int(rows[0][1])
            deliveries = [row[0] for row in rows]
            return deliveries, total

        if offset == 0:
            return [], 0

        # Out-of-range page fallback
        count_query = select(func.count()).select_from(WebhookDelivery).where(tenant_scope(WebhookDelivery, tenant_id))
        if endpoint_id:
            count_query = count_query.where(WebhookDelivery.webhook_endpoint_id == endpoint_id)
        if delivery_status:
            count_query = count_query.where(WebhookDelivery.status == delivery_status)
        if event_type:
            count_query = count_query.where(WebhookDelivery.event_type == event_type)
        if start_date:
            count_query = count_query.where(WebhookDelivery.created_at >= start_date)
        if end_date:
            count_query = count_query.where(WebhookDelivery.created_at <= end_date)
        total_result = await self.db.execute(count_query)
        return [], total_result.scalar_one()

    async def _invalidate_endpoints_cache(self, tenant_id: UUID | str) -> None:
        """Invalidate active endpoints Redis cache for a tenant (P2-4)."""
        from src.app.core.redis_provider import get_redis

        redis_client = get_redis()
        if redis_client is None:
            return
        try:
            index_key = f"webhook:tenant_endpoints:{tenant_id}"
            keys_to_delete = await redis_client.smembers(index_key)  # type: ignore[misc]
            keys_list = [k.decode("utf-8") if isinstance(k, bytes) else k for k in (keys_to_delete or set())]
            keys_list.append(index_key)
            async with redis_client.pipeline(transaction=False) as pipe:
                for k in keys_list:
                    pipe.delete(k)
                await pipe.execute()
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to invalidate webhook endpoints cache", tenant_id=str(tenant_id), error=str(e))

    async def get_active_endpoints(self, tenant_id: UUID, event_type: str) -> list[WebhookEndpoint]:
        """Get active endpoints for an event type with glob matching support and Redis caching (P2-4)."""
        from src.app.core.redis_provider import get_redis

        redis_client = get_redis()
        cache_key = f"webhook:active_endpoints:{tenant_id}:{event_type}"
        index_key = f"webhook:tenant_endpoints:{tenant_id}"

        if redis_client is not None:
            try:
                cached = await redis_client.get(cache_key)
                if cached is not None:
                    raw_list = json_loads(cached)
                    endpoints = [_deserialize_endpoint(item) for item in raw_list]
                    return endpoints
            except Exception as e:
                logger.warning("Failed to read active endpoints from Redis cache", error=str(e))

        stmt = select(WebhookEndpoint).where(
            tenant_scope(WebhookEndpoint, tenant_id),
            WebhookEndpoint.is_active.is_(True),
            or_(
                WebhookEndpoint.event_type == event_type,
                WebhookEndpoint.event_type.like("%*%"),
                WebhookEndpoint.event_type.like("%?%"),
                WebhookEndpoint.event_type.like("%[%"),
            ),
        )
        result = await self.db.execute(stmt)
        candidates = list(result.scalars().all())
        matching = [ep for ep in candidates if ep.event_type == event_type or _glob_match(event_type, ep.event_type)]

        if redis_client is not None:
            try:
                serialized = json_dumps([_serialize_endpoint(ep) for ep in matching], as_str=True)
                async with redis_client.pipeline(transaction=False) as pipe:
                    pipe.setex(cache_key, 300, serialized)
                    pipe.sadd(index_key, cache_key)
                    pipe.expire(index_key, 600)
                    await pipe.execute()
            except Exception as e:
                logger.warning("Failed to cache active webhook endpoints in Redis", error=str(e))

        return matching

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

        # Prepare deliveries for bulk insert.
        # NOTE: the signature is computed at SEND time per attempt in
        # delivery.py (Stripe-style `t=,v1=` over the canonical bytes) and
        # persisted rows intentionally leave `signature` unset (NULL) — the
        # column is a deprecated trigger-time artifact, not the wire value.
        deliveries_data = []

        for endpoint in endpoints:
            now = datetime.now(UTC)

            deliveries_data.append(
                {
                    "id": uuid4(),
                    "webhook_endpoint_id": endpoint.id,
                    "tenant_id": endpoint.tenant_id,
                    "event_type": event_type,
                    "payload": payload,
                    "signature": None,
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
                *(delivery_module.enqueue(d.id, tenant_id=d.tenant_id) for d in deliveries),
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
        """Trigger a specific webhook endpoint (e.g., for testing).

        The delivery row is persisted with `signature` unset (NULL); the wire
        signature is computed at send time in delivery.py.
        """
        delivery = WebhookDelivery(
            webhook_endpoint_id=endpoint.id,
            tenant_id=endpoint.tenant_id,
            event_type=event_type,
            payload=payload,
            signature=None,
            status=WebhookDeliveryStatus.PENDING,
            attempt_count=0,
        )

        self.db.add(delivery)
        await self.db.commit()
        await self._enqueue_delivery(delivery.id, tenant_id=delivery.tenant_id)

        logger.info("Triggered webhook delivery for endpoint", delivery_id=delivery.id, endpoint_id=endpoint.id)
        return delivery

    async def check_test_rate_limit(self, tenant_id: UUID, current_user: AuthenticatedUser) -> None:
        """Enforce the per-tier rate limit for endpoint test calls.

        Q-M1: raises the domain RateLimitExceededError (moved out of the
        router in architecture candidate 8) — the tier-policy lookup,
        fallback defaults, and limiter call are service logic, not
        presentation. The central handler maps the domain error to 429.

        The check happens BEFORE endpoint lookup intentionally — it
        prevents timing-based endpoint enumeration (404s still consume
        quota).
        """
        from src.app.core.utils.path_utils import sanitize_path
        from src.app.core.utils.rate_limit import rate_limiter
        from src.app.modules.tiers.entitlement import resolve_path_limit
        from src.app.shared.exceptions import RateLimitExceededError

        tier = current_user.tier
        tier_id = current_user.tier_id
        if tier_id is None and isinstance(tier, dict):
            tier_id = tier.get("id")

        rate_limit_path = sanitize_path("/api/v1/webhooks/{endpoint_id}/test")
        # A5: tier-indexed lookup + entitlement default live behind the one
        # entitlement seam (same row query and 10/60 fallback as before).
        limit, period = await resolve_path_limit(self.db, tier_id, rate_limit_path)

        is_limited, _ = await rate_limiter.check_rate_limit(f"webhook:test:{tenant_id}", limit, period)
        if is_limited:
            raise RateLimitExceededError(f"Rate limit exceeded: {limit} tests per {period} seconds", retry_after=period)

    # ------------------------------------------------------------------
    # Delivery lifecycles live in webhooks/delivery.py (enqueue/deliver/
    # recover). These thin delegates keep the historical service surface.
    # ------------------------------------------------------------------

    async def deliver_webhook(self, delivery_id: UUID, endpoint: WebhookEndpoint | None = None) -> WebhookDelivery:
        """Deliver a webhook (delegates to the delivery module's retry loop)."""
        return await delivery_module.deliver(self.db, delivery_id, endpoint=endpoint)

    async def _enqueue_delivery(self, delivery_id: UUID, defer_by: int = 0, tenant_id: UUID | None = None) -> None:
        """Enqueue webhook delivery task (delegates to the delivery module)."""
        await delivery_module.enqueue(delivery_id, defer_by=defer_by, tenant_id=tenant_id)

    async def retry_delivery(self, delivery_id: UUID) -> WebhookDelivery:
        """Retry a failed delivery."""
        delivery = await self.db.get(WebhookDelivery, delivery_id)
        if not delivery:
            raise ModelNotFoundError("WebhookDelivery", delivery_id)

        delivery.status = WebhookDeliveryStatus.PENDING
        delivery.attempt_count = 0
        delivery.next_retry_at = None

        await self.db.commit()
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
            .where(tenant_scope(WebhookEndpoint, tenant_id))
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
            tenant_scope(WebhookEndpoint, tenant_id),
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
            tenant_scope(WebhookEndpoint, tenant_id),
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
        await self._invalidate_endpoints_cache(endpoint.tenant_id)
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
    # R4-04: strip internal publish metadata and tenant-internal data.
    # Only _event_meta, tenant/tenant_id are dropped; the rest is business
    # payload. An explicit allowlist per event type would be stricter — this
    # denylist is the safe incremental fix.
    external_payload = {
        k: v for k, v in data.items() if k not in ("_event_meta", "tenant_id", "tenant") and not k.startswith("_")
    }
    if tenant_id:
        await handle_webhook_trigger_event(
            {"tenant_id": str(tenant_id), "event_type": event_type, "payload": external_payload}
        )
