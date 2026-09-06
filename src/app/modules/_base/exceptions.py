"""Base exceptions re-exported from src.app.shared.exceptions for backward compatibility."""

from __future__ import annotations

from src.app.core.exceptions.http_exceptions import TenantContextError
from src.app.modules.billing.exceptions import StripeError
from src.app.shared.exceptions import (
    AppException,
    DuplicateError,
    ForbiddenError,
    ModelNotFoundError,
    RateLimitExceededError,
    ServiceUnavailableError,
    UnauthorizedError,
    ValidationError,
)

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
