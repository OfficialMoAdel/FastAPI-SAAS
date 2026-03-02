"""Stripe configuration router - API compatibility layer for Stripe settings.

Provides /api/v1/configurations/stripe endpoints for frontend compatibility.
Delegates to billing module's StripeSettingsService and webhook handler.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import async_get_db
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.core.utils.cache import async_get_redis
from src.app.modules.billing.schemas import StripeSettingsRead, StripeSettingsReadFull, StripeSettingsUpdate
from src.app.modules.billing.service import get_stripe_settings_service
from src.app.modules.billing.webhook import handle_webhook_event, verify_webhook_signature

router = APIRouter(prefix="/configurations/stripe", tags=["configurations", "stripe"])
logger = logging.getLogger(__name__)


@router.get(
    "",
    response_model=StripeSettingsReadFull,
    dependencies=[Depends(requires_permission(Permission.MANAGE_BILLING))],
)
async def get_stripe_configuration(
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Get Stripe configuration settings (admin only).

    Returns the current Stripe settings for the tenant, including
    customer ID, account ID, and flags for secret key/webhook secret.
    """
    service = get_stripe_settings_service(db)
    settings = await service.get_settings()

    if settings is None:
        return {
            "id": 0,
            "tenant_id": 0,
            "stripe_customer_id": None,
            "stripe_account_id": None,
            "is_active": False,
            "has_secret_key": False,
            "has_webhook_secret": False,
            "created_at": None,
            "updated_at": None,
        }

    return {
        **settings,
        "has_secret_key": bool(settings.get("encrypted_secret_key")),
        "has_webhook_secret": bool(settings.get("webhook_secret")),
    }


@router.post(
    "",
    response_model=StripeSettingsRead,
    dependencies=[Depends(requires_permission(Permission.MANAGE_BILLING))],
)
async def create_or_update_stripe_configuration(
    data: StripeSettingsUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Create or update Stripe configuration settings (admin only).

    This endpoint handles both create and update operations for
    backward compatibility with the old frontend.
    """
    service = get_stripe_settings_service(db)
    return await service.update_settings(data)


@router.post("/webhooks", status_code=status.HTTP_200_OK)
async def handle_stripe_webhooks(
    request: Request,
    background_tasks: BackgroundTasks,
    redis=Depends(async_get_redis),
    stripe_signature: Annotated[str | None, Header(alias="stripe-signature")] = None,
) -> dict:
    """Handle Stripe webhook events.

    Verifies the webhook signature and processes the event in the background.
    Implements idempotency check to prevent duplicate event processing.

    Args:
        request: HTTP request containing the webhook payload
        background_tasks: FastAPI background tasks for async processing
        redis: Redis connection for idempotency check
        stripe_signature: Stripe signature header for verification

    Returns:
        Success response
    """
    import json

    from src.app.core.config import settings as app_settings
    from src.app.modules.billing.crud import crud_stripe_settings

    payload = await request.body()
    sig_header = stripe_signature

    if not sig_header:
        logger.error("Missing Stripe signature header")
        raise HTTPException(status_code=400, detail="Missing signature")

    # M-03: Retrieve tenant specific webhook secret
    webhook_secret = None
    try:
        payload_dict = json.loads(payload)
        stripe_account_id = payload_dict.get("account")

        # AsyncSession dependencies
        from src.app.core.db.database import local_session

        if stripe_account_id:
            logger.info(f"Looking up webhook secret for Stripe account: {stripe_account_id}")
            async with local_session() as db:
                settings = await crud_stripe_settings.get_by_account_id(db, stripe_account_id)
                if settings and settings.webhook_secret:
                    webhook_secret = settings.webhook_secret
    except Exception as e:
        logger.warning(f"Failed to lookup tenant webhook secret: {e}")

    # Fallback to global secret
    if not webhook_secret:
        webhook_secret = (
            app_settings.STRIPE_WEBHOOK_SECRET.get_secret_value()
            if app_settings.STRIPE_WEBHOOK_SECRET else None
        )

    if not webhook_secret:
        logger.error("Stripe webhook secret not configured")
        raise HTTPException(status_code=500, detail="Webhook secret not configured")

    # Verify webhook signature
    try:
        event = verify_webhook_signature(payload, sig_header, webhook_secret)
    except Exception as e:
        logger.error(f"Webhook signature verification failed: {e}")
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Idempotency check
    event_id = event.get("id")
    key = f"stripe_webhook:{event_id}"
    if await redis.exists(key):
        logger.info(f"Duplicate webhook event ignored: {event_id}")
        return {"status": "duplicate"}

    # Mark as processed for 24 hours
    await redis.setex(key, 86400, "1")

    # Handle event in background task
    background_tasks.add_task(handle_webhook_event, event)

    return {"status": "success"}
