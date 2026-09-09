"""Shared domain exceptions for application-wide use.

Zero-dependency leaf exceptions that can be safely imported by core,
middleware, domain services, repositories, and controllers.
"""

from __future__ import annotations

from typing import Any


class AppException(Exception):
    """Base application exception for all domain errors."""

    def __init__(self, message: str = "An application error occurred"):
        self.message = message
        self.detail = message
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

    Lives in the zero-dependency shared leaf so the outbound clients layer
    (``src/app/clients/``) can raise it without importing feature modules.

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
    "UnauthorizedError",
    "ValidationError",
]
