"""Billing module specific exceptions.

Provides exception classes for billing and payment error scenarios.

T079: HTTP 402 PaymentError response handling
"""

from __future__ import annotations


class PaymentRequiredError(Exception):
    """Raised when payment is required or payment has failed.

    Used to return HTTP 402 Payment Required responses.

    Attributes:
        message: Human-readable error message.
        error_code: Stripe or internal error code.
        decline_code: Stripe decline code if applicable.
    """

    def __init__(
        self,
        message: str,
        error_code: str = "payment_required",
        decline_code: str | None = None,
    ):
        """Initialize PaymentRequiredError.

        Args:
            message: Description of the payment failure.
            error_code: Error code (e.g., "card_declined", "insufficient_funds").
            decline_code: Stripe decline code if applicable.
        """
        self.message = message
        self.error_code = error_code
        self.decline_code = decline_code
        super().__init__(self.message)


class SubscriptionRequiredError(Exception):
    """Raised when an active subscription is required to access a feature.

    Attributes:
        message: Human-readable error message.
        required_plan: The plan required to access the feature.
    """

    def __init__(self, message: str = "Active subscription required", required_plan: str | None = None):
        """Initialize SubscriptionRequiredError.

        Args:
            message: Description of the subscription requirement.
            required_plan: Name of the required subscription plan.
        """
        self.message = message
        self.required_plan = required_plan
        super().__init__(self.message)


class WebhookVerificationError(Exception):
    """Raised when Stripe webhook payload or signature verification fails."""

    def __init__(self, message: str = "Webhook verification failed"):
        self.message = message
        super().__init__(self.message)


class WebhookNotConfiguredError(Exception):
    """Raised when Stripe webhook secret is not configured in environment."""

    def __init__(self, message: str = "Webhook secret not configured"):
        self.message = message
        super().__init__(self.message)

class StripeError(Exception):
    """Raised when a Stripe API call fails."""

    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


__all__ = [
    "PaymentRequiredError",
    "StripeError",
    "SubscriptionRequiredError",
    "WebhookNotConfiguredError",
    "WebhookVerificationError",
]
