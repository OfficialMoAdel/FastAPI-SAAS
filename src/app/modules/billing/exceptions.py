"""Billing module specific exceptions.

Provides exception classes for billing and payment error scenarios.

T079: HTTP 402 PaymentError response handling

Each class declares its own HTTP mapping (``http_status`` / ``response_code``
/ ``log_message``) so ``core.errors`` needs no row for this family and
therefore no import of this module.
"""

from __future__ import annotations

from typing import Any

from src.app.shared.error_codes import ErrorCode
from src.app.shared.exceptions import (
    AppException,
    StripeError,  # noqa: F401  (re-export; canonical home is shared)
)


class PaymentRequiredError(AppException):
    """Raised when payment is required or payment has failed.

    Used to return HTTP 402 Payment Required responses.

    Attributes:
        message: Human-readable error message.
        error_code: Stripe or internal error code. NOTE: this is a *provider*
            code (e.g. "card_declined") surfaced in the envelope's
            ``details["error_code"]`` — it is NOT the envelope ``code``,
            which is ``response_code`` below.
        decline_code: Stripe decline code if applicable.
    """

    http_status = 402
    response_code = ErrorCode.PAYMENT_REQUIRED
    log_message = "Payment required"
    log_error = False

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
        self.detail = message
        super().__init__(self.message)

    def http_details(self) -> dict[str, Any] | None:
        """Surface the provider code, and the decline code when Stripe gave one."""
        details: dict[str, Any] = {"error_code": self.error_code}
        if self.decline_code:
            details["decline_code"] = self.decline_code
        return details


class SubscriptionRequiredError(AppException):
    """Raised when an active subscription is required to access a feature.

    Attributes:
        message: Human-readable error message.
        required_plan: The plan required to access the feature.
    """

    http_status = 402
    response_code = ErrorCode.PAYMENT_REQUIRED
    log_message = "Subscription required"
    log_error = False

    def __init__(self, message: str = "Active subscription required", required_plan: str | None = None):
        """Initialize SubscriptionRequiredError.

        Args:
            message: Description of the subscription requirement.
            required_plan: Name of the required subscription plan.
        """
        self.message = message
        self.required_plan = required_plan
        self.detail = message
        super().__init__(self.message)


class WebhookVerificationError(AppException):
    """Raised when Stripe webhook payload or signature verification fails."""

    http_status = 400
    response_code = ErrorCode.INVALID_INPUT
    log_message = "Webhook verification error"
    log_error = False

    def __init__(self, message: str = "Webhook verification failed"):
        self.message = message
        self.detail = message
        super().__init__(self.message)


class WebhookNotConfiguredError(AppException):
    """Raised when Stripe webhook secret is not configured in environment."""

    http_status = 501
    response_code = ErrorCode.NOT_IMPLEMENTED
    log_message = "Webhook secret not configured"
    log_error = True

    def __init__(self, message: str = "Webhook secret not configured"):
        self.message = message
        self.detail = message
        super().__init__(self.message)

__all__ = [
    "PaymentRequiredError",
    "StripeError",
    "SubscriptionRequiredError",
    "WebhookNotConfiguredError",
    "WebhookVerificationError",
]
