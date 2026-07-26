"""Payment gateway integration layer.

Provides PaymentGateway protocol and StripeGateway implementation with resilience (with_retry).
"""

import asyncio
from typing import Any, Protocol

import stripe
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.utils.r1_error_handling import with_retry
from src.app.modules._base.exceptions import StripeError

logger = get_logger(__name__)


class PaymentGateway(Protocol):
    """Protocol for payment gateway integrations."""

    async def create_checkout_session(self, tenant_id: Any = None, **kwargs: Any) -> Any:
        """Create a checkout session."""
        ...

    async def create_portal_session(
        self, customer_id: str, return_url: str, tenant_id: Any = None, **kwargs: Any
    ) -> Any:
        """Create a customer portal session."""
        ...

    async def modify_subscription(
        self, subscription_id: str, tenant_id: Any = None, **kwargs: Any
    ) -> Any:
        """Modify a subscription."""
        ...

    async def retrieve_subscription(
        self, subscription_id: str, tenant_id: Any = None, **kwargs: Any
    ) -> Any:
        """Retrieve a subscription."""
        ...

    async def retrieve_invoice(
        self, invoice_id: str, tenant_id: Any = None, **kwargs: Any
    ) -> Any:
        """Retrieve an invoice."""
        ...

    async def get_api_key(self, tenant_id: Any = None) -> str | None:
        """Resolve API key for tenant or global config."""
        ...


class StripeGateway:
    """Stripe implementation of PaymentGateway with built-in retry resilience."""

    def __init__(self, db: AsyncSession | None = None):
        self.db = db

    async def get_api_key(self, tenant_id: Any = None) -> str | None:
        """Resolve Stripe API key for a tenant, falling back to global config."""
        api_key = None
        if tenant_id is not None and self.db is not None:
            from src.app.modules.billing.service import StripeSettingsService

            stripe_settings_record = await StripeSettingsService(self.db).get_settings()
            if stripe_settings_record and stripe_settings_record.get("encrypted_secret_key"):
                from src.app.core.security.encryption import CredentialEncryption

                api_key = CredentialEncryption().decrypt_column(stripe_settings_record["encrypted_secret_key"])
        if not api_key:
            import src.app.modules.billing.service as billing_service

            active_settings = getattr(billing_service, "settings", settings)
            secret_key_obj = getattr(
                active_settings, "STRIPE_SECRET_KEY", getattr(settings, "STRIPE_SECRET_KEY", None)
            )
            if secret_key_obj is not None:
                val = (
                    secret_key_obj.get_secret_value()
                    if hasattr(secret_key_obj, "get_secret_value")
                    else getattr(secret_key_obj, "STRIPE_SECRET_KEY", secret_key_obj)
                )
                api_key = str(val) if val is not None else None
        return api_key

    async def _require_api_key(self, tenant_id: Any = None, provided_key: str | None = None) -> str:
        if provided_key:
            return provided_key
        api_key = await self.get_api_key(tenant_id)
        if not api_key:
            raise StripeError("Stripe API key not configured", status_code=500)
        return api_key

    async def create_checkout_session(self, tenant_id: Any = None, **kwargs: Any) -> Any:
        """Create a Stripe Checkout Session with retry resilience."""
        api_key = await self._require_api_key(tenant_id, kwargs.get("api_key"))
        kwargs["api_key"] = api_key
        try:
            return await with_retry(
                fn=lambda: asyncio.to_thread(lambda: stripe.checkout.Session.create(**kwargs)),
                max_attempts=3,
                delay_seconds=0.2,
                exceptions=(stripe.StripeError, Exception),
            )
        except stripe.StripeError as e:
            logger.error("Stripe checkout error", error=str(e))
            raise StripeError(f"Failed to initialize checkout session: {str(e)}") from e

    async def create_portal_session(
        self, customer_id: str, return_url: str, tenant_id: Any = None, **kwargs: Any
    ) -> Any:
        """Create a Stripe Customer Portal Session with retry resilience."""
        api_key = await self._require_api_key(tenant_id, kwargs.get("api_key"))
        try:
            return await with_retry(
                fn=lambda: asyncio.to_thread(
                    lambda: stripe.billing_portal.Session.create(
                        api_key=api_key, customer=customer_id, return_url=return_url
                    )
                ),
                max_attempts=3,
                delay_seconds=0.2,
                exceptions=(stripe.StripeError, Exception),
            )
        except stripe.StripeError as e:
            logger.error("Stripe portal error", error=str(e))
            raise StripeError("Failed to initialize customer portal.", status_code=400) from e

    async def modify_subscription(
        self, subscription_id: str, tenant_id: Any = None, **kwargs: Any
    ) -> Any:
        """Modify a Stripe Subscription with retry resilience."""
        api_key = await self._require_api_key(tenant_id, kwargs.pop("api_key", None))
        try:
            return await with_retry(
                fn=lambda: asyncio.to_thread(
                    lambda: stripe.Subscription.modify(subscription_id, api_key=api_key, **kwargs)
                ),
                max_attempts=3,
                delay_seconds=0.2,
                exceptions=(stripe.StripeError, Exception),
            )
        except stripe.StripeError as e:
            logger.error("Stripe subscription modify error", error=str(e))
            raise StripeError(f"Failed to modify subscription in Stripe: {str(e)}") from e

    async def retrieve_subscription(
        self, subscription_id: str, tenant_id: Any = None, **kwargs: Any
    ) -> Any:
        """Retrieve a Stripe Subscription with retry resilience."""
        api_key = await self._require_api_key(tenant_id, kwargs.pop("api_key", None))
        try:
            return await with_retry(
                fn=lambda: asyncio.to_thread(
                    lambda: stripe.Subscription.retrieve(subscription_id, api_key=api_key, **kwargs)
                ),
                max_attempts=3,
                delay_seconds=0.2,
                exceptions=(stripe.StripeError, Exception),
            )
        except stripe.StripeError as e:
            logger.error("Stripe subscription retrieve error", error=str(e))
            raise StripeError(f"Failed to retrieve subscription from Stripe: {str(e)}") from e

    async def retrieve_invoice(
        self, invoice_id: str, tenant_id: Any = None, **kwargs: Any
    ) -> Any:
        """Retrieve a Stripe Invoice with retry resilience."""
        api_key = await self._require_api_key(tenant_id, kwargs.pop("api_key", None))
        try:
            return await with_retry(
                fn=lambda: asyncio.to_thread(
                    lambda: stripe.Invoice.retrieve(invoice_id, api_key=api_key, **kwargs)
                ),
                max_attempts=3,
                delay_seconds=0.2,
                exceptions=(stripe.StripeError, Exception),
            )
        except stripe.StripeError as e:
            logger.error("Stripe invoice retrieve error", error=str(e))
            raise StripeError(f"Failed to retrieve invoice from Stripe: {str(e)}") from e


def get_payment_gateway(db: AsyncSession | None = None) -> PaymentGateway:
    """Get PaymentGateway instance."""
    return StripeGateway(db)
