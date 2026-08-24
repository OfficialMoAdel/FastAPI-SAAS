"""Stripe Checkout and Portal service layer.

Business logic for creating checkout sessions and customer portal sessions.
"""

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.tenancy.context import get_current_tenant
from src.app.modules._base.exceptions import (
    ModelNotFoundError,
    StripeError,
    ValidationError,
)
from src.app.modules.billing.schemas import CheckoutCreate, CheckoutSession
from src.app.modules.billing.service import StripeSettingsService
from src.app.modules.billing.subscription_service import SubscriptionPlanService

logger = get_logger(__name__)


class CheckoutService:
    """Service for managing Stripe checkout sessions."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_session(self, user_id: int, user_email: str, data: CheckoutCreate) -> CheckoutSession:
        """Create a Stripe Checkout session."""
        tenant = get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("Tenant", "current")

        # Use Platform API Key from Env
        from src.app.modules.billing.gateway import get_payment_gateway

        gateway = get_payment_gateway(self.db)
        api_key = await gateway.get_api_key(tenant.id)
        if not api_key:
            raise StripeError("Stripe API key not configured", status_code=500)

        # Get settings to find Customer ID
        settings_service = StripeSettingsService(self.db)
        stripe_settings = await settings_service.get_settings()

        customer_id = None
        if stripe_settings:
            customer_id = stripe_settings.get("stripe_customer_id")

        # Get Plan
        plan_service = SubscriptionPlanService(self.db)
        plan = await plan_service.get_plan(data.plan_id)
        # Get Price ID based on interval
        price_id = (
            plan["stripe_price_id_monthly"] if data.billing_interval == "monthly" else plan["stripe_price_id_yearly"]
        )

        if not price_id:
            raise ValidationError(f"Plan does not have a {data.billing_interval} price configured")

        try:
            # Create Stripe Session - build kwargs conditionally to satisfy type checker
            session_kwargs: dict[str, Any] = {
                "api_key": api_key,
                "payment_method_types": ["card"],
                "line_items": [{"price": price_id, "quantity": 1}],
                "mode": "subscription",
                "success_url": data.success_url,
                "cancel_url": data.cancel_url,
                "client_reference_id": str(user_id),
                "subscription_data": {
                    "metadata": {"tenant_id": str(tenant.id), "user_id": str(user_id), "plan_id": str(data.plan_id)}
                },
                "metadata": {"tenant_id": str(tenant.id), "user_id": str(user_id), "plan_id": str(data.plan_id)},
            }

            if customer_id:
                session_kwargs["customer"] = customer_id
            else:
                session_kwargs["customer_email"] = user_email

            session = await gateway.create_checkout_session(tenant_id=tenant.id, **session_kwargs)

            checkout_url = session.url
            if not checkout_url:
                raise StripeError("Stripe did not return a checkout URL", status_code=500)

            return CheckoutSession(
                checkout_url=checkout_url,
                session_id=session.id,
                expires_at=datetime.fromtimestamp(session.expires_at, UTC),
            )

        except StripeError:
            raise
        except Exception as e:
            logger.error("Stripe portal error", error=str(e))
            raise StripeError(f"Failed to initialize checkout session: {str(e)}") from e

    async def create_portal_session(self, return_url: str) -> str:
        """Create a Stripe Customer Portal session."""
        tenant = get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("Tenant", "current")

        # Validate return_url to prevent open redirects (M-01)
        if return_url:
            # Allow relative URLs or URLs matching FRONTEND_URL
            is_relative = return_url.startswith("/") and not return_url.startswith("//")

            frontend_url = settings.FRONTEND_URL.rstrip("/") if settings.FRONTEND_URL else ""
            is_allowed_origin = False

            if frontend_url:
                parsed_return = urlparse(return_url)
                parsed_frontend = urlparse(frontend_url)

                # Check if it matches exactly or is a subdomain of frontend_url
                if parsed_return.netloc == parsed_frontend.netloc:
                    is_allowed_origin = True

            if not (is_relative or is_allowed_origin):
                raise ValidationError("Invalid return_url form: Must be relative or match allowed origins.")

            # Stripe requires absolute URLs
            if is_relative:
                if not frontend_url:
                    raise StripeError(
                        "Server misconfiguration: Need FRONTEND_URL to resolve relative return_url.",
                        status_code=500
                    )
                return_url = f"{frontend_url}{return_url}"

        from src.app.modules.billing.gateway import get_payment_gateway

        gateway = get_payment_gateway(self.db)
        api_key = await gateway.get_api_key(tenant.id)
        if not api_key:
            raise StripeError("Stripe API key not configured", status_code=500)

        # Get Customer ID
        settings_service = StripeSettingsService(self.db)
        stripe_settings = await settings_service.get_settings()

        if not stripe_settings or not stripe_settings.get("stripe_customer_id"):
            raise ValidationError("No Stripe customer ID found for this tenant")

        customer_id = stripe_settings["stripe_customer_id"]

        try:
            session = await gateway.create_portal_session(
                customer_id=customer_id, return_url=return_url, tenant_id=tenant.id, api_key=api_key
            )
            return str(session.url)
        except StripeError:
            raise
        except Exception as e:
            logger.error("Stripe portal error", error=str(e))
            raise StripeError("Failed to initialize customer portal.", status_code=400) from e


def get_checkout_service(db: AsyncSession) -> CheckoutService:
    """Get checkout service instance."""
    return CheckoutService(db)
