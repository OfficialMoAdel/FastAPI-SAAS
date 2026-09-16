"""Stripe Checkout and Portal service layer.

Business logic for creating checkout sessions and customer portal sessions.
"""

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.clients.billing.stripe import PaymentGateway
from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.tenancy.context import require_active_tenant
from src.app.modules.billing.schemas import CheckoutCreate, CheckoutSession
from src.app.modules.billing.service import StripeSettingsService
from src.app.modules.billing.subscription_service import SubscriptionPlanService
from src.app.shared.exceptions import (
    StripeError,
    ValidationError,
)

logger = get_logger(__name__)


class CheckoutService:
    """Service for managing Stripe checkout sessions."""

    def __init__(
        self,
        db: AsyncSession,
        gateway: Any = None,
        gateway_factory: Any = None,
        settings_service: Any = None,
        plan_service: Any = None,
    ):
        self.db = db
        # Item 10: the gateway is built at call time, not held across the
        # commit-before-network boundary. Pass ``gateway`` for a ready-made
        # instance/double, or ``gateway_factory`` (called as
        # ``factory(**kwargs)`` per operation, e.g. api_key pinning on
        # webhook paths). Default: fresh gateway from the current session.
        from src.app.clients.billing.stripe import get_payment_gateway

        if gateway_factory is not None:
            self._gateway_factory = gateway_factory
        elif gateway is not None:
            self._gateway_factory = lambda **kw: gateway
        else:
            self._gateway_factory = lambda **kw: get_payment_gateway(self.db, **kw)
        self.settings_service = settings_service or StripeSettingsService(self.db)
        self.plan_service = plan_service or SubscriptionPlanService(self.db)

    def _gateway(self, **kwargs: Any) -> PaymentGateway:
        """Build the payment gateway for one operation (see __init__)."""
        gateway: PaymentGateway = self._gateway_factory(**kwargs)
        return gateway

    async def resolve_api_key(self, tenant_id: Any) -> str | None:
        """Resolve the Stripe API key for a tenant (per-tenant, else platform).

        Item 10: the ONE key-resolution seam — checkout flows and webhook
        handlers share it instead of each constructing a gateway their own
        way. No DB session is held across the await by callers (webhooks
        resolve inside a short bypass context; request flows use their own
        session, released by commit before any Stripe network call).
        """
        gateway = self._gateway()
        key: str | None = await gateway.get_api_key(tenant_id)
        return key

    async def create_session(self, user_id: int, user_email: str, data: CheckoutCreate) -> CheckoutSession:
        """Create a Stripe Checkout session."""
        tenant = require_active_tenant()
        gateway = self._gateway()

        api_key = await gateway.get_api_key(tenant.id)
        if not api_key:
            raise StripeError("Stripe API key not configured", status_code=500)

        # Get settings to find Customer ID
        stripe_settings = await self.settings_service.get_settings()

        customer_id = None
        if stripe_settings:
            customer_id = stripe_settings.get("stripe_customer_id")

        # Get Plan
        plan = await self.plan_service.get_plan(data.plan_id)
        # Get Price ID based on interval
        price_id = (
            plan["stripe_price_id_monthly"] if data.billing_interval == "monthly" else plan["stripe_price_id_yearly"]
        )

        if not price_id:
            raise ValidationError(f"Plan does not have a {data.billing_interval} price configured")

        # P1-14: release the pooled connection before the Stripe network call
        # below. All inputs (price_id, customer_id, metadata) are materialized
        # into locals above, so no re-fetch is needed after the commit.
        await self.db.commit()

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
            # R2-2: forward a caller-supplied idempotency key when present.
            # The client mints one UUID per checkout intent for double-click /
            # double-POST protection; omitted → gateway's per-attempt key
            # (retry-dedupe only). Deliberately NOT derived server-side from
            # (tenant, plan, interval): Stripe holds keys 24h, so a stable
            # derived key would replay a possibly-expired session — see B16.
            if data.idempotency_key:
                session_kwargs["idempotency_key"] = data.idempotency_key

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
        tenant = require_active_tenant()

        # Validate return_url to prevent open redirects (M-01)
        if return_url:
            # Allow relative URLs or URLs matching FRONTEND_URL
            is_relative = return_url.startswith("/") and not return_url.startswith("//")

            frontend_url = settings.app.FRONTEND_URL.rstrip("/") if settings.app.FRONTEND_URL else ""
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

        gateway = self._gateway()
        api_key = await gateway.get_api_key(tenant.id)
        if not api_key:
            raise StripeError("Stripe API key not configured", status_code=500)

        # Get Customer ID
        settings_service = StripeSettingsService(self.db)
        stripe_settings = await settings_service.get_settings()

        if not stripe_settings or not stripe_settings.get("stripe_customer_id"):
            raise ValidationError("No Stripe customer ID found for this tenant")

        customer_id = stripe_settings["stripe_customer_id"]

        # P1-14: release the pooled connection before the Stripe network call
        # below (same rationale as create_session above).
        await self.db.commit()

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
