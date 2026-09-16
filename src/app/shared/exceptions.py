"""Shared domain exceptions for application-wide use.

Zero-dependency leaf exceptions that can be safely imported by core,
middleware, domain services, repositories, and controllers.

A-M1 (partial): feature-domain exceptions must NOT live here — this
leaf is shared foundation, not a feature surface. StripeError is the
known deviation: its move to ``modules/billing/exceptions.py`` is
scheduled for the post-merge single-owner pass (its importers span
client + billing layers); until then it stays put and is re-exported
by ``modules/billing/exceptions.py`` and the deprecated
``modules/_base/exceptions.py`` shim.
"""

from __future__ import annotations

from typing import Any


class AppException(Exception):
    """Base application exception for all domain errors."""

    def __init__(self, message: str = "An application error occurred"):
        self.message = message
        self.detail = message
        # Item 7: optional transport headers (e.g. SSO ``Set-Cookie`` hygiene).
        # The central table merges these into the envelope response.
        self.headers: dict[str, str] | None = None
        super().__init__(self.message)


class ModelNotFoundError(AppException):
    """Raised when a model or entity is not found by ID or other criteria.

    Attributes:
        model_name: Name of the model class.
        identifier: The ID or identifier used for lookup.
        message: Human-readable error message.
        detail: Backward-compatibility attribute mirroring message.
    """

    def __init__(self, model_name: str, identifier: Any = None, message: str | None = None):
        self.model_name = model_name
        self.identifier = identifier
        if message:
            self.message = message
        elif identifier is not None:
            self.message = f"{model_name} with id {identifier} not found"
        else:
            self.message = model_name
        self.detail = self.message
        super().__init__(self.message)


class ValidationError(AppException):
    """Raised when domain validation fails.

    Attributes:
        message: Human-readable error message describing the validation failure.
        detail: Backward-compatibility attribute mirroring message.
    """

    def __init__(self, message: str):
        self.message = message
        self.detail = message
        super().__init__(self.message)


class DuplicateError(AppException):
    """Raised when a duplicate record is detected.

    Attributes:
        field: The field name with duplicate value (e.g., "email").
        value: The duplicate value.
        message: Human-readable error message.
        detail: Backward-compatibility attribute mirroring message.
    """

    def __init__(self, field: str, value: Any = None, message: str | None = None):
        self.field = field
        self.value = value
        if message:
            self.message = message
        elif value is not None:
            self.message = f"Duplicate value for {field}: {value}"
        else:
            self.message = f"Duplicate value for {field}"
        self.detail = self.message
        super().__init__(self.message)


class ForbiddenError(AppException):
    """Raised when an action is forbidden due to business rules or permissions.

    Attributes:
        message: Human-readable error message.
        reason: Machine-readable reason code.
        detail: Backward-compatibility attribute mirroring message.
    """

    def __init__(self, message: str = "Forbidden", reason: str = "forbidden"):
        self.message = message
        self.reason = reason
        self.detail = message
        super().__init__(self.message)


class UnauthorizedError(AppException):
    """Raised when authentication fails or credentials are required.

    Attributes:
        message: Human-readable error message.
        reason: Optional machine-readable reason code.
        detail: Backward-compatibility attribute mirroring message.
    """

    def __init__(self, message: str = "Unauthorized", reason: str = "unauthorized"):
        self.message = message
        self.reason = reason
        self.detail = message
        super().__init__(self.message)


class TenantContextError(AppException):
    """Raised when a tenant-scoped operation lacks an active tenant context.

    Canonical home (item 7): previously lived in
    ``core/exceptions/http_exceptions.py`` as an HTTP-shaped
    ``CustomException`` subclass. Services must raise this domain error;
    the central ``EXCEPTION_HANDLERS`` table maps it to 403.

    Attributes:
        message: Human-readable error message.
        reason: Machine-readable reason code.
        detail: Backward-compatibility attribute mirroring message.
    """

    def __init__(
        self,
        message: str = "Tenant context required for tenant-scoped operation",
        reason: str = "tenant_context_error",
    ):
        self.message = message
        self.reason = reason
        self.detail = message
        super().__init__(self.message)


class RateLimitExceededError(AppException):
    """Raised when a rate limit is exceeded.

    Attributes:
        message: Human-readable error message.
        retry_after: Seconds to wait before retrying.
        detail: Backward-compatibility attribute mirroring message.
    """

    def __init__(self, message: str = "Rate limit exceeded", retry_after: int = 60):
        self.message = message
        self.retry_after = retry_after
        self.detail = message
        super().__init__(self.message)


class ServiceUnavailableError(AppException):
    """Raised when an external or downstream service is unavailable.

    Attributes:
        message: Human-readable error message.
        detail: Backward-compatibility attribute mirroring message.
    """

    def __init__(self, message: str = "Service temporarily unavailable"):
        self.message = message
        self.detail = message
        super().__init__(self.message)


class StripeError(AppException):
    """Raised when a Stripe API call fails.

    A-M1 DEBT: this is a billing-domain exception living in the shared
    leaf. Relocation to ``src.app/modules/billing/exceptions.py`` is
    scheduled for the post-merge single-owner pass (importers span
    ``src/app/clients/billing/stripe.py`` and the billing module); it
    stays here for now and is re-exported by billing and the deprecated
    ``_base`` shim so no importer breaks mid-merge.

    Attributes:
        message: Human-readable error message.
        status_code: HTTP status code to surface at the API boundary.
        detail: Backward-compatibility attribute mirroring message.
    """

    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        self.detail = message
        super().__init__(self.message)


__all__ = [
    "AppException",
    "DuplicateError",
    "ForbiddenError",
    "ModelNotFoundError",
    "RateLimitExceededError",
    "ServiceUnavailableError",
    "StripeError",
    "TenantContextError",
    "UnauthorizedError",
    "ValidationError",
]
